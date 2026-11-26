---
name: apk-repack
description: Unpack APKs to smali/resources with apktool, and rebuild, page-align (4 KB or 16 KB for Android 15+), and resign APKs with modern v2/v3 signatures via a deterministic script.
---

# Skill: APK Unpack, Rebuild & Resigning (`apk-repack`)

## Rules
1. **Verify the toolchain first:** run the `check` action before repacking.
2. **Resigning goes through the script only — no hand-run commands.** The v2/v3 pipeline order is strict and error-prone (`apktool b` → `zipalign` *before* signing → `apksigner sign` → `apksigner verify`), and the tools live in the SDK `build-tools/<ver>/` dir (not on `PATH`). `apk_tools.py repack` encodes that order, finds the tools, and gates on `apksigner verify`. Do **not** substitute a raw `zipalign`/`apksigner` sequence, and never use `jarsigner` (v1-only — apps targeting API 30+ require a v2 signature to install).
3. **Signing certificate (`→ Decision point: signing_identity`).** Resolve **debug** vs **release** per the Decision policy (it's a locked decision — it always asks). In both cases the agent runs `repack`; this skill just explains the options:
   - **Debug** — auto-generated debug keystore (`CN=Android Debug`, password `android`). No secret involved.
   - **Release** — the user's own keystore: the keystore **path**, key **alias**, and a **path to a file containing the password** (one-time: `printf %s 'PASSWORD' > key.pw`). Run `repack` with `--ks-pass-file`; `apksigner` reads the file itself. **Hard rule (not a policy choice): never accept, echo, log, or store the password value** — no pasting it into chat (the transcript can't be scrubbed), no command line, no notes. The agent never sees the secret. After a successful sign, tell the user they can delete the password file.

> **Modifying the target** (patching smali, neutralizing a check, editing resources) before a rebuild is **`→ Decision point: modify_target`** (locked — confirm what and why first).

## Agent Commands

**Paths:** `<ws>` below is the target workspace beside the APK — `<apk-dir>/<apk-name>_analysis` (see `AGENTS.md` → Target workspace). Always use **absolute** paths; never write decoded output or rebuilt APKs into the agent home (your working directory) or the repo.

### 1. Check Toolchain Availability
```bash
python3 .claude/skills/apk-repack/apk_tools.py check
```
Reports paths for `java`, `keytool`, `apktool`, `zipalign`, and `apksigner`.

### 2. Unpack / Decompile an APK
Unpacking is a single, safe command — run `apktool` directly (not through the script):
```bash
apktool d </abs/path/app.apk> -o <ws>/apktool -f
```
Extracts `AndroidManifest.xml`, resources, assets, and disassembled smali for analysis or patching. (For Java decompilation use the `jadx-headless` MCP via the `java-analysis` skill instead.)

### 3. Rebuild, Align, Sign & Verify (resign)
This is the **only** supported way to resign — deterministic order, no hand-run commands. First ask debug vs release (Rule 3).

**Debug** (agent may run it):
```bash
python3 .claude/skills/apk-repack/apk_tools.py repack <ws>/apktool -o <ws>/out/<apk-name>-signed.apk
```

**Release** (agent runs it) — the user supplies the keystore path, alias, and a password **file** path (see Rule 3). The agent never handles the password value:
```bash
python3 .claude/skills/apk-repack/apk_tools.py repack <ws>/apktool -o <ws>/out/<apk-name>-signed.apk \
  --keystore </path/to/release.keystore> --alias <release-alias> --ks-pass-file </path/to/key.pw>
```
`apksigner` reads the password from the file (`file:` spec) — it is never on the command line. If release `repack` is run with neither `--ks-pass-file` nor a terminal, the script refuses and prints the `printf … > key.pw` one-liner. `--ks-pass`/`--key-pass` (raw values) exist for unattended CI only — do not use them to carry a user's release password through chat.

Common option (either mode): `--page-size 16` — `.so` page alignment in KB, `16` (default) or `4`.

Output is a JSON report with `signing` (`debug`/`release`), `output_apk`, `page_size_kb`, and the `apksigner verify` result (`verified`). No password is ever printed.

Record the resign in the target's case notes (`notes.md` — see `AGENTS.md` → Working memory): mode, keystore path, alias, output APK + its SHA-256, and `verified`. **Never** write the release password (or the password-file contents) into notes.

## Page alignment (16 KB)
`--page-size` controls alignment of **uncompressed** `.so` entries: **16 KB (default)** is required for Android 15+ (API 35) devices, **4 KB** is the legacy value. It needs Android SDK build-tools **≥ 35**; on older `zipalign` the script falls back to 4 KB and warns (upgrade build-tools to get 16 KB). The zip entry alignment stays 4 bytes regardless. Note: this aligns `.so` entries *inside the APK*; true 16 KB ELF-segment support for the libraries themselves is a build-time/NDK concern, not something resigning can add.
