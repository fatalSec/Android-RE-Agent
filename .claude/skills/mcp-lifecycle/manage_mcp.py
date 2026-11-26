#!/usr/bin/env python3
"""
MCP server installer / tester / doctor / uninstaller for OpenCode and Claude Code.
Engines: jadx-headless (1013503897/jadx-headless-mcp) and the ghidra-mcp bridge
(bethington/ghidra-mcp); llm-re-wiki is delegated to its own registrar.

Normally driven by the repo's install.sh / uninstall.sh. `doctor` is the preflight the
agent runs before analysis; `set-ghidra` records an existing Ghidra path; `download-ghidra`
fetches the latest official release (used by install.sh only when the user chooses it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# Root for binaries + project config. ANDRE_HOME (set by a global install) wins, so
# the toolkit works from any directory depth; otherwise it's the repo root (dev mode).
WORKSPACE_ROOT = Path(os.environ.get("ANDRE_HOME") or Path(__file__).resolve().parents[3])
MCP_TOOLS_DIR = WORKSPACE_ROOT / ".mcp-tools"
JADX_DIR = MCP_TOOLS_DIR / "jadx-headless"
GHIDRA_DIR = MCP_TOOLS_DIR / "ghidra-mcp"
CLAUDE_MCP_JSON = WORKSPACE_ROOT / ".mcp.json"
OPENCODE_JSON = WORKSPACE_ROOT / "opencode.json"

# User/global scope targets (for an installed agent usable from any project).
_XDG = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
OPENCODE_GLOBAL_JSON = _XDG / "opencode" / "opencode.json"
USE_GLOBAL = False  # set by --global on install/uninstall → register at user scope

JADX_REPO = "1013503897/jadx-headless-mcp"
JADX_FALLBACK_VERSION = "0.7.1"

GHIDRA_REPO = "bethington/ghidra-mcp"
GHIDRA_FALLBACK_VERSION = "7.0.0"

# llm-re-wiki is a USER-SCOPED, machine-wide knowledge base that ships its own
# multi-agent registrar. We delegate to it instead of re-implementing registration.
WIKI_ROOT = Path(os.environ.get("LLM_RE_WIKI_ROOT") or (Path.home() / ".llm-re-wiki"))

GHIDRA_CONF = MCP_TOOLS_DIR / "ghidra.conf"  # persists the user's Ghidra install dir


def log(msg: str) -> None:
    print(f"[mcp-manager] {msg}")


# ---------------------------------------------------------------------------
# Platform / Ghidra helpers (generic — no hardcoded paths)
# ---------------------------------------------------------------------------

def ghidra_platform_dir() -> str:
    """Ghidra's per-platform native dir name for the current OS/arch."""
    import platform
    mach = platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "mac_arm_64" if arm else "mac_x86_64"
    if sys.platform.startswith("linux"):
        return "linux_arm_64" if arm else "linux_x86_64"
    if os.name == "nt" or sys.platform.startswith("win"):
        return "win_x86_64"
    return f"{sys.platform}_{mach}"


def get_ghidra_dir() -> str | None:
    """Ghidra install dir: env GHIDRA_INSTALL_DIR wins, else the saved conf."""
    env = os.environ.get("GHIDRA_INSTALL_DIR")
    if env:
        return env
    if GHIDRA_CONF.is_file():
        for line in GHIDRA_CONF.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GHIDRA_INSTALL_DIR="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


def save_ghidra_dir(path: str) -> None:
    MCP_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    GHIDRA_CONF.write_text(f"GHIDRA_INSTALL_DIR={path}\n", encoding="utf-8")


def ghidra_decompiler_status(ghidra_dir: str | None) -> dict:
    """Whether this Ghidra install has the native decompiler for THIS platform."""
    if not ghidra_dir:
        return {"ghidra_dir": None, "exists": False, "decompiler_present": False,
                "note": "No Ghidra path set (pass --ghidra to install.sh or export GHIDRA_INSTALL_DIR)."}
    gdir = Path(ghidra_dir).expanduser()
    if not gdir.is_dir():
        return {"ghidra_dir": str(gdir), "exists": False, "decompiler_present": False,
                "note": "Ghidra path does not exist."}
    plat = ghidra_platform_dir()
    exe = "decompile.exe" if plat.startswith("win") else "decompile"
    decomp = gdir / "Ghidra" / "Features" / "Decompiler" / "os" / plat / exe
    present = decomp.is_file()
    return {
        "ghidra_dir": str(gdir), "exists": True, "platform": plat,
        "decompiler_present": present,
        "note": None if present else (
            f"Missing native decompiler for {plat} ({decomp}). This install cannot decompile "
            "on this platform — rebuild native components (support/buildNatives) or use a complete build."),
    }


def check_jdk_17() -> str:
    """Verify java is on PATH and is JDK 17+."""
    java_bin = shutil.which("java")
    if not java_bin:
        sys.exit("[!] Error: 'java' not found on PATH. Required JDK 17+.")

    proc = subprocess.run([java_bin, "-version"], capture_output=True, text=True)
    version_output = proc.stderr + proc.stdout
    match = re.search(r'version "(\d+)', version_output)
    if not match or int(match.group(1)) < 17:
        sys.exit(f"[!] Error: JDK 17+ required.\n{version_output.strip()}")
    log(f"Verified Java runtime: {version_output.splitlines()[0]}")
    return java_bin


def fetch_jadx_release_metadata() -> tuple[str, str, str | None]:
    """Return (version, jar_download_url, sha256_download_url) from GitHub API."""
    api_url = f"https://api.github.com/repos/{JADX_REPO}/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "re-agent-mcp-installer"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data["tag_name"].lstrip("v")
        jar_url, sha_url = None, None
        for asset in data.get("assets", []):
            name = asset["name"]
            if name.endswith("-all.jar"):
                jar_url = asset["browser_download_url"]
            elif name == "jadx-headless-mcp.sha256":
                sha_url = asset["browser_download_url"]
        if jar_url:
            return tag, jar_url, sha_url
    except Exception as exc:
        log(f"GitHub API lookup warning ({exc}), falling back to v{JADX_FALLBACK_VERSION}...")

    base = f"https://github.com/{JADX_REPO}/releases/download/v{JADX_FALLBACK_VERSION}"
    return (
        JADX_FALLBACK_VERSION,
        f"{base}/jadx-headless-mcp-{JADX_FALLBACK_VERSION}-all.jar",
        f"{base}/jadx-headless-mcp.sha256",
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def install_jadx(build_from_source: bool = False, max_ram_pct: int = 60, timeout_ms: int = 90000) -> None:
    check_jdk_17()
    JADX_DIR.mkdir(parents=True, exist_ok=True)

    if build_from_source:
        src_dir = JADX_DIR / "src"
        if not src_dir.exists():
            log(f"Cloning https://github.com/{JADX_REPO}.git ...")
            subprocess.run(["git", "clone", "--depth", "1", f"https://github.com/{JADX_REPO}.git", str(src_dir)], check=True)
        log("Building portable fat JAR via ./gradlew shadowJar ...")
        gradlew = "./gradlew.bat" if os.name == "nt" else "./gradlew"
        subprocess.run([gradlew, "shadowJar"], cwd=src_dir, check=True)
        jars = list((src_dir / "build" / "libs").glob("jadx-headless-mcp-*-all.jar"))
        if not jars:
            sys.exit("[!] Gradle build finished, but fat JAR was not found in build/libs/.")
        target_jar = JADX_DIR / "jadx-headless-mcp-all.jar"
        shutil.copy2(jars[0], target_jar)
        stable_jar = target_jar
    else:
        version, jar_url, sha_url = fetch_jadx_release_metadata()
        target_jar = JADX_DIR / f"jadx-headless-mcp-{version}-all.jar"
        if not target_jar.exists():
            log(f"Downloading {jar_url} ...")
            urllib.request.urlretrieve(jar_url, target_jar)
        else:
            log(f"Using cached JAR: {target_jar}")

        if sha_url:
            try:
                req = urllib.request.Request(sha_url, headers={"User-Agent": "re-agent-mcp-installer"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    expected_sha = resp.read().decode("utf-8").strip().split()[0].lower()
                actual_sha = sha256_file(target_jar)
                if actual_sha != expected_sha:
                    target_jar.unlink(missing_ok=True)
                    sys.exit(f"[!] SHA-256 mismatch!\nExpected: {expected_sha}\nActual:   {actual_sha}")
                log(f"SHA-256 verified OK: {actual_sha}")
            except Exception as exc:
                log(f"Note: Could not verify remote .sha256 ({exc}); local SHA-256: {sha256_file(target_jar)}")

        stable_jar = JADX_DIR / "jadx-headless-mcp-all.jar"
        shutil.copy2(target_jar, stable_jar)

    jvm_args = [
        f"-XX:MaxRAMPercentage={float(max_ram_pct)}",
        "-Djava.awt.headless=true",
        "-jar",
        str(stable_jar.resolve()),
        "--decompile-timeout-ms",
        str(timeout_ms),
    ]
    register_configs("jadx-headless", "java", jvm_args)
    test_mcp_stdio("jadx-headless")


def _find_python_310_plus() -> str | None:
    """A Python >= 3.10 interpreter (the ghidra-mcp bridge's `mcp` dep requires it)."""
    seen: set[str] = set()
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"):
        p = shutil.which(name)
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            out = subprocess.run([p, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            maj, minor = (int(x) for x in out.split("."))
            if (maj, minor) >= (3, 10):
                return p
        except Exception:
            continue
    if sys.version_info >= (3, 10):
        return sys.executable
    return None


def _ghidra_bridge_wheel_url() -> str | None:
    """Latest ghidra-mcp bridge wheel from the GitHub releases API (not a hardcoded tag)."""
    api = f"https://api.github.com/repos/{GHIDRA_REPO}/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "re-agent-mcp-installer"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for asset in data.get("assets", []):
            if asset["name"].endswith(".whl"):
                return asset["browser_download_url"]
    except Exception as exc:
        log(f"GitHub API lookup for the ghidra-mcp wheel failed ({exc}).")
    return None


def install_ghidra() -> bool:
    """Install + register the ghidra-mcp bridge. Non-fatal: returns False (never raises)
    on failure, so a broken bridge install can't crash setup."""
    GHIDRA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Already on PATH?
    system_bridge = shutil.which("bridge-mcp-ghidra")
    if system_bridge:
        log(f"Found system bridge-mcp-ghidra at {system_bridge}")
        register_configs("ghidra-mcp", system_bridge, [])
        test_mcp_stdio("ghidra-mcp")
        return True

    # 2. The bridge needs Python >= 3.10 — never build the venv with an older one.
    py = _find_python_310_plus()
    if not py:
        log("[!] ghidra-mcp bridge needs Python 3.10+, none found. Install one (e.g. "
            "`brew install python@3.12`), then re-run install.sh --ghidra <dir>. Skipping the bridge.")
        return False

    venv_dir = GHIDRA_DIR / "venv"
    venv_py = venv_dir / "bin" / "python"
    # Recreate the venv if missing or built with < 3.10 (the failure you hit).
    ok310 = False
    if venv_py.exists():
        try:
            ok310 = subprocess.run([str(venv_py), "-c", "import sys;print(sys.version_info[:2] >= (3,10))"],
                                   capture_output=True, text=True, timeout=10).stdout.strip() == "True"
        except Exception:
            ok310 = False
    if not ok310:
        shutil.rmtree(venv_dir, ignore_errors=True)
        log(f"Creating venv with {py} ...")
        if subprocess.run([py, "-m", "venv", str(venv_dir)]).returncode != 0:
            log("[!] Could not create the venv. Skipping ghidra-mcp bridge.")
            return False

    pip_bin = venv_dir / "bin" / "pip"
    bridge_bin = venv_dir / "bin" / "bridge-mcp-ghidra"
    subprocess.run([str(pip_bin), "install", "--upgrade", "pip"], capture_output=True)  # old pip can't resolve modern deps

    if not bridge_bin.exists():
        installed = False
        wheel = _ghidra_bridge_wheel_url()
        if wheel:
            log(f"Installing ghidra-mcp bridge wheel: {wheel}")
            installed = subprocess.run([str(pip_bin), "install", "--upgrade", wheel],
                                       capture_output=True, text=True).returncode == 0
        if not installed:
            log("Wheel unavailable; trying git install ...")
            r = subprocess.run([str(pip_bin), "install", f"git+https://github.com/{GHIDRA_REPO}.git"],
                               capture_output=True, text=True)
            installed = r.returncode == 0
            if not installed:
                tail = (r.stderr or r.stdout).strip()
                log("[!] ghidra-mcp bridge install failed:\n    " + tail[-800:].replace("\n", "\n    "))

    if not bridge_bin.exists():
        log("[!] bridge-mcp-ghidra not present after install — skipping registration. "
            "Fix Python/deps (needs 3.10+) and re-run install.sh --ghidra <dir>.")
        return False

    register_configs("ghidra-mcp", str(bridge_bin.resolve()), [])
    log("Registered ghidra-mcp bridge in Claude Code and OpenCode.")
    test_mcp_stdio("ghidra-mcp")
    return True


def download_ghidra(dest_dir: str | None = None) -> str | None:
    """Download + extract the latest official Ghidra public release, then record it via
    set-ghidra. NOTE: the official zip ships prebuilt natives only for some platforms
    (e.g. Linux/Windows x86_64) — on macOS the decompiler is missing and must be built
    (build-ghidra-natives) or Ghidra installed via Homebrew. Non-fatal: returns the
    install dir, or None on failure."""
    import zipfile

    api = "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "re-agent-installer"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log(f"[!] Could not query Ghidra releases ({exc}). Download from "
            "https://github.com/NationalSecurityAgency/ghidra/releases and use --ghidra <dir>.")
        return None

    url = name = None
    for a in data.get("assets", []):
        if a["name"].startswith("ghidra_") and "PUBLIC" in a["name"] and a["name"].endswith(".zip"):
            url, name = a["browser_download_url"], a["name"]
            break
    if not url:
        log("[!] No Ghidra public .zip asset in the latest release.")
        return None

    dest = (Path(dest_dir).expanduser() if dest_dir else (WORKSPACE_ROOT / "ghidra"))
    dest.mkdir(parents=True, exist_ok=True)
    zpath = dest / name
    log(f"Downloading Ghidra ({name}, ~400 MB): {url}")
    try:
        urllib.request.urlretrieve(url, zpath)
    except Exception as exc:
        log(f"[!] Ghidra download failed ({exc}).")
        return None

    log("Extracting ...")
    top = None
    try:
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            top = names[0].split("/")[0] if names else None
            z.extractall(dest)
    except Exception as exc:
        log(f"[!] Extract failed ({exc}).")
        return None
    finally:
        zpath.unlink(missing_ok=True)

    ghidra_dir = dest / top if top else None
    if not ghidra_dir or not ghidra_dir.is_dir():
        cands = [p for p in dest.iterdir() if p.is_dir() and p.name.startswith("ghidra_")]
        ghidra_dir = cands[0] if cands else None
    if not ghidra_dir:
        log("[!] Could not locate the extracted Ghidra directory.")
        return None

    save_ghidra_dir(str(ghidra_dir))
    st = ghidra_decompiler_status(str(ghidra_dir))
    log(f"Ghidra installed at {ghidra_dir}  (decompiler_present={st.get('decompiler_present')}).")
    if not st.get("decompiler_present"):
        log(f"[!] The official zip has no prebuilt decompiler for {st.get('platform')}. Build it "
            f"(`manage_mcp.py build-ghidra-natives`) or, on macOS, use `brew install ghidra`.")
    log("Note: running Ghidra needs a JDK 21+. The ghidra-mcp plugin must still be installed into "
        "<ghidra>/Ghidra/Extensions and enabled in the CodeBrowser (doctor reports it).")
    return str(ghidra_dir)


def _wiki_paths() -> tuple[Path, Path]:
    """Return (venv_python, register.py) for the installed llm-re-wiki, or exit with guidance."""
    register = WIKI_ROOT / ".kb" / "mcp" / "register.py"
    venv_py = WIKI_ROOT / ".kb" / "scripts" / ".venv" / "bin" / "python"
    python = venv_py if venv_py.exists() else Path(sys.executable)
    if not register.exists():
        sys.exit(
            f"[!] llm-re-wiki not found at {WIKI_ROOT}.\n"
            "    Install it (https://github.com/fatalSec/LLM-RE-WIKI):\n"
            "      curl -fsSL https://raw.githubusercontent.com/fatalSec/LLM-RE-WIKI/main/install.sh | bash\n"
            "    Or set LLM_RE_WIKI_ROOT to an existing checkout."
        )
    return python, register


def wiki(sub_action: str, agent: str = "claude") -> None:
    """Delegate llm-re-wiki lifecycle to its own registrar (detect|status|add|remove).

    llm-re-wiki owns its registration logic (user/global scope, JSONC-safe, backups,
    multi-agent). We do NOT duplicate it here; we shell its register.py with its own venv.
    """
    python, register = _wiki_paths()
    if sub_action == "test":
        # Start-up self-test: can the registered KB server actually run? (venv on 3.10+ with `mcp`)
        venv_py = WIKI_ROOT / ".kb" / "scripts" / ".venv" / "bin" / "python"
        health = _wiki_venv_health(venv_py)
        if not health["mcp_importable"]:
            log(f"[!] {health['note']}")
            sys.exit(1)
        server = WIKI_ROOT / ".kb" / "mcp" / "server.py"
        sys.exit(0 if test_mcp_stdio("llm-re-wiki", cmd=[str(venv_py), str(server)]) else 1)
    if sub_action == "status":
        proc = subprocess.run([str(python), str(register), "status", "--agent", agent], capture_output=True, text=True)
        print(json.dumps({"server": "llm-re-wiki", "agent": agent, "registered": proc.returncode == 0}, indent=2))
        return
    # detect | add | remove -> stream the registrar's own output
    args = [str(python), str(register), sub_action]
    if sub_action in ("add", "remove"):
        args += ["--agent", agent]
    proc = subprocess.run(args, text=True)
    sys.exit(proc.returncode)


def load_json(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"Warning: {path} had invalid JSON; backing up to {path}.bak")
            shutil.copy2(path, f"{path}.bak")
    return default


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def register_configs(server_name: str, command: str, args: list[str]) -> None:
    """Register an MCP server for Claude Code and OpenCode.

    USE_GLOBAL (--global) registers at **user scope** so the server attaches in every
    project (Claude `-s user`; OpenCode's global `~/.config/opencode/opencode.json`).
    Otherwise it writes project-local config (dev mode).
    """
    scope = "user" if USE_GLOBAL else "project"

    # 1. Claude Code
    if USE_GLOBAL:
        if shutil.which("claude"):
            subprocess.run(["claude", "mcp", "remove", "-s", "user", server_name], capture_output=True)
            subprocess.run(["claude", "mcp", "add", server_name, "-s", "user", "--", command, *args], capture_output=True)
            log("Registered with `claude mcp` CLI (user scope).")
        else:
            log("Claude Code CLI not found — skipped Claude user-scope registration.")
    else:
        claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
        claude_cfg.setdefault("mcpServers", {})[server_name] = {"type": "stdio", "command": command, "args": args}
        save_json(CLAUDE_MCP_JSON, claude_cfg)
        log(f"Updated Claude Code project config: {CLAUDE_MCP_JSON}")
        if shutil.which("claude"):
            subprocess.run(["claude", "mcp", "remove", server_name], capture_output=True)
            subprocess.run(["claude", "mcp", "add", server_name, "-s", "project", "--", command, *args], capture_output=True)
            log("Registered with `claude mcp` CLI (project scope).")

    # 2. OpenCode — prefer the `opencode` CLI so the entry lands in the location the
    #    installed OpenCode version expects (current versions use `mcp.servers.*`;
    #    hand-writing flat `mcp.<name>` can silently not load). Fall back to JSON.
    if USE_GLOBAL and shutil.which("opencode"):
        subprocess.run(["opencode", "mcp", "remove", server_name], capture_output=True)
        r = subprocess.run(["opencode", "mcp", "add", "--global", server_name, "--", command, *args],
                           capture_output=True, text=True)
        if r.returncode == 0:
            log("Registered with `opencode mcp add --global`.")
            return
        log(f"`opencode mcp add` failed ({(r.stderr or r.stdout).strip()[:200]}); writing config directly.")
    oc_path = OPENCODE_GLOBAL_JSON if USE_GLOBAL else OPENCODE_JSON
    oc_path.parent.mkdir(parents=True, exist_ok=True)
    opencode_cfg = load_json(oc_path, {"$schema": "https://opencode.ai/config.json", "mcp": {}})
    opencode_cfg.setdefault("$schema", "https://opencode.ai/config.json")
    # Current OpenCode reads servers under mcp.servers; write there when using the global file.
    table = opencode_cfg.setdefault("mcp", {})
    if USE_GLOBAL:
        table = table.setdefault("servers", {})
    table[server_name] = {"type": "local", "command": [command, *args], "enabled": True}
    save_json(oc_path, opencode_cfg)
    log(f"Updated OpenCode config ({scope} scope): {oc_path}")


def check_status(server_name: str) -> None:
    """Non-destructive status check outputting JSON."""
    if server_name == "llm-re-wiki":
        wiki("status")
        return
    claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
    opencode_cfg = load_json(OPENCODE_JSON, {"mcp": {}})

    in_claude = server_name in claude_cfg.get("mcpServers", {})
    in_opencode = server_name in opencode_cfg.get("mcp", {})

    binary_exists = False
    if server_name == "jadx-headless":
        binary_exists = (JADX_DIR / "jadx-headless-mcp-all.jar").exists()
    elif server_name in ("ghidra-mcp", "ghidra-headless"):
        venv_bridge = GHIDRA_DIR / "venv" / "bin" / "bridge-mcp-ghidra"
        binary_exists = venv_bridge.exists() or (shutil.which("bridge-mcp-ghidra") is not None)
    else:
        binary_exists = in_claude or in_opencode

    installed = (in_claude or in_opencode) and binary_exists
    print(json.dumps({"server": server_name, "installed": installed}, indent=2))


def test_mcp_stdio(server_name: str, timeout: float = 45.0, cmd: list[str] | None = None) -> bool:
    """Verify an MCP server over stdio (initialize + tools/list).

    Reads the server's stdout until the tools/list response arrives, then terminates it.
    It must NOT wait for the process to exit — MCP servers stay running after answering,
    so a `communicate()`-based wait always times out regardless of health. Non-fatal:
    returns True only on a confirmed tool list, False otherwise, and never sys.exits
    (a slow/unconfirmed probe is not proof the server is broken).
    """
    import queue
    import threading
    import time

    # An explicit command (e.g. the KB server, registered by its own registrar) skips config lookup.
    entry = {"command": cmd[0], "args": cmd[1:]} if cmd else None
    claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
    if not entry:
        entry = claude_cfg.get("mcpServers", {}).get(server_name)
    if not entry:
        for oc in (OPENCODE_JSON, OPENCODE_GLOBAL_JSON):  # project, then global (flat or mcp.servers)
            if oc.exists():
                mcp = load_json(oc, {"mcp": {}}).get("mcp", {}) or {}
                srv = mcp.get("servers", {}) if isinstance(mcp.get("servers"), dict) else {}
                op_entry = mcp.get(server_name) or srv.get(server_name) or {}
                cmd0 = op_entry.get("command", [])
                if cmd0:
                    entry = {"command": cmd0[0], "args": cmd0[1:]}
                    break
    if not entry:
        log(f"[!] Server '{server_name}' is not registered (checked {CLAUDE_MCP_JSON}, {OPENCODE_JSON}, {OPENCODE_GLOBAL_JSON}). Run install first.")
        return False

    cmd = [entry["command"], *entry.get("args", [])]
    log(f"Spawning stdio self-test (<= {int(timeout)}s): {' '.join(cmd)}")

    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mcp-manager-selftest", "version": "1.0.0"}}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    )

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
    except Exception as exc:
        log(f"[!] Could not launch '{server_name}': {exc}")
        return False

    q: "queue.Queue" = queue.Queue()

    def _reader(pipe, tag):
        try:
            for line in pipe:
                q.put((tag, line))
        except Exception:
            pass
        finally:
            q.put((tag, None))

    threading.Thread(target=_reader, args=(proc.stdout, "out"), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, "err"), daemon=True).start()

    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
    except Exception:
        pass  # server may have exited early; readers surface it

    tools_found: list = []
    stderr_tail: list = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            tag, line = q.get(timeout=0.5)
        except queue.Empty:
            if proc.poll() is not None:
                break  # server exited without answering
            continue
        if line is None:
            continue
        if tag == "err":
            stderr_tail.append(line)
            continue
        s = line.strip()
        if s.startswith("{"):
            try:
                msg = json.loads(s)
            except Exception:
                continue
            if msg.get("id") == 2 and "result" in msg:
                tools_found = [t.get("name") for t in msg["result"].get("tools", [])]
                break

    # Always clean up the server we spawned.
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    if not tools_found:
        tail = "".join(stderr_tail[-15:]).strip()
        log(f"[!] Self-test did not confirm '{server_name}' within {int(timeout)}s — "
            "it is still installed and registered and should attach after an agent restart; "
            "this probe does not prove it is broken.")
        if tail:
            print("    server stderr (tail):\n" + tail)
        return False

    log(f"SUCCESS! '{server_name}' responded over stdio with {len(tools_found)} tools.")
    print("  Sample tools:", ", ".join([t for t in tools_found[:10] if t]), "...")
    return True


def uninstall_mcp(server_name: str, purge_files: bool = False) -> None:
    """Remove an MCP server from Claude Code (.mcp.json + CLI) and OpenCode (opencode.json)."""
    removed_any = False

    if CLAUDE_MCP_JSON.exists():
        claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
        if server_name in claude_cfg.get("mcpServers", {}):
            del claude_cfg["mcpServers"][server_name]
            save_json(CLAUDE_MCP_JSON, claude_cfg)
            log(f"Removed '{server_name}' from {CLAUDE_MCP_JSON}")
            removed_any = True

    if shutil.which("claude"):
        for scope in ("project", "user", "local"):
            subprocess.run(["claude", "mcp", "remove", "-s", scope, server_name], capture_output=True)

    if shutil.which("opencode"):
        subprocess.run(["opencode", "mcp", "remove", server_name], capture_output=True)

    for oc in (OPENCODE_JSON, OPENCODE_GLOBAL_JSON):  # project + global, both flat and mcp.servers
        if oc.exists():
            opencode_cfg = load_json(oc, {"mcp": {}})
            mcp = opencode_cfg.get("mcp", {})
            changed = False
            for table in (mcp, mcp.get("servers", {}) if isinstance(mcp.get("servers"), dict) else {}):
                if server_name in table:
                    del table[server_name]
                    changed = True
            if changed:
                save_json(oc, opencode_cfg)
                log(f"Removed '{server_name}' from {oc}")
                removed_any = True

    if purge_files:
        if server_name == "jadx-headless" and JADX_DIR.exists():
            shutil.rmtree(JADX_DIR)
            log(f"Purged directory: {JADX_DIR}")
        elif server_name in ("ghidra-mcp", "ghidra-headless") and GHIDRA_DIR.exists():
            shutil.rmtree(GHIDRA_DIR)
            log(f"Purged directory: {GHIDRA_DIR}")

    if not removed_any:
        log(f"Server '{server_name}' was not found in active configs.")
    else:
        log(f"Uninstalled '{server_name}' cleanly.")


def list_installed() -> None:
    claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
    opencode_cfg = load_json(OPENCODE_JSON, {"mcp": {}})
    print("\n=== Claude Code (.mcp.json) ===")
    for k, v in claude_cfg.get("mcpServers", {}).items():
        print(f"  - {k}: {v.get('command')} {' '.join(v.get('args', []))}")
    print("\n=== OpenCode (opencode.json) ===")
    for k, v in opencode_cfg.get("mcp", {}).items():
        cmd = " ".join(v.get("command", []))
        print(f"  - {k} (enabled={v.get('enabled', True)}): {cmd}\n")


def _registered(server_name: str) -> dict:
    claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
    def _oc_has(cfg: dict) -> bool:
        mcp = cfg.get("mcp", {}) or {}
        srv = mcp.get("servers", {}) if isinstance(mcp.get("servers"), dict) else {}
        return server_name in mcp or server_name in srv
    opencode_cfg = load_json(OPENCODE_JSON, {"mcp": {}})
    opencode_global = load_json(OPENCODE_GLOBAL_JSON, {"mcp": {}})
    claude_user = False
    if shutil.which("claude"):
        try:
            claude_user = subprocess.run(["claude", "mcp", "get", server_name],
                                         capture_output=True, timeout=20).returncode == 0
        except Exception:
            claude_user = False
    return {
        "claude_project": server_name in claude_cfg.get("mcpServers", {}),
        "claude_user": claude_user,
        "opencode_project": _oc_has(opencode_cfg),
        "opencode_global": _oc_has(opencode_global),
    }


def ghidra_plugin_status(ghidra_dir: str | None) -> dict:
    """Is a ghidra-mcp plugin/extension installed? Looks in the install dir
    (<ghidra>/Ghidra/Extensions/Ghidra) and in Ghidra's per-user settings dirs, where
    File > Install Extensions puts it (macOS ~/Library/ghidra/<ver>/Extensions,
    Linux ~/.config/ghidra/<ver>/Extensions, older ~/.ghidra/.<ver>/Extensions)."""
    if not ghidra_dir:
        return {"present": False, "note": "No Ghidra path set."}
    home = Path.home()
    dirs = [Path(ghidra_dir).expanduser() / "Ghidra" / "Extensions" / "Ghidra"]
    for pattern in ("Library/ghidra/*/Extensions", ".config/ghidra/*/Extensions", ".ghidra/.ghidra_*/Extensions"):
        dirs += sorted(home.glob(pattern))
    searched, hits = [], []
    for ext in dirs:
        if not ext.is_dir():
            continue
        searched.append(str(ext))
        hits += [str(p) for p in ext.iterdir() if p.is_dir() and "mcp" in p.name.lower()]
    return {
        "searched": searched or [str(dirs[0])],
        "present": bool(hits),
        "matches": hits,
        "note": None if hits else (
            "No ghidra-mcp extension found. Install it in Ghidra (File > Install Extensions) and enable it "
            "in the CodeBrowser — the bridge needs a running Ghidra with this plugin to connect."),
    }


def _find_jdk(min_major: int = 21) -> str | None:
    """A JAVA_HOME for a JDK >= min_major (prefers exactly 21, the version Ghidra targets)."""
    import re
    cands: list[str] = []
    if sys.platform == "darwin" and Path("/usr/libexec/java_home").exists():
        for spec in (str(min_major), f"{min_major}+"):
            out = subprocess.run(["/usr/libexec/java_home", "-v", spec], capture_output=True, text=True).stdout.strip()
            if out:
                cands.append(out)
    if os.environ.get("JAVA_HOME"):
        cands.append(os.environ["JAVA_HOME"])
    java = shutil.which("java")
    if java:
        props = subprocess.run([java, "-XshowSettings:properties", "-version"], capture_output=True, text=True).stderr
        m = re.search(r"java\.home = (.+)", props)
        if m:
            cands.append(m.group(1).strip())
    for home in cands:
        jbin = Path(home) / "bin" / "java"
        if not jbin.exists():
            continue
        ver = subprocess.run([str(jbin), "-version"], capture_output=True, text=True).stderr
        m = re.search(r'version "(\d+)', ver)
        if m and int(m.group(1)) >= min_major:
            return home
    return None


def build_ghidra_natives(ghidra_dir: str | None = None) -> bool:
    """Build Ghidra's native components (decompiler etc.) for this platform with the
    bundled Gradle wrapper: `<ghidra>/support/gradle/gradlew buildNatives`. Needs a
    JDK 21+ and a C/C++ toolchain. Non-fatal: returns True only if the decompiler is
    present afterwards."""
    gdir = ghidra_dir or get_ghidra_dir()
    if not gdir or not Path(gdir).expanduser().is_dir():
        log("[!] No Ghidra install dir (pass a path or run set-ghidra first).")
        return False
    gdir = str(Path(gdir).expanduser())
    if ghidra_decompiler_status(gdir).get("decompiler_present"):
        log("Decompiler already present for this platform — nothing to build.")
        return True
    gradlew = Path(gdir) / "support" / "gradle" / "gradlew"
    if not gradlew.exists():
        log(f"[!] {gradlew} not found — this Ghidra copy can't build its natives.")
        return False
    jdk = _find_jdk(21)
    if not jdk:
        log("[!] Building Ghidra natives needs a JDK 21+ (e.g. `brew install openjdk@21`).")
        return False
    if sys.platform == "darwin":
        if subprocess.run(["xcrun", "--find", "clang++"], capture_output=True).returncode != 0:
            log("[!] No C++ compiler — install the Xcode Command Line Tools: `xcode-select --install`.")
            return False
    elif not any(shutil.which(c) for c in ("c++", "g++", "clang++")):
        log("[!] No C++ compiler found (install build-essential / gcc-c++ / clang).")
        return False
    log(f"Building Ghidra natives with JAVA_HOME={jdk} (Gradle is downloaded on first run; this takes several minutes) ...")
    env = os.environ.copy()
    env["JAVA_HOME"] = jdk
    r = subprocess.run([str(gradlew), "buildNatives"], cwd=str(gradlew.parent), env=env)
    st = ghidra_decompiler_status(gdir)
    if r.returncode == 0 and st.get("decompiler_present"):
        log(f"Native build OK — decompiler present for {st.get('platform')}.")
        return True
    log(f"[!] Native build did not produce the decompiler (gradle exit {r.returncode}). {st.get('note') or ''}")
    return False


def _wiki_venv_health(venv_py: Path) -> dict:
    """Python version of the KB venv and whether it can import `mcp` (needs 3.10+).
    A registered server whose venv fails this check will show as 'failed' in the agent."""
    if not venv_py.exists():
        return {"venv_python": None, "python_version": None, "mcp_importable": False,
                "note": "KB venv missing — re-run install.sh (it rebuilds the KB venv on Python 3.10+)."}
    try:
        p = subprocess.run([str(venv_py), "-c",
                            "import sys\nv='%d.%d.%d' % sys.version_info[:3]\n"
                            "try:\n import mcp; ok=True\nexcept Exception:\n ok=False\nprint(v, ok)"],
                           capture_output=True, text=True, timeout=30)
        ver, ok = (p.stdout.strip().split() + ["?", "False"])[:2]
    except Exception:
        ver, ok = "?", "False"
    importable = ok == "True"
    note = None
    if not importable:
        note = (f"KB venv is Python {ver} and cannot import `mcp` (needs 3.10+), so the KB server "
                "cannot start. Re-run install.sh — it rebuilds the KB venv on Python 3.10+.")
    return {"venv_python": str(venv_py), "python_version": ver, "mcp_importable": importable, "note": note}


def _wiki_status_report() -> dict:
    register = WIKI_ROOT / ".kb" / "mcp" / "register.py"
    venv_py = WIKI_ROOT / ".kb" / "scripts" / ".venv" / "bin" / "python"
    installed = register.is_file()
    res = {"installed": installed, "root": str(WIKI_ROOT), "claude": False, "opencode": False}
    if installed:
        res.update(_wiki_venv_health(venv_py))
        py = str(venv_py) if venv_py.exists() else sys.executable
        for agent in ("claude", "opencode"):
            try:
                p = subprocess.run([py, str(register), "status", "--agent", agent],
                                   capture_output=True, text=True, timeout=30)
                res[agent] = p.returncode == 0
            except Exception:
                res[agent] = False
    return res


def doctor() -> None:
    """One-shot preflight: on-disk + config state of every tool/MCP, as JSON.

    NOTE: this cannot see whether an MCP is *attached* to a running agent session —
    that lives in the agent's own tool registry. After installing, restart the agent.
    """
    import platform as _plat

    toolchain = {}
    apk_tools = WORKSPACE_ROOT / ".claude" / "skills" / "apk-repack" / "apk_tools.py"
    if apk_tools.is_file():
        try:
            p = subprocess.run([sys.executable, str(apk_tools), "check"],
                               capture_output=True, text=True, timeout=60)
            toolchain = json.loads(p.stdout or "{}")
        except Exception as exc:
            toolchain = {"error": str(exc)}

    jadx = {"installed": (JADX_DIR / "jadx-headless-mcp-all.jar").exists(), **_registered("jadx-headless")}

    gdir = get_ghidra_dir()
    bridge = (GHIDRA_DIR / "venv" / "bin" / "bridge-mcp-ghidra").exists() or bool(shutil.which("bridge-mcp-ghidra"))
    ghidra = {"bridge_installed": bridge, **_registered("ghidra-mcp"),
              "ghidra": ghidra_decompiler_status(gdir), "plugin": ghidra_plugin_status(gdir)}

    solvers = {}
    for mod in ("unicorn", "z3"):
        try:
            subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True, timeout=20, check=True)
            solvers[mod] = True
        except Exception:
            solvers[mod] = False

    report = {
        "platform": {"os": sys.platform, "arch": _plat.machine(), "ghidra_os_dir": ghidra_platform_dir()},
        "install": {"andre_home": os.environ.get("ANDRE_HOME"), "workspace_root": str(WORKSPACE_ROOT),
                    "mode": "global" if os.environ.get("ANDRE_HOME") else "dev/project"},
        "note": ("On-disk/config state only. Whether an MCP is ATTACHED to this agent session is "
                 "visible only in the agent's tool registry — restart the agent after any install. "
                 "If something is missing, run install.sh (do not install mid-analysis)."),
        "toolchain": toolchain,
        "jadx_headless": jadx,
        "ghidra_mcp": ghidra,
        "llm_re_wiki": _wiki_status_report(),
        "apkid": {"installed": bool(shutil.which("apkid"))},
        "native_solvers": solvers,
    }
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage MCP servers for OpenCode and Claude Code")
    sub = parser.add_subparsers(dest="action", required=True)

    # status
    p_status = sub.add_parser("status", help="Check status of an MCP server")
    p_status.add_argument("server", nargs="?", default="jadx-headless", help="Server name (jadx-headless, ghidra-mcp)")
    p_status.add_argument("--server", dest="server_flag", help="Server name")

    # install
    p_install = sub.add_parser("install", help="Install and register an MCP server")
    p_install.add_argument("server", choices=["jadx-headless", "ghidra-mcp"], help="MCP server to install")
    p_install.add_argument("--from-source", action="store_true", help="Build via gradle instead of release JAR (jadx only)")
    p_install.add_argument("--max-ram-pct", type=int, default=60, help="JVM MaxRAMPercentage")
    p_install.add_argument("--timeout-ms", type=int, default=90000, help="Decompile timeout in ms")
    p_install.add_argument("--global", dest="use_global", action="store_true", help="Register at user scope (attach in every project), not project-local")

    # test
    p_test = sub.add_parser("test", help="Test MCP server via stdio")
    p_test.add_argument("server", default="jadx-headless", nargs="?")

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Uninstall an MCP server")
    p_uninstall.add_argument("server", help="Server name (jadx-headless, ghidra-mcp) or 'all'")
    p_uninstall.add_argument("--purge", action="store_true", help="Delete downloaded binaries")
    p_uninstall.add_argument("--global", dest="use_global", action="store_true", help="Also clean user-scope (global) registrations")

    # list
    sub.add_parser("list", help="List configured MCP servers")

    # wiki (delegates to llm-re-wiki's own registrar — user/global scope)
    p_wiki = sub.add_parser("wiki", help="Manage the user-scoped llm-re-wiki KB via its own registrar")
    p_wiki.add_argument("sub", choices=["detect", "status", "add", "remove", "test"],
                        help="Registrar action; 'test' = start-up self-test of the KB server")
    p_wiki.add_argument("--agent", default="claude", help="Target agent: claude | opencode | cursor | all (default: claude)")

    # doctor (preflight status, JSON)
    sub.add_parser("doctor", help="Preflight: report on-disk/config state of all tools and MCPs as JSON")

    # set-ghidra (persist + validate the user's Ghidra install path)
    p_gh = sub.add_parser("set-ghidra", help="Record the user's Ghidra install path and check its decompiler")
    p_gh.add_argument("path", help="Path to the Ghidra install directory")

    # build-ghidra-natives (build the decompiler etc. for this platform via the bundled gradlew)
    p_bn = sub.add_parser("build-ghidra-natives", help="Build Ghidra's native decompiler for this platform (needs JDK 21+ and a C++ compiler)")
    p_bn.add_argument("path", nargs="?", default=None, help="Ghidra install dir (default: the recorded one)")

    # download-ghidra (fetch + extract the latest official Ghidra, then set-ghidra)
    p_dg = sub.add_parser("download-ghidra", help="Download + extract the latest official Ghidra public release")
    p_dg.add_argument("--dir", dest="dir", default=None, help="Where to install it (default: <home>/ghidra)")

    args = parser.parse_args()

    global USE_GLOBAL
    USE_GLOBAL = bool(getattr(args, "use_global", False))

    if args.action == "status":
        check_status(args.server_flag or args.server)
    elif args.action == "install":
        if args.server == "jadx-headless":
            install_jadx(args.from_source, args.max_ram_pct, args.timeout_ms)
        elif args.server == "ghidra-mcp":
            install_ghidra()
    elif args.action == "test":
        sys.exit(0 if test_mcp_stdio(args.server) else 1)
    elif args.action == "uninstall":
        if args.server == "all":
            claude_cfg = load_json(CLAUDE_MCP_JSON, {"mcpServers": {}})
            opencode_cfg = load_json(OPENCODE_JSON, {"mcp": {}})
            for name in set(claude_cfg.get("mcpServers", {})) | set(opencode_cfg.get("mcp", {})):
                uninstall_mcp(name, purge_files=args.purge)
        else:
            uninstall_mcp(args.server, purge_files=args.purge)
    elif args.action == "list":
        list_installed()
    elif args.action == "wiki":
        wiki(args.sub, agent=args.agent)
    elif args.action == "doctor":
        doctor()
    elif args.action == "set-ghidra":
        # Exit codes let install.sh act on the result: 0 = decompiler present,
        # 3 = install found but no decompiler for this platform, 1 = path doesn't exist.
        status = ghidra_decompiler_status(args.path)
        if status.get("exists"):
            save_ghidra_dir(str(Path(args.path).expanduser()))
        print(json.dumps(status, indent=2))
        sys.exit(0 if status.get("decompiler_present") else (3 if status.get("exists") else 1))
    elif args.action == "build-ghidra-natives":
        sys.exit(0 if build_ghidra_natives(args.path) else 1)
    elif args.action == "download-ghidra":
        sys.exit(0 if download_ghidra(args.dir) else 1)


if __name__ == "__main__":
    main()
