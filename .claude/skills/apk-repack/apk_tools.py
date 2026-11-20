#!/usr/bin/env python3
"""
APK rebuild + resign CLI tool (the single, deterministic signing path for apk-repack).
Pipeline: apktool b -> zipalign (page-align) -> apksigner sign -> apksigner verify.

Unpacking is NOT handled here — run `apktool d <apk> -o <dir> -f` directly for that.
Resigning MUST go through this script so the strict v2/v3 order is never reordered by hand.
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KEYSTORE_DIR = WORKSPACE_ROOT / ".keystore"
DEFAULT_KEYSTORE = DEFAULT_KEYSTORE_DIR / "debug.keystore"


def find_tool(name: str) -> str | None:
    """Find a binary on PATH, Homebrew, or Android SDK build-tools."""
    # 1. Direct PATH
    path = shutil.which(name)
    if path:
        return path

    # 2. Check Homebrew standard paths
    for brew_path in [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]:
        if os.path.isfile(brew_path) and os.access(brew_path, os.X_OK):
            return brew_path

    # 3. Check Android SDK build-tools for zipalign / apksigner
    sdk_roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Library/Android/sdk"),
        str(Path.home() / "Android/Sdk"),
    ]
    for root in sdk_roots:
        if not root:
            continue
        build_tools_pattern = os.path.join(root, "build-tools", "*", name)
        matches = sorted(glob.glob(build_tools_pattern), reverse=True)
        if matches and os.access(matches[0], os.X_OK):
            return matches[0]

    return None


def ensure_keystore(custom_path: str | None = None) -> str:
    """Ensure a debug keystore exists; create one if missing."""
    if custom_path:
        keystore_path = Path(custom_path).resolve()
        if not keystore_path.exists():
            sys.exit(f"[!] Error: Specified keystore does not exist: {keystore_path}")
        return str(keystore_path)

    # Check ~/.android/debug.keystore
    user_debug_ks = Path.home() / ".android" / "debug.keystore"
    if user_debug_ks.exists():
        return str(user_debug_ks)

    # Check workspace keystore
    if DEFAULT_KEYSTORE.exists():
        return str(DEFAULT_KEYSTORE)

    # Auto-generate debug keystore
    keytool_bin = find_tool("keytool")
    if not keytool_bin:
        sys.exit("[!] Error: 'keytool' not found. Install JDK to generate debug keystore.")

    DEFAULT_KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        keytool_bin,
        "-genkey",
        "-v",
        "-keystore",
        str(DEFAULT_KEYSTORE),
        "-storepass",
        "android",
        "-alias",
        "androiddebugkey",
        "-keypass",
        "android",
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "10000",
        "-dname",
        "CN=Android Debug,O=Android,C=US",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"[!] Failed to generate debug keystore:\n{proc.stderr.strip()}")

    return str(DEFAULT_KEYSTORE)


def cmd_check() -> None:
    """Check tool availability."""
    tools = {
        "java": find_tool("java"),
        "keytool": find_tool("keytool"),
        "apktool": find_tool("apktool"),
        "zipalign": find_tool("zipalign"),
        "apksigner": find_tool("apksigner"),
    }
    all_present = all(tools.values())
    result = {
        "tools": tools,
        "all_present": all_present,
    }
    print(json.dumps(result, indent=2))


def _run_zipalign(zipalign_bin: str, page_size_kb: int, src: Path, dst: Path) -> int:
    """Zipalign with the requested .so page size (16 KB for Android 15+, else 4 KB).

    `-P <kb>` (build-tools 35+) sets the uncompressed-.so page alignment; older zipalign
    only knows `-p` (4 KB), so fall back to it and report the downgrade. The zip entry
    alignment stays 4 bytes either way.
    """
    dst.unlink(missing_ok=True)
    proc = subprocess.run(
        [zipalign_bin, "-P", str(page_size_kb), "-f", "4", str(src), str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return page_size_kb
    # Old zipalign without -P: fall back to legacy 4 KB page alignment.
    dst.unlink(missing_ok=True)
    proc = subprocess.run(
        [zipalign_bin, "-p", "-f", "4", str(src), str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"[!] zipalign failed:\n{proc.stderr.strip() or proc.stdout.strip()}")
    if page_size_kb != 4:
        print(f"[!] Warning: this zipalign lacks '-P'; used legacy 4 KB page alignment "
              f"instead of {page_size_kb} KB. Upgrade Android SDK build-tools to >=35 for 16 KB.",
              file=sys.stderr)
    return 4


def _resolve_signing(
    keystore_path: str | None,
    ks_pass: str | None,
    key_pass: str | None,
    ks_pass_file: str | None = None,
    key_pass_file: str | None = None,
) -> tuple[str, str, str, str, dict[str, str]]:
    """Return (mode, keystore_file, ks_pass_spec, key_pass_spec, env_extra).

    The *_spec values are apksigner password specs — 'env:VAR' or 'file:/abs/path'.
    Release passwords are NEVER placed on argv.

    Release password delivery, in priority order:
      1. --ks-pass-file PATH : a file the USER created; apksigner reads it directly via
         'file:'. The agent never sees, echoes, or persists the value — this is the
         agent-driven path (user supplies the path only).
      2. interactive getpass : when a human runs this at a real terminal.
      3. --ks-pass VALUE     : unattended CI only.
    Debug (no --keystore): auto debug keystore, well-known password 'android'.
    """
    ks_file = ensure_keystore(keystore_path)
    env: dict[str, str] = {}

    if keystore_path is None:
        env["AKS_KS_PASS"] = ks_pass or "android"
        env["AKS_KEY_PASS"] = key_pass or env["AKS_KS_PASS"]
        return "debug", ks_file, "env:AKS_KS_PASS", "env:AKS_KEY_PASS", env

    # --- Release ---
    # 1) File-based (preferred): the agent never reads the secret.
    if ks_pass_file:
        ksf = Path(ks_pass_file).expanduser()
        if not ksf.is_file():
            sys.exit(f"[!] --ks-pass-file not found: {ksf}")
        ks_spec = f"file:{ksf.resolve()}"
        if key_pass_file:
            kf = Path(key_pass_file).expanduser()
            if not kf.is_file():
                sys.exit(f"[!] --key-pass-file not found: {kf}")
            key_spec = f"file:{kf.resolve()}"
        else:
            key_spec = ks_spec
        return "release", ks_file, ks_spec, key_spec, env

    # 2) Interactive hidden prompt (a person at a terminal).
    if ks_pass is None:
        if not sys.stdin.isatty():
            sys.exit(
                "[!] Release signing needs the keystore password. Give it as a file so it\n"
                "    never touches the command line or the chat transcript:\n"
                "      printf %s 'YOUR_PASSWORD' > key.pw   # then: --ks-pass-file key.pw\n"
                "    apksigner reads the file directly. (At a real terminal you'd be prompted\n"
                "    instead; --ks-pass is for unattended CI only.)"
            )
        store_pw = getpass.getpass(f"Release keystore password for {ks_file}: ")
        if not store_pw:
            sys.exit("[!] Empty release keystore password; aborting.")
        entered = getpass.getpass("Key password (press Enter if same as keystore): ")
        env["AKS_KS_PASS"] = store_pw
        env["AKS_KEY_PASS"] = entered or store_pw
        return "release", ks_file, "env:AKS_KS_PASS", "env:AKS_KEY_PASS", env

    # 3) Explicit value (unattended CI only).
    env["AKS_KS_PASS"] = ks_pass
    env["AKS_KEY_PASS"] = key_pass or ks_pass
    return "release", ks_file, "env:AKS_KS_PASS", "env:AKS_KEY_PASS", env


def cmd_repack(
    decompiled_dir: str,
    out_apk_path: str,
    keystore_path: str | None = None,
    ks_pass: str | None = None,
    key_pass: str | None = None,
    key_alias: str = "androiddebugkey",
    page_size_kb: int = 16,
    ks_pass_file: str | None = None,
    key_pass_file: str | None = None,
) -> None:
    """Rebuild, page-align, sign, and verify an APK."""
    # 1. Verify required binaries
    apktool_bin = find_tool("apktool")
    zipalign_bin = find_tool("zipalign")
    apksigner_bin = find_tool("apksigner")

    missing = []
    if not apktool_bin:
        missing.append("apktool")
    if not zipalign_bin:
        missing.append("zipalign")
    if not apksigner_bin:
        missing.append("apksigner")

    if missing:
        sys.exit(f"[!] Error: Missing required tools: {', '.join(missing)}.\nEnsure Android SDK build-tools and apktool are installed.")

    src_dir = Path(decompiled_dir).resolve()
    if not src_dir.exists() or not src_dir.is_dir():
        sys.exit(f"[!] Error: Source directory does not exist: {src_dir}")

    out_apk = Path(out_apk_path).resolve()
    out_apk.parent.mkdir(parents=True, exist_ok=True)

    # Choose debug vs release and resolve how apksigner will read the password
    # (env:VAR for debug/CI/interactive, file:/path for a user-supplied release file).
    signing_mode, ks_file, ks_pass_spec, key_pass_spec, sign_env_extra = _resolve_signing(
        keystore_path, ks_pass, key_pass, ks_pass_file, key_pass_file
    )

    temp_unsigned = out_apk.parent / f"{out_apk.stem}.unsigned.tmp.apk"
    temp_aligned = out_apk.parent / f"{out_apk.stem}.aligned.tmp.apk"

    try:
        # Step A: Build with apktool
        build_cmd = [apktool_bin, "b", str(src_dir), "-o", str(temp_unsigned), "--use-aapt2"]
        proc_build = subprocess.run(build_cmd, capture_output=True, text=True)
        if proc_build.returncode != 0:
            # Fallback without --use-aapt2 if unsupported
            proc_build = subprocess.run([apktool_bin, "b", str(src_dir), "-o", str(temp_unsigned)], capture_output=True, text=True)
            if proc_build.returncode != 0:
                sys.exit(f"[!] apktool build failed:\n{proc_build.stderr.strip() or proc_build.stdout.strip()}")

        # Step B: zipalign (Must precede apksigner for v2/v3 signing)
        applied_page_kb = _run_zipalign(zipalign_bin, page_size_kb, temp_unsigned, temp_aligned)

        # Step C: apksigner sign.
        # The password reaches apksigner only via env:VAR or file:/path (set in
        # _resolve_signing) — never on argv, so it can't leak into the process list.
        sign_env = os.environ.copy()
        sign_env.update(sign_env_extra)
        sign_cmd = [
            apksigner_bin,
            "sign",
            "--ks",
            ks_file,
            "--ks-pass",
            ks_pass_spec,
            "--key-pass",
            key_pass_spec,
            "--ks-key-alias",
            key_alias,
            "--out",
            str(out_apk),
            str(temp_aligned),
        ]
        proc_sign = subprocess.run(sign_cmd, capture_output=True, text=True, env=sign_env)
        if proc_sign.returncode != 0:
            sys.exit(f"[!] apksigner sign failed:\n{proc_sign.stderr.strip() or proc_sign.stdout.strip()}")

        # Step D: apksigner verify
        verify_cmd = [apksigner_bin, "verify", "--verbose", str(out_apk)]
        proc_verify = subprocess.run(verify_cmd, capture_output=True, text=True)
        verified = proc_verify.returncode == 0

        print(json.dumps({
            "status": "success",
            "action": "repack_and_sign",
            "signing": signing_mode,
            "source_dir": str(src_dir),
            "output_apk": str(out_apk),
            "keystore": ks_file,
            "page_size_kb": applied_page_kb,
            "verified": verified,
        }, indent=2))

    finally:
        # Cleanup temp artifacts
        temp_unsigned.unlink(missing_ok=True)
        temp_aligned.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="APK Rebuild, Align & Resign Tool (unpack with `apktool d` directly)")
    sub = parser.add_subparsers(dest="action", required=True)

    # check
    sub.add_parser("check", help="Check availability of apktool, zipalign, apksigner, keytool, and java")

    # repack (the only resigning path)
    p_rep = sub.add_parser("repack", help="Rebuild, align, sign, and verify an APK from a decompiled directory")
    p_rep.add_argument("dir", help="Decompiled directory to rebuild")
    p_rep.add_argument("-o", "--output", required=True, dest="output", help="Output signed APK path")
    p_rep.add_argument("--keystore", help="Release keystore path. Omit for a debug signature (auto-generated debug.keystore).")
    p_rep.add_argument("--ks-pass-file", help="Path to a file containing the keystore password (release). apksigner reads it directly; the value never appears on argv or in the transcript. Preferred for release.")
    p_rep.add_argument("--key-pass-file", help="Path to a file containing the key password (defaults to the keystore password file).")
    p_rep.add_argument("--ks-pass", default=None, help="Keystore password VALUE. UNATTENDED/CI ONLY — prefer --ks-pass-file; debug uses 'android'.")
    p_rep.add_argument("--key-pass", default=None, help="Key password VALUE. UNATTENDED/CI ONLY (defaults to the keystore password).")
    p_rep.add_argument("--alias", default="androiddebugkey", help="Key alias (default: androiddebugkey; set your release alias with --keystore)")
    p_rep.add_argument("--page-size", type=int, choices=[4, 16], default=16,
                       help="Uncompressed-.so page alignment in KB (default: 16 for Android 15+; needs build-tools >=35, else falls back to 4)")

    args = parser.parse_args()

    if args.action == "check":
        cmd_check()
    elif args.action == "repack":
        cmd_repack(
            args.dir,
            args.output,
            keystore_path=args.keystore,
            ks_pass=args.ks_pass,
            key_pass=args.key_pass,
            key_alias=args.alias,
            page_size_kb=args.page_size,
            ks_pass_file=args.ks_pass_file,
            key_pass_file=args.key_pass_file,
        )


if __name__ == "__main__":
    main()

