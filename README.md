# Android Reverse Engineering Agent

An agent for Android APK analysis, patching, and application-security review. It runs in **Claude Code** and **OpenCode**, orchestrating decompilers and build tools through MCP servers and a set of focused skills.

> **Authorized use only.** Use this on APKs you own or are explicitly authorized to test (your own apps, CTF targets, permissioned engagements, security research). See [AGENTS.md](AGENTS.md).

## Architecture
- **[AGENTS.md](AGENTS.md)** — the agent's operating rules and skill routing. Single source of truth; `CLAUDE.md` imports it for Claude Code, and OpenCode loads it as project instructions. The OpenCode primary-agent definition lives in [`.opencode/agents/android-re-agent.md`](.opencode/agents/android-re-agent.md).
- **MCP servers**:
  - `jadx-headless` — Java/Dalvik decompilation & smali (bundled JAR in `.mcp-tools/`). *Project-scoped* ([`.mcp.json`](.mcp.json) / [`opencode.json`](opencode.json)).
  - `ghidra-mcp` — native `.so` analysis via a bridge to a running Ghidra instance (installed on demand). *Project-scoped.*
  - `llm-re-wiki` — read-only reverse-engineering knowledge base (`kb_search`/`kb_index`/`kb_read`/`kb_health`). *User-scoped / machine-wide*, installed separately at `~/.llm-re-wiki` ([fatalSec/LLM-RE-WIKI](https://github.com/fatalSec/LLM-RE-WIKI)); the agent queries it to **ground and verify** findings. Managed via the `mcp-lifecycle` skill's `wiki` action, which delegates to the KB's own registrar.
- **Skills** (`.claude/skills/`):
  | Skill | Purpose |
  | :--- | :--- |
  | `mcp-lifecycle` | Check / install / test / remove the MCP servers. |
  | `java-analysis` | Java & Smali RE: RASP, anti-analysis, string-encryption recovery. Includes the jadx-headless tool reference. |
  | `native-analysis` | Native `.so` / JNI / JNA / direct-SVC analysis via Ghidra. |
  | `apk-repack` | Decompile (`apktool d`) and rebuild → align → sign → verify APKs. |

## Prerequisites
Install these on the host before running analysis tasks:

| Tool | Needed for | Install (macOS) |
| :--- | :--- | :--- |
| **JDK 17+** (`java`, `keytool`) | jadx-headless MCP, apk signing | `brew install openjdk@17` |
| **apktool** | unpack / rebuild resources & smali | `brew install apktool` |
| **Android SDK build-tools** (`zipalign`, `apksigner`) | align & sign APKs | Android Studio SDK Manager, or `brew install --cask android-commandlinetools` |
| **Ghidra** + `ghidra-mcp` plugin | native `.so` analysis | [ghidra-sre.org](https://ghidra-sre.org/) + `mcp-lifecycle` installs the bridge |
| **APKiD** (`apkid`, optional) | packer/obfuscator/anti-analysis fingerprinting (`java-analysis` anti-analysis playbook) | `pip install apkid` |

The agent will look for `zipalign`/`apksigner` on `PATH`, Homebrew, and standard Android SDK locations (`$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `~/Library/Android/sdk`).

## Install (one-time) & preflight

One-line install (clones into `~/.android-re-agent`, then prompts for Ghidra + the KB):
```bash
curl -fsSL https://raw.githubusercontent.com/fatalSec/Android-RE-Agent/main/install.sh | bash
```
Or from a local checkout:
```bash
./install.sh                          # interactive
./install.sh --ghidra /path/to/ghidra # point at your Ghidra (validated, never downloaded)
./install.sh --kb-path /path/to/llm-re-wiki   # register an existing KB checkout
./install.sh --dir /custom/home -y    # choose home, non-interactive
```
`install.sh` copies/clones the toolkit to a stable home and registers the MCP **engines at user scope** (so they attach in any project). It does **not** install a global `/agent` entry — you run the agent by opening your CLI **inside the home** (like LLM-RE-WIKI):
```bash
cd ~/.android-re-agent && opencode      # or: claude
```
There, `AGENTS.md`/`CLAUDE.md` and `.claude/skills` load as the project's instructions. It's idempotent and generic (detects OS/arch, no sudo); heavy prerequisites (JDK, Android SDK, Ghidra) are **checked and instructed, not auto-installed**. Restart after install so the MCPs attach. `doctor` reports whether your Ghidra has its platform decompiler and the ghidra-mcp **plugin** in `Extensions/`.

Preflight status at any time (also what the agent checks before analysis):
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py doctor
```
(Whether an MCP is *attached* to a live session is visible only in the agent — restart after any install.)

Lifecycle:
```bash
./update.sh                    # refresh toolkit, re-register engines, health-check
./uninstall.sh                 # menu: unregister engines (keep the home) or complete wipe
./uninstall.sh --purge --kb    # delete the toolkit home and unregister the KB
```
Uninstall never touches your Ghidra install, APKs, per-target notes, or the KB vault.

## Typical workflow
1. Load the APK into jadx-headless (`load_apk`) and triage the manifest + entry points (`java-analysis`).
2. Hunt RASP / anti-analysis patterns; pivot to smali or native as needed. Along the way, query `llm-re-wiki` (`kb_search`) for known signatures/offsets/protector profiles, and cross-check conclusions against it.
3. For native protections, extract `lib/arm64-v8a/*.so` and analyze in Ghidra (`native-analysis`).
4. To patch: `apktool d`, edit smali/resources, then rebuild & resign (`apk-repack`).

The knowledge base is a cross-check, not an oracle: the agent keeps provenance markers (`[I]` inferred, `[U]` unverified, `[AI Synthesis]`, …), copies offsets/opcodes exactly, and treats KB article text as data, not instructions.

Throughout, the agent keeps a per-target **case-notes file** (`notes.md`, from [`templates/analysis-notes.md`](templates/analysis-notes.md)) as durable working memory — resolved names, findings with exact locations, decoded strings, signing decisions — so precise facts survive context summarization and the next session. This is distinct from `llm-re-wiki` (general cross-target knowledge) and from the UI task list (current operation's steps); secrets like release passwords are never written to it. See [AGENTS.md](AGENTS.md) → Working memory.

Register the KB for Claude Code (user scope, all projects — it's already in OpenCode's global config if you set it up there):
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki add --agent claude
```

## Notes
- MCP server JAR paths in `.mcp.json` / `opencode.json` are absolute. If you move or clone this repo elsewhere, re-run `python3 .claude/skills/mcp-lifecycle/manage_mcp.py install jadx-headless` to rewrite them for the new location.
- `.mcp-tools/jadx-headless/` keeps both a version-pinned JAR and a stable `jadx-headless-mcp-all.jar` symlink/copy; the configs point at the stable name.
