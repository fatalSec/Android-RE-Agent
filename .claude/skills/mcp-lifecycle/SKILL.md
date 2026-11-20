---
name: mcp-lifecycle
description: Check, install, test, list, and uninstall the project MCP engines (jadx-headless, ghidra-mcp) and register/unregister the user-scoped llm-re-wiki knowledge base, for OpenCode and Claude Code, on user confirmation.
---

# Skill: MCP Lifecycle Manager (`mcp-lifecycle`)

## Server tiers (why two code paths)
This project uses two kinds of MCP server, managed differently on purpose:

| Server | Tier | Scope | Lifecycle owner |
| :--- | :--- | :--- | :--- |
| `jadx-headless` | Project RE engine (bundled JAR) | **project** (`.mcp.json` / `opencode.json`) | `manage_mcp.py` installs the binary and writes project config |
| `ghidra-mcp` | Project RE engine (venv bridge to Ghidra) | **project** | `manage_mcp.py` |
| `llm-re-wiki` | User-scoped knowledge base (machine-wide, read-only) | **user / global** (all projects) | its **own** registrar at `~/.llm-re-wiki/.kb/mcp/register.py` — we **delegate**, never duplicate |

`manage_mcp.py wiki` shells the wiki's own registrar (with its venv Python) rather than re-implementing registration, because that registrar already handles user-scope, JSONC-safe edits, config backups, and multiple agents (Claude Code / OpenCode / Cursor). Keeping one source of truth avoids config drift.

## Provisioning model (setup / doctor / uninstall)
Installs happen **once, out of the analysis session**, because a newly-registered MCP only attaches to a *new* agent session:
- **`./install.sh`** (repo root) — the user runs it in a terminal to install + register everything, then restarts the agent. It orchestrates the `manage_mcp.py` / `apk_tools.py` actions below; heavy prereqs (JDK, Android SDK, Ghidra) are checked-and-instructed, never auto-installed; Ghidra is pointed at, never downloaded.
- **`manage_mcp.py doctor`** — the agent's **preflight**: a JSON snapshot of on-disk/config state (it can't see session attach — that's the agent's own registry). Used as in Workflow 1.
- **`./uninstall.sh`** — unregisters the engines (optionally `--purge` binaries, `--kb` to unregister the KB); never deletes the user's Ghidra, APKs, notes, or KB vault.

The `install` / `uninstall` / `wiki` actions below are the low-level ops those scripts call — the agent invokes them directly only on explicit user request, not as a mid-analysis install reflex.

## Rules & Behavioral Guidelines
1. **Never install, register, or uninstall without explicit user confirmation.** Confirm the specific install/registration before running it.
2. After installing a project engine, always run the `test` action to verify the JSON-RPC 2.0 `stdio` handshake and report the discovered tools.
3. "Installed" is not "usable" for `ghidra-mcp`: the bridge being present does not mean Ghidra is running with the target loaded (see `native-analysis`). Report both.

---

## Workflows

### 1. Just-in-time capability check (not upfront)
Do **not** check or install every MCP before starting — check a capability's MCP only when a task actually reaches the branch that needs it (jadx for Java/Smali, `ghidra-mcp` for native `.so`, `llm-re-wiki` for KB queries). Two-step, cheapest first:

1. **Registry first (free, no script).** Look at your active tool registry: if the capability's tools are already present (`load_apk`/`get_class_source` for jadx, the `ghidra-mcp` tools, `kb_search` for the wiki), just use them — nothing to run.
2. **`doctor` only when they're absent.** If the needed tools are *not* in your registry, run the preflight for on-disk/config state:
   ```bash
   python3 .claude/skills/mcp-lifecycle/manage_mcp.py doctor
   ```
   Then route — **you do not install mid-session** (an install only attaches to a *new* session):
   - **installed but not attached** → tell the user to **restart** (`/mcp` in Claude Code; `opencode -c` / `opencode service restart`), then continue.
   - **not installed** → tell the user to run **`./install.sh`** (`--ghidra <dir>` for native; `--with-kb` / `--kb-path <dir>` for the KB), then restart.
   - **Ghidra present but `decompiler_present: false`** → that install can't decompile on this platform; tell the user to fix it (doctor names the missing file). Do **not** hand-roll a headless/analyzeHeadless workaround.

> Run this at the **start of the branch that needs the capability** (e.g. verify `ghidra-mcp` before native deep-work), not mid-flow. Provisioning is the user-run `install.sh`; the `install`/`wiki add` actions below are what `install.sh` calls — invoke them directly only if the user explicitly asks you to, not as a mid-analysis reflex.
>
> **This is a gate, not a warning (AGENTS.md preflight).** If a capability the task needs is missing/not-attached, **stop and report the blocker + fix first** — don't silently start the parts that happen to work. A reduced CLI-only pass while engines are down is offered as a user choice, never the default.

---

### 2. Installation (Upon Confirmation)
- **`jadx-headless` (Java / Dalvik analysis):**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py install jadx-headless
  ```
  *(Or from source: `python3 .claude/skills/mcp-lifecycle/manage_mcp.py install jadx-headless --from-source`)*

- **`ghidra-mcp` (Native C/C++ ELF analysis):**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py install ghidra-mcp
  ```
  This installs **only the bridge** (`bridge-mcp-ghidra`) — **not Ghidra**. Ghidra is a manual install the user owns: **ask the user for their Ghidra install path / running instance; never download or install Ghidra automatically.** The bridge then connects to that instance (see `native-analysis` §3).

- **Verification:** Run stdio self-test:
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py test <jadx-headless|ghidra-mcp>
  ```

---

### 3. Listing Configured Servers
```bash
python3 .claude/skills/mcp-lifecycle/manage_mcp.py list
```

---

### 3b. `llm-re-wiki` Knowledge Base (user-scoped, delegated)
The RE knowledge base is a separate, machine-wide, read-only MCP (`kb_search`, `kb_index`, `kb_read`, `kb_health`). It is **not** a project engine — manage it through its own registrar via the `wiki` action:

- **Detect** which agents are installed:
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki detect
  ```
- **Status** for an agent (JSON `registered: true|false`):
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki status --agent claude
  ```
- **Register** (user scope — affects all projects; confirm with the user first):
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki add --agent claude   # or: opencode | cursor | all
  ```
  Restart/reload the agent afterwards so it attaches (`/mcp` in Claude Code; `opencode service restart` for OpenCode).
- **Unregister:**
  ```bash
  python3 .claude/skills/mcp-lifecycle/manage_mcp.py wiki remove --agent claude
  ```

If the KB is not installed at `~/.llm-re-wiki`, the command prints the install instructions. Point it elsewhere with `LLM_RE_WIKI_ROOT`. Once registered, use the `kb_*` tools to **ground and verify** analysis (see `AGENTS.md` → Knowledge base).

---

### 4. Conversational Uninstallation
When asked to remove or uninstall an MCP server:
1. List currently configured servers: `python3 .claude/skills/mcp-lifecycle/manage_mcp.py list`.
2. Ask the user which mode they prefer:
   - **Config-Only:** Keeps downloaded binaries cached.
     ```bash
     python3 .claude/skills/mcp-lifecycle/manage_mcp.py uninstall <jadx-headless|ghidra-mcp|all>
     ```
   - **Full Purge:** Deletes configs and `.mcp-tools/<server>` directory.
     ```bash
     python3 .claude/skills/mcp-lifecycle/manage_mcp.py uninstall <jadx-headless|ghidra-mcp|all> --purge
     ```
