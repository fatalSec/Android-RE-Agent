---
name: mcp-lifecycle
description: Preflight (doctor), status, self-tests, and the low-level install/register/uninstall ops behind install.sh / uninstall.sh — for the MCP engines (jadx-headless, ghidra-mcp) and the optional, read-only llm-re-wiki knowledge base, for OpenCode and Claude Code.
---

# Skill: MCP Lifecycle Manager (`mcp-lifecycle`)

## Provisioning model (install / doctor / uninstall)
Installs happen **once, out of the analysis session**, because a newly-registered MCP only attaches to a *new* agent session:
- **`./install.sh`** (in the agent home, `~/.android-re-agent`) — the user runs it in a terminal; it registers the engines at **user scope**, asks for Ghidra (an **existing path** or **download** the official release — the user's choice), and **offers** the optional KB. Then the user restarts the agent. Heavy prerequisites (JDK, Android SDK) are checked-and-instructed, never auto-installed.
- **`manage_mcp.py doctor`** — the agent's **preflight**: a JSON snapshot of on-disk/config state, including the Ghidra decompiler + plugin and the KB's Python version / `mcp` importability (it can't see session attach — that's the agent's own registry). Used as in Workflow 1.
- **`./uninstall.sh`** — unregisters the engines (menu: keep the home, or complete wipe; `--kb` to unregister the KB); never deletes the user's Ghidra, APKs, target workspaces/notes, or KB vault.

The `install` / `uninstall` / `wiki add|remove` / `download-ghidra` actions below are the low-level ops those scripts call. **The agent never runs them as a mid-analysis reflex** — only on the user's explicit request; otherwise it points the user at `./install.sh` and a restart.

## Servers
| Server | Role | Registered at | Lifecycle owner |
| :--- | :--- | :--- | :--- |
| `jadx-headless` | Java/Smali engine (JAR under `<home>/.mcp-tools/`) | **user scope** (Claude `-s user`; OpenCode `mcp.servers`) | `manage_mcp.py install … --global` (driven by `install.sh`) |
| `ghidra-mcp` | Native engine — Python bridge to a running Ghidra | **user scope** | `manage_mcp.py install ghidra-mcp --global`; Ghidra itself via `set-ghidra` / `download-ghidra` |
| `llm-re-wiki` | **Optional, read-only** knowledge base (machine-wide) | **user scope** | its **own** registrar `~/.llm-re-wiki/.kb/mcp/register.py` — we **delegate**, never duplicate |

## Rules
1. **Never install, register, or uninstall without explicit user confirmation** — and never mid-analysis. Provisioning is `install.sh`.
2. After installing an engine, run the `test` action to verify the stdio handshake and report the discovered tools.
3. "Installed" is not "usable" for `ghidra-mcp`: the bridge being present doesn't mean Ghidra is running with the plugin and the target loaded (see `native-analysis`). Report both.
4. The KB is **optional**: its absence is never a blocker for analysis. Mention it once as an add-on (`./install.sh`), then proceed without it.

---

## Workflows

### 1. Just-in-time capability check (not upfront)
Check a capability's MCP only when a task reaches the branch that needs it (jadx for Java/Smali, `ghidra-mcp` for native `.so`, `llm-re-wiki` for KB queries). Cheapest first:

1. **Registry first (free).** If the capability's tools are already in your active registry (`load_apk`/`get_class_source` for jadx, the `ghidra-mcp` tools, `kb_search` for the KB), just use them.
2. **`doctor` only when they're absent:**
   ```bash
   python3 .claude/skills/mcp-lifecycle/manage_mcp.py doctor
   ```
   Then route — **you do not install mid-session**:
   - **installed but not attached** → the user **restarts** (`/mcp` in Claude Code; `opencode -c`), then continue.
   - **not installed** → the user runs **`./install.sh`** (it prompts for Ghidra and the optional KB; `--ghidra <dir>` / `--kb-path <dir>` skip the prompts), then restarts.
   - **Ghidra present but `decompiler_present: false`** → that install can't decompile here; the user repairs it, or re-runs `./install.sh` and chooses **download**. Do **not** hand-roll a headless/`analyzeHeadless` workaround.
   - **KB registered but `mcp_importable: false`** → its server can't start (old Python); the user re-runs `./install.sh`. Proceed without the KB meanwhile — it's optional.

> **This is a gate, not a warning (AGENTS.md preflight).** If an *engine* the task needs is missing/not attached, stop and report the blocker + fix first — don't silently start the parts that happen to work. A reduced CLI-only pass is offered as a user choice, never the default. A missing KB never gates anything.

---

### 2. Engine install ops (what `install.sh` runs — on explicit request only)
- **`jadx-headless`:**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py install jadx-headless --global
  ```
- **Ghidra + `ghidra-mcp` bridge:**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py set-ghidra </path/to/ghidra>     # existing install (validates its decompiler)
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py download-ghidra                  # or: fetch the latest official release
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py install ghidra-mcp --global      # the Python bridge (needs Python 3.10+)
  ```
  The bridge connects to a **running** Ghidra with the ghidra-mcp plugin in `<ghidra>/Ghidra/Extensions/` (`doctor` reports the plugin).
- **Verify:**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py test <jadx-headless|ghidra-mcp>
  ```

### 3. Listing configured servers
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py list
```

### 4. `llm-re-wiki` knowledge base (optional, read-only, delegated)
A separate, machine-wide, **read-only** MCP (`kb_search`, `kb_index`, `kb_read`, `kb_health`) — the agent only reads it. It's **optional**: `install.sh` offers it; the user can skip it entirely. Ops go through its own registrar:

- **Status / detect:**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki status --agent opencode
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki detect
  ```
- **Start-up test** — can the KB server actually run? (venv on Python 3.10+ with `mcp`, then an MCP handshake):
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki test
  ```
  A registered KB that fails this shows as "failed" in the agent; the fix is re-running `install.sh` (it rebuilds the KB venv on 3.10+ and only registers a server that starts).
- **Register / unregister** (user scope; normally done by `install.sh` / `uninstall.sh --kb` — directly only on explicit request):
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki add --agent all
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki remove --agent all
  ```
If the KB isn't at `~/.llm-re-wiki`, these print the install instructions; `LLM_RE_WIKI_ROOT` points them elsewhere.

### 5. Uninstall
Normally the user runs `./uninstall.sh` (menu: unregister engines and keep the home, or complete wipe). Low-level equivalent, on explicit request:
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py uninstall <jadx-headless|ghidra-mcp> --global          # config only
python3 .claude/skills/mcp-lifecycle/manage_mcp.py uninstall <jadx-headless|ghidra-mcp> --global --purge  # + delete binaries
```
