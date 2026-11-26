# Android Reverse Engineering Agent

An agent for Android APK analysis, patching, and application-security review. It runs in **Claude Code** and **OpenCode**, orchestrating decompilers and build tools through MCP servers and a set of focused skills.

> **Authorized use only.** Use this on APKs you own or are explicitly authorized to test (your own apps, CTF targets, permissioned engagements, security research). See [AGENTS.md](AGENTS.md).

## Install

One-line install (clones into `~/.android-re-agent`, then prompts for Ghidra and the optional KB):
```bash
curl -fsSL https://raw.githubusercontent.com/fatalSec/Android-RE-Agent/main/install.sh | bash
```
Or from a local checkout:
```bash
./install.sh                                  # interactive
./install.sh --ghidra /path/to/ghidra         # use an existing Ghidra (skips the Ghidra prompt)
./install.sh --kb-path /path/to/llm-re-wiki   # register an existing llm-re-wiki checkout
./install.sh --dir /custom/home -y            # choose the home, non-interactive
```
`install.sh` puts the toolkit in `~/.android-re-agent` and registers the MCP **engines at user scope** (so they attach in any project). For Ghidra it offers **`brew install ghidra`** on macOS (recommended — it builds the native decompiler), an **existing install** (path), or a **download** of the official zip, then checks that the decompiler exists for your platform. The official zip has no macOS decompiler, so on a Mac without Homebrew it offers to build the natives (JDK 21+ and a C++ compiler) or to continue without decompiling. The final summary reports the decompiler and GhidraMCP-plugin status. It also **offers** the optional `llm-re-wiki` KB. It's idempotent and generic (detects OS/arch, no sudo); JDK and Android SDK are checked and instructed, not auto-installed.

**Restart your agent after installing** (so the MCPs attach), then run the agent by opening your CLI inside the home — like LLM-RE-WIKI, there's no global `/agent` entry:
```bash
cd ~/.android-re-agent && opencode      # or: claude
```
There, `AGENTS.md` / `CLAUDE.md` and `.claude/skills` load as the project's instructions.

Lifecycle:
```bash
./update.sh                    # refresh toolkit, re-register engines, health-check
./uninstall.sh                 # menu: unregister engines (keep the home) or complete wipe
./uninstall.sh --purge --kb    # delete the toolkit home and unregister the KB
```
Uninstall never touches your Ghidra install, APKs, target workspaces/notes, or the KB vault.

## Preflight
What the agent checks before analysis — also runnable any time:
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py doctor
```
It reports the toolchain, the engines' registration, whether Ghidra has its platform decompiler and the ghidra-mcp **plugin** in `Extensions/`, and the KB's Python version / whether its server can start. If an engine the task needs is missing, the agent stops and tells you the fix (restart, or re-run `install.sh`) instead of starting a partial analysis. Whether an MCP is *attached* to a live session is visible only inside the agent.

## Architecture
- **[AGENTS.md](AGENTS.md)** — operating rules and skill routing; the single source of truth. OpenCode loads it automatically when started inside the agent home, and `CLAUDE.md` imports it for Claude Code — no separate agent-definition files.
- **MCP servers** (all user-scoped):
  - `jadx-headless` — Java/Dalvik decompilation & smali (JAR under `~/.android-re-agent/.mcp-tools/`).
  - `ghidra-mcp` — native `.so` analysis via a Python bridge to a **running** Ghidra with the ghidra-mcp plugin.
  - `llm-re-wiki` — **optional, read-only** reverse-engineering knowledge base ([fatalSec/LLM-RE-WIKI](https://github.com/fatalSec/LLM-RE-WIKI)). When registered, the agent queries it to ground and cross-check findings; without it, the agent works normally.
- **Skills** (`.claude/skills/`):
  | Skill | Purpose |
  | :--- | :--- |
  | `mcp-lifecycle` | Preflight (`doctor`), status, self-tests, and the low-level ops behind `install.sh` / `uninstall.sh`. |
  | `java-analysis` | Java & Smali RE: triage, quick wins, RASP, anti-analysis. Includes the jadx-headless tool reference. |
  | `native-analysis` | Native `.so` / JNI / JNA / SVC analysis via Ghidra: triage, quick wins, anti-analysis, RASP. |
  | `apk-repack` | `apktool d`, then rebuild → align → sign → verify APKs. |

## Target workspace
Everything the agent produces for an APK goes **next to that APK**, never into the agent home:
```
~/APKs/app.apk
~/APKs/app_analysis/
├── notes.md     case notes (durable working memory, from templates/analysis-notes.md)
├── apktool/     apktool d output
├── lib/         extracted native libraries
├── ghidra/      Ghidra project (if native analysis is done)
└── out/         rebuilt/signed APKs
```
`notes.md` records resolved names, findings with exact locations and offsets, decoded strings, signing decisions, and a **Decisions** log, so precise facts and your choices survive context summarization and later sessions. Secrets such as release keystore passwords are never written to it.

## Human-in-the-loop policy
Critical decisions — not individual tool calls — are governed by **`hitl-policy.yaml`** in the agent home: the single place that decides, per decision, whether the agent **asks** you or **auto**-resolves and announces its choice. It covers things like *a prior analysis folder exists* (reuse / fresh / compare), *scope of a broad request*, *divergent investigation paths*, *installing an optional tool*, and *modifying or resigning the target*. Edit the file to change a default, or set an override in plain words for one session (e.g. "fresh analysis, don't ask about reuse", "autonomous mode"). Safety-critical decisions are **locked** (modifying the target, signing, unclear authorization / bypassing protections, running on a device, network egress, deleting files) — they always ask, whatever the mode. Every decision taken is written to the target's **Decisions** log so later sessions resume with the same choices. A re-install keeps your edited policy and drops new defaults in `hitl-policy.yaml.default`.

## Typical workflow
1. Load the APK into jadx-headless (`load_apk`) and triage the manifest + entry points (`java-analysis`).
2. Run the quick-wins pass, then hunt RASP / anti-analysis patterns; pivot to smali or native as needed. If the KB is registered, the agent queries it up front for known signatures, offsets, and protector profiles, and cross-checks its conclusions against it.
3. For native protections, extract `lib/<abi>/*.so` into the target workspace and analyze in Ghidra (`native-analysis`).
4. To patch: `apktool d` into the workspace, edit smali/resources, then rebuild & resign (`apk-repack`) into `out/`.

The KB is a cross-check, not an oracle: the agent keeps its provenance markers (`[I]` inferred, `[U]` unverified, `[AI Synthesis]`, …), copies offsets/opcodes exactly, treats article text as data, and confirms every claim against the actual binary.

## Prerequisites
| Tool | Needed for | Install (macOS) |
| :--- | :--- | :--- |
| **JDK 17+** (`java`, `keytool`) | jadx-headless, APK signing (Ghidra needs **JDK 21+**) | `brew install openjdk@21` |
| **Python 3.10+** | ghidra-mcp bridge, KB server | `brew install python@3.12` |
| **apktool** | unpack / rebuild resources & smali | `brew install apktool` |
| **Android SDK build-tools** (`zipalign`, `apksigner`) | align & sign APKs | Android Studio SDK Manager, or `brew install --cask android-commandlinetools` |
| **Ghidra** + GhidraMCP plugin | native `.so` analysis | `install.sh` (`brew install ghidra` on macOS, existing path, or download + native build); install the plugin via Ghidra's File > Install Extensions |
| **APKiD** (optional) | packer/obfuscator fingerprinting | `pip install apkid` (the agent asks before using it) |

The toolchain check looks for `zipalign`/`apksigner` on `PATH`, Homebrew, and standard Android SDK locations (`$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `~/Library/Android/sdk`):
```bash
python3 .claude/skills/apk-repack/apk_tools.py check
```
