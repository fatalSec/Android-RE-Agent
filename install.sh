#!/usr/bin/env bash
# ==============================================================================
# ANDROID-RE-AGENT — One-Line Installer & Bootstrapper
# Installs the agent toolkit to a home dir and registers the MCP engines at user
# scope. Run the agent by opening your CLI inside the home dir (cd <home> && opencode).
# (Conventions consistent with the LLM-RE-WIKI installer.)
#
#   curl -fsSL https://raw.githubusercontent.com/fatalSec/Android-RE-Agent/main/install.sh | bash
# ==============================================================================

set -e

if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[0;33m"
    RED="\033[0;31m"; CYAN="\033[0;36m"; DIM="\033[2m"; RESET="\033[0m"
else BOLD=""; GREEN=""; BLUE=""; YELLOW=""; RED=""; CYAN=""; DIM=""; RESET=""; fi

REPO_URL="${ANDRE_REPO:-https://github.com/fatalSec/Android-RE-Agent.git}"
SRC="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd 2>/dev/null || echo "")"
PY="$(command -v python3 || command -v python || true)"
DEFAULT_DIR="${ANDRE_HOME:-$HOME/.android-re-agent}"
ARG_DIR=""; ARG_GHIDRA="${GHIDRA_INSTALL_DIR:-}"; KB_PATH=""; ARG_YES=0

step() { printf "\n%b==> %s%b\n" "$BOLD" "$1" "$RESET"; }
ok()   { printf "  %b[✓]%b %s\n" "$GREEN" "$RESET" "$1"; }
warn() { printf "  %b[!]%b %s\n" "$YELLOW" "$RESET" "$1"; }
err()  { printf "%b[X] %s%b\n" "$RED" "$1" "$RESET" >&2; }
info() { printf "  %b[-]%b %s\n" "$DIM" "$RESET" "$1"; }

prompt_user() {  # prompt, default — reads /dev/tty when piped (curl | bash)
    local a=""
    if [ -t 0 ]; then read -r -p "$1" a
    elif [ -e /dev/tty ]; then read -r -p "$1" a < /dev/tty
    else a="$2"; fi
    echo "${a:-$2}"
}

find_py310() {
    local p c v
    for p in python3.13 python3.12 python3.11 python3.10 python3; do
        c="$(command -v "$p" 2>/dev/null)" || continue
        v="$("$c" -c 'import sys;print(1 if sys.version_info[:2]>=(3,10) else 0)' 2>/dev/null)"
        [ "$v" = "1" ] && { echo "$c"; return; }
    done
    echo ""
}

show_banner() {
    printf "%b%b" "$CYAN" "$BOLD"
    cat << "EOF"
   _           _         _    ___   ___     _                 _
  /_\  _ _  __| |_ _ ___(_)__| | | / _ \   /_\  __ _ ___ _ _| |_
 / _ \| ' \/ _` | '_/ _ \ / _` |__| (_) | / _ \/ _` / -_) ' \  _|
/_/ \_\_||_\__,_|_| \___/_\__,_|   \___/ /_/ \_\__, \___|_||_\__|
                                               |___/
EOF
    printf "%b" "$RESET"
    echo "  Android Reverse Engineering Agent — Installer"
    echo ""
}

show_help() {
    cat << EOF
ANDROID-RE-AGENT Installer

Usage: ./install.sh [options]
   or: curl -fsSL https://raw.githubusercontent.com/fatalSec/Android-RE-Agent/main/install.sh | bash -s -- [options]

Options:
  --dir <path>     Install home (default: ~/.android-re-agent; or \$ANDRE_HOME)
  --ghidra <dir>   Your existing Ghidra install (validated, never downloaded)
  --kb-path <dir>  Register an existing llm-re-wiki checkout
  -y, --yes        Non-interactive (accept defaults)
  -h, --help       Show this help
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) ARG_DIR="$2"; shift 2 ;;
        --ghidra) ARG_GHIDRA="$2"; shift 2 ;;
        --kb-path) KB_PATH="$2"; shift 2 ;;
        -y|--yes) ARG_YES=1; shift ;;
        -h|--help) show_help ;;
        *) err "Unknown option: $1"; show_help ;;
    esac
done

show_banner

# Step 1: Prerequisites -------------------------------------------------------
step "Checking prerequisites"
[ -n "$PY" ] || { err "python3 is required but not installed."; exit 1; }
ok "Python: $(command -v "$PY" 2>/dev/null || echo "$PY")"
command -v git >/dev/null 2>&1 && ok "git: $(git --version | head -n1)" || { err "git is required."; exit 1; }
command -v opencode >/dev/null 2>&1 && ok "OpenCode detected" || info "OpenCode not found (install it to run the agent)"

# Step 2: Target home ---------------------------------------------------------
IS_CHECKOUT=0
[ -n "$SRC" ] && [ -f "$SRC/AGENTS.md" ] && [ -d "$SRC/.claude/skills" ] && [ -f "$SRC/install.sh" ] && IS_CHECKOUT=1

TARGET_DIR="$ARG_DIR"
if [ -z "$TARGET_DIR" ]; then
    if [ "$ARG_YES" -eq 1 ]; then TARGET_DIR="$DEFAULT_DIR"
    else TARGET_DIR="$(prompt_user "Install home [$DEFAULT_DIR]: " "$DEFAULT_DIR")"; fi
fi
TARGET_DIR="$("$PY" -c "import os,sys;print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$TARGET_DIR")"
export ANDRE_HOME="$TARGET_DIR"
MGR="$TARGET_DIR/.claude/skills/mcp-lifecycle/manage_mcp.py"
printf "\n%bInstall home:%b %b%s%b\n" "$BOLD" "$RESET" "$CYAN" "$TARGET_DIR" "$RESET"

# Step 3: Get the toolkit into the home (copy from checkout, else clone) ------
step "Fetching toolkit"
if [ "$TARGET_DIR" = "$SRC" ]; then
    ok "Using the current checkout in place."
elif [ "$IS_CHECKOUT" -eq 1 ]; then
    mkdir -p "$TARGET_DIR"
    for item in .claude templates AGENTS.md CLAUDE.md README.md install.sh update.sh uninstall.sh .opencode .git; do
        [ -e "$SRC/$item" ] && cp -R "$SRC/$item" "$TARGET_DIR/" 2>/dev/null || true
    done
    ok "Copied toolkit from $SRC"
elif [ -d "$TARGET_DIR/.git" ]; then
    git -C "$TARGET_DIR" pull --ff-only && ok "Updated existing clone." || warn "Could not fast-forward existing clone."
else
    case "$REPO_URL" in
        *REPLACE_ME*) err "No source checkout and REPO_URL is a placeholder. Set ANDRE_REPO=<git url> or run install.sh from a checkout."; exit 1 ;;
    esac
    git clone "$REPO_URL" "$TARGET_DIR" && ok "Cloned $REPO_URL" || { err "Clone failed."; exit 1; }
fi
chmod +x "$TARGET_DIR"/*.sh 2>/dev/null || true

# Step 4: Register MCP engines at user scope ---------------------------------
step "Installing jadx-headless MCP (user scope)"
"$PY" "$MGR" install jadx-headless --global || warn "jadx-headless install reported an error."

step "Ghidra (native engine)"
GHIDRA_READY=0
if [ -n "$ARG_GHIDRA" ]; then
    "$PY" "$MGR" set-ghidra "$ARG_GHIDRA" || true; GHIDRA_READY=1
elif [ "$ARG_YES" -eq 0 ]; then
    case "$(prompt_user "Ghidra — [p]ath to an existing install, [d]ownload the latest now (~400MB, needs JDK 21+ to run), or [s]kip? [p/d/s, default p]: " "p")" in
        d|D|download|DOWNLOAD)
            if "$PY" "$MGR" download-ghidra --dir "$TARGET_DIR/ghidra"; then GHIDRA_READY=1
            else warn "Ghidra download failed — set a path later: $TARGET_DIR/install.sh --ghidra <dir>"; fi ;;
        s|S|skip|SKIP) info "Skipped native/Ghidra." ;;
        *) p="$(prompt_user "Path to your Ghidra install dir: " "")"
           if [ -n "$p" ]; then "$PY" "$MGR" set-ghidra "$p" || true; GHIDRA_READY=1; else info "No path given — skipped native/Ghidra."; fi ;;
    esac
else
    info "Skipped native/Ghidra (non-interactive). Later: $TARGET_DIR/install.sh --ghidra <dir>"
fi
if [ "$GHIDRA_READY" -eq 1 ]; then
    "$PY" "$MGR" install ghidra-mcp --global || warn "ghidra-mcp bridge install reported an error."
    warn "The bridge needs a RUNNING Ghidra with the ghidra-mcp plugin in <ghidra>/Ghidra/Extensions/ (doctor reports it)."
fi

# Step 5: llm-re-wiki KB (interactive) + ensure its MCP dep (needs py3.10+) ---
step "llm-re-wiki knowledge base (optional, recommended)"
ensure_kb_mcp() {  # root
    local root="$1" venv="$1/.kb/scripts/.venv"
    [ -d "$root/.kb/mcp" ] || return 0
    if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c 'import mcp' >/dev/null 2>&1; then ok "KB MCP dependency present."; return; fi
    local py; py="$(find_py310)"
    [ -n "$py" ] || { warn "KB MCP server needs Python 3.10+ (none found; e.g. brew install python@3.12) — register works, server won't start until fixed."; return; }
    warn "KB venv missing 'mcp' or built on <3.10 — rebuilding with $py"
    rm -rf "$venv"; "$py" -m venv "$venv" && "$venv/bin/pip" install -q -U pip >/dev/null 2>&1
    if "$venv/bin/pip" install -q -r "$root/.kb/scripts/requirements.txt" -r "$root/.kb/mcp/requirements-mcp.txt" >/dev/null 2>&1; then
        ok "KB MCP dependency installed (mcp on $("$venv/bin/python" -V 2>&1))."
    else warn "KB dependency install failed — see $root/.kb/mcp/requirements-mcp.txt"; fi
}
kb_fresh() {
    command -v curl >/dev/null 2>&1 || { warn "curl not found; install the KB manually, then: $TARGET_DIR/install.sh --kb-path <dir>"; return; }
    curl -fsSL https://raw.githubusercontent.com/fatalSec/LLM-RE-WIKI/main/install.sh | bash || { warn "KB install failed."; return; }
    ensure_kb_mcp "$HOME/.llm-re-wiki"; "$PY" "$MGR" wiki add --agent all && ok "KB registered" || warn "KB register failed."
}
if [ -n "$KB_PATH" ]; then
    KB_PATH="$("$PY" -c "import os,sys;print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$KB_PATH")"
    ensure_kb_mcp "$KB_PATH"; LLM_RE_WIKI_ROOT="$KB_PATH" "$PY" "$MGR" wiki add --agent all && ok "KB registered ($KB_PATH)" || warn "KB register failed."
elif [ "$ARG_YES" -eq 1 ]; then
    info "KB: skipped (non-interactive). Add later: $TARGET_DIR/install.sh --kb-path <dir>"
elif [ -d "$HOME/.llm-re-wiki/.kb" ]; then
    case "$(prompt_user "Register the existing llm-re-wiki KB (~/.llm-re-wiki)? [Y/n]: " "Y")" in
        n|N|no|NO) info "KB: left unregistered." ;;
        *) ensure_kb_mcp "$HOME/.llm-re-wiki"; "$PY" "$MGR" wiki add --agent all && ok "KB registered" || warn "KB register failed." ;;
    esac
else
    case "$(prompt_user "Install the llm-re-wiki knowledge base now (recommended)? [y/N]: " "N")" in
        y|Y|yes|YES) kb_fresh ;;
        *) info "KB: skipped — later: $TARGET_DIR/install.sh --kb-path <dir> (or --with it via its own installer)" ;;
    esac
fi

# Step 6: Self-check ----------------------------------------------------------
step "Self-check (doctor)"
"$PY" "$MGR" doctor || true

# Step 7: Done ----------------------------------------------------------------
printf "\n%b%b========================================================================%b\n" "$GREEN" "$BOLD" "$RESET"
printf "%b%b  ANDROID-RE-AGENT installed!%b\n" "$GREEN" "$BOLD" "$RESET"
printf "%b%b========================================================================%b\n\n" "$GREEN" "$BOLD" "$RESET"
echo "Location: $TARGET_DIR"
echo ""
echo "Run the agent by opening your CLI inside the home dir (restart first so MCPs attach):"
echo "  cd $TARGET_DIR && opencode      # or: claude"
echo ""
echo "Lifecycle:  $TARGET_DIR/update.sh   •   $TARGET_DIR/uninstall.sh"
echo ""
