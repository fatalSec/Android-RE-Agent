#!/usr/bin/env bash
# ==============================================================================
# ANDROID-RE-AGENT — Safe Uninstaller
# Removes what install.sh set up (user-scope MCP engine registrations, and optionally
# the toolkit home), and NOTHING of yours. (Consistent with the LLM-RE-WIKI lifecycle.)
# ==============================================================================

set -e

if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; CYAN="\033[0;36m"; DIM="\033[2m"; RESET="\033[0m"
else BOLD=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; DIM=""; RESET=""; fi

PY="$(command -v python3 || command -v python || true)"
ARG_HOME=""; ARG_PURGE=0; ARG_KB=0; ARG_APKID=0; ARG_SOLVERS=0; ARG_CHOICE=""

ok()   { printf "  %b[✓]%b %s\n" "$GREEN" "$RESET" "$1"; }
warn() { printf "  %b[!]%b %s\n" "$YELLOW" "$RESET" "$1"; }
info() { printf "  %b[-]%b %s\n" "$DIM" "$RESET" "$1"; }

prompt_input() {
    local a=""
    if [ -t 0 ]; then read -r -p "$1" a
    elif [ -e /dev/tty ]; then read -r -p "$1" a < /dev/tty
    else a="$2"; fi
    echo "${a:-$2}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --home) ARG_HOME="$2"; shift 2 ;;
        --purge) ARG_PURGE=1; ARG_CHOICE=2; shift ;;
        --kb) ARG_KB=1; shift ;;
        --apkid) ARG_APKID=1; shift ;;
        --solvers) ARG_SOLVERS=1; shift ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) warn "unknown option: $1"; exit 2 ;;
    esac
done
[ -n "$PY" ] || { printf "%b[X] python3 not found.%b\n" "$RED" "$RESET"; exit 1; }

HOME_DIR="${ARG_HOME:-${ANDRE_HOME:-$HOME/.android-re-agent}}"
HOME_DIR="$("$PY" -c "import os,sys;print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$HOME_DIR")"
export ANDRE_HOME="$HOME_DIR"
MGR="$HOME_DIR/.claude/skills/mcp-lifecycle/manage_mcp.py"

printf "%b%b=== ANDROID-RE-AGENT Safe Uninstaller ===%b\n\n" "$BOLD" "$RED" "$RESET"
printf "Toolkit home: %b%s%b\n\n" "$CYAN" "$HOME_DIR" "$RESET"

if [ -z "$ARG_CHOICE" ]; then
    echo "Choose an option:"
    echo "  1) Unregister MCP engines  (keep the toolkit home + cached binaries)"
    echo "  2) Complete wipe  (also delete the toolkit home $HOME_DIR)"
    echo "  3) Cancel"
    echo ""
    ARG_CHOICE="$(prompt_input "Enter choice [1-3, default: 3]: " "3")"
fi
case "$ARG_CHOICE" in 1|2) ;; *) echo "Cancelled."; exit 0 ;; esac
[ "$ARG_CHOICE" = "2" ] && ARG_PURGE=1

# 1. Unregister engines (user scope) -----------------------------------------
printf "\n%b==> Unregistering MCP engines (user scope)%b\n" "$BOLD" "$RESET"
if [ -f "$MGR" ]; then
    "$PY" "$MGR" uninstall jadx-headless --global $( [ "$ARG_PURGE" -eq 1 ] && echo --purge ) || true
    "$PY" "$MGR" uninstall ghidra-mcp  --global $( [ "$ARG_PURGE" -eq 1 ] && echo --purge ) || true
    ok "jadx-headless / ghidra-mcp unregistered from Claude Code & OpenCode"
else
    warn "manage_mcp.py not found under $HOME_DIR — skipping config unregister."
fi

# 2. Optional KB / pip tools -------------------------------------------------
if [ "$ARG_KB" -eq 1 ] && [ -f "$MGR" ]; then
    printf "\n%b==> Unregistering llm-re-wiki (vault & notes left intact)%b\n" "$BOLD" "$RESET"
    "$PY" "$MGR" wiki remove --agent all || warn "KB unregister reported an error."
    warn "To remove KB content, use its own uninstaller (it backs up notes): ~/.llm-re-wiki/uninstall.sh"
fi
[ "$ARG_APKID" -eq 1 ] && { printf "\n%b==> Removing APKiD%b\n" "$BOLD" "$RESET"; "$PY" -m pip uninstall -y apkid || warn "APKiD not pip-installed here."; }
[ "$ARG_SOLVERS" -eq 1 ] && { printf "\n%b==> Removing native solvers%b\n" "$BOLD" "$RESET"; "$PY" -m pip uninstall -y unicorn z3-solver || warn "solvers not pip-installed here."; }

# 4. Complete wipe -----------------------------------------------------------
if [ "$ARG_PURGE" -eq 1 ]; then
    if [ "$ARG_CHOICE" = "2" ] && [ -t 0 -o -e /dev/tty ]; then
        CONFIRM="$(prompt_input "Type DELETE to remove $HOME_DIR: " "")"
        [ "$CONFIRM" = "DELETE" ] || { warn "Not confirmed — toolkit home kept."; ARG_PURGE=0; }
    fi
    if [ "$ARG_PURGE" -eq 1 ]; then
        printf "\n%b==> Deleting toolkit home%b\n" "$BOLD" "$RESET"
        rm -rf "$HOME_DIR" && ok "removed $HOME_DIR"
    fi
fi

cat <<EONOTE

$(printf "%b%b[✓] Uninstall complete.%b" "$GREEN" "$BOLD" "$RESET")
Left untouched (yours): your Ghidra install, APK files, per-target notes.md /
*_analysis dirs, your JDK / Android SDK, and the llm-re-wiki vault.
Restart your agent so the unregistered servers drop:  opencode -c  /  /mcp
EONOTE
