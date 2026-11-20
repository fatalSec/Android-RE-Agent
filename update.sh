#!/usr/bin/env bash
# ==============================================================================
# ANDROID-RE-AGENT — Idempotent Update
# Refreshes the installed toolkit, re-syncs the global agent/skills, re-registers
# the MCP engines (user scope), and re-runs the health check. Preserves your
# Ghidra path, APK notes, and KB. (Consistent with the LLM-RE-WIKI lifecycle.)
# ==============================================================================

set -e

if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; CYAN="\033[0;36m"; DIM="\033[2m"; RESET="\033[0m"
else BOLD=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; DIM=""; RESET=""; fi

PY="$(command -v python3 || command -v python || true)"

step() { printf "\n%b==> %s%b\n" "$BOLD" "$1" "$RESET"; }
ok()   { printf "  %b[✓]%b %s\n" "$GREEN" "$RESET" "$1"; }
warn() { printf "  %b[!]%b %s\n" "$YELLOW" "$RESET" "$1"; }
info() { printf "  %b[-]%b %s\n" "$DIM" "$RESET" "$1"; }

resolve_home() {
    [ -n "${ANDRE_HOME:-}" ] && { echo "$ANDRE_HOME"; return; }
    local sd; sd="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
    if [ -f "$sd/AGENTS.md" ] && [ -d "$sd/.claude/skills" ]; then echo "$sd"; return; fi
    [ -d "$HOME/.android-re-agent" ] && { echo "$HOME/.android-re-agent"; return; }
    echo ""
}

HOME_DIR="$(resolve_home)"
[ -n "$PY" ] || { printf "%b[X] python3 not found.%b\n" "$RED" "$RESET"; exit 1; }
[ -n "$HOME_DIR" ] && [ -d "$HOME_DIR" ] || { printf "%b[X] Install not found. Run install.sh first.%b\n" "$RED" "$RESET"; exit 1; }
export ANDRE_HOME="$HOME_DIR"
MGR="$HOME_DIR/.claude/skills/mcp-lifecycle/manage_mcp.py"

printf "%b%b=== ANDROID-RE-AGENT Update ===%b\n" "$BOLD" "$CYAN" "$RESET"
printf "Toolkit home: %b%s%b\n" "$CYAN" "$HOME_DIR" "$RESET"

# 1. Pull new code if the home is a git clone (future-friendly); else advise -
step "Refreshing toolkit code"
if [ -d "$HOME_DIR/.git" ]; then
    git -C "$HOME_DIR" pull --ff-only && ok "Fast-forwarded." || warn "Could not fast-forward; reconcile with git by hand."
else
    info "Home is a copy, not a git clone — to pull new toolkit code, re-run install.sh from the latest repo checkout."
fi

# 2. Re-register engines (idempotent, user scope) ----------------------------
step "Re-registering MCP engines (user scope)"
"$PY" "$MGR" install jadx-headless --global || warn "jadx-headless refresh reported an error."
if "$PY" "$MGR" doctor 2>/dev/null | "$PY" -c "import sys,json;d=json.load(sys.stdin);sys.exit(0 if d['ghidra_mcp']['ghidra'].get('ghidra_dir') else 1)" 2>/dev/null; then
    "$PY" "$MGR" install ghidra-mcp --global || warn "ghidra-mcp refresh reported an error."
else
    info "No Ghidra path recorded — skipping native refresh (set it: install.sh --ghidra <dir>)."
fi

# 4. Health check ------------------------------------------------------------
step "Health check (doctor)"
"$PY" "$MGR" doctor || true

printf "\n%b%b[✓] Update complete.%b Restart your agent so changes attach (opencode -c / /mcp).\n" "$GREEN" "$BOLD" "$RESET"
printf "Your Ghidra install, APK notes, and KB are untouched.\n\n"
