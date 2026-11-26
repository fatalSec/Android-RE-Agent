#!/usr/bin/env bash
# ==============================================================================
# ANDROID-RE-AGENT — Safe Uninstaller
# Removes what install.sh set up (user-scope MCP engine registrations, and optionally
# the toolkit home). It also offers to handle the optional llm-re-wiki KB: unregister it,
# or run the KB's OWN uninstaller (which backs up your notes). Flags: --kb unregisters the
# KB non-interactively; --purge wipes the toolkit home; --apkid / --solvers pip-uninstall.
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

have_tty() { [ -t 0 ] || [ -e /dev/tty ]; }

kb_root() {  # where llm-re-wiki is installed, if anywhere
    local c
    for c in "${LLM_RE_WIKI_ROOT:-}" "$HOME/.llm-re-wiki" "$HOME/llm-re-wiki"; do
        [ -n "$c" ] && [ -d "$c/.kb" ] && { echo "$c"; return; }
    done
    echo ""
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

# 2. llm-re-wiki knowledge base (optional, separate) -------------------------
KB_DIR="$(kb_root)"
KB_REG=0
grep -rqs "llm-re-wiki" "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json" "$HOME/.claude.json" 2>/dev/null && KB_REG=1
KB_ACTION="none"   # none | unregister | full

if [ "$ARG_KB" -eq 1 ]; then
    KB_ACTION="unregister"          # --kb: non-interactive, unregister only
elif have_tty && { [ -n "$KB_DIR" ] || [ "$KB_REG" -eq 1 ]; }; then
    printf "\n%b==> llm-re-wiki knowledge base (optional, separate)%b\n" "$BOLD" "$RESET"
    [ -n "$KB_DIR" ] && echo "    Installed at: $KB_DIR"
    echo "  [u] Unregister it from your agents (keep the KB and its notes)"
    [ -n "$KB_DIR" ] && echo "  [f] Run its own uninstaller at $KB_DIR (offers to back up your notes first)"
    echo "  [l] Leave it as is"
    case "$(prompt_input "Choice [u/f/l, default l]: " "l")" in
        u|U) KB_ACTION="unregister" ;;
        f|F) [ -n "$KB_DIR" ] && KB_ACTION="full" || KB_ACTION="unregister" ;;
        *)   info "Left llm-re-wiki as is." ;;
    esac
fi

if [ "$KB_ACTION" = "unregister" ] || [ "$KB_ACTION" = "full" ]; then
    if [ -f "$MGR" ]; then
        "$PY" "$MGR" wiki remove --agent all >/dev/null 2>&1 && ok "llm-re-wiki unregistered from your agents" || warn "KB unregister reported an error (it may not have been registered)."
    else
        warn "manage_mcp.py not found — skipping KB unregister."
    fi
fi
if [ "$KB_ACTION" = "full" ]; then
    if [ -x "$KB_DIR/uninstall.sh" ]; then
        printf "\n%b==> Running the llm-re-wiki uninstaller (%s)%b\n" "$BOLD" "$KB_DIR" "$RESET"
        ( cd "$KB_DIR" && ./uninstall.sh ) || warn "The llm-re-wiki uninstaller exited with an error."
        KB_REMOVED=1
    else
        warn "No uninstaller at $KB_DIR/uninstall.sh — remove the KB folder yourself if you want it gone."
    fi
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

KB_NOTE="and the llm-re-wiki vault."
[ "${KB_REMOVED:-0}" -eq 1 ] && KB_NOTE="(the llm-re-wiki KB was removed by its own uninstaller, which backs up notes first)."
cat <<EONOTE

$(printf "%b%b[✓] Uninstall complete.%b" "$GREEN" "$BOLD" "$RESET")
Left untouched (yours): your Ghidra install, APK files, per-target notes.md /
*_analysis dirs, your JDK / Android SDK, $KB_NOTE
Restart your agent so the unregistered servers drop:  opencode -c  /  /mcp
EONOTE
