#!/usr/bin/env bash
# Install re-studio for the current user. Safe to run again.
#
#   ./install.sh               check tools, set up config, render units, link scripts, daemon-reload
#   ./install.sh --enable      also enable and start the viewer and auditor services
#   ./install.sh --dry-run     print what would change, change nothing
#
# Touches only: ~/.config/re-studio/, ~/.config/systemd/user/ (our two units), ~/.local/bin/ (links),
# ~/.config/omarchy/plugins/<plugin id>/ (only when Omarchy's plugins dir exists), and this checkout's own
# git config (core.hooksPath = hooks, so the pre-push privacy scan runs).
#
# Environment:
#   RE_STUDIO_PLUGIN_ID=<id>   Omarchy bar plugin id (default studio-routing). Sets the plugin dir name,
#                              the manifest id and the QML moduleName. Use the id your shell.json loads.
#   RE_STUDIO_HOME=<dir>       install into a sandbox home instead of $HOME (for tests); skips systemctl.
#   RE_STUDIO_CONFIG=<file>    read this machine config instead of ~/.config/re-studio/machines.json.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# RE_STUDIO_HOME=<dir> installs into a sandbox home instead of $HOME (for tests). It also skips systemctl.
SANDBOX="${RE_STUDIO_HOME:-}"
INSTALL_HOME="${SANDBOX:-$HOME}"
CONFIG_DIR="$INSTALL_HOME/.config/re-studio"
UNIT_DIR="$INSTALL_HOME/.config/systemd/user"
BIN_DIR="$INSTALL_HOME/.local/bin"
UNITS=(studio-viewer.service re-audit.service)
PLUGIN_ID="${RE_STUDIO_PLUGIN_ID:-studio-routing}"
case "$PLUGIN_ID" in
  ""|*[!A-Za-z0-9._-]*) echo "error: RE_STUDIO_PLUGIN_ID may only contain letters, digits, dot, dash, underscore" >&2; exit 2 ;;
esac

DRY=0
ENABLE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --enable)  ENABLE=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
# Run a state-changing command, or only print it under --dry-run.
do_run() {
  if [ "$DRY" -eq 1 ]; then say "  [dry-run] $*"; else "$@"; fi
}

MODE=""
[ "$DRY" -eq 1 ] && MODE=" (dry run)"
say "re-studio install from $REPO$MODE"

# 1. Tools ---------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 is required (the viewer, auditor and config loader run on it)"
command -v ssh >/dev/null 2>&1 || warn "ssh not found: Mini routes will not work"
command -v herdr >/dev/null 2>&1 || warn "herdr not found: dispatch and the studio sessions need it"
say "tools: python3 ok"

# 2. Machine config (never overwrite an existing file) ----------------------
MACHINES="$CONFIG_DIR/machines.json"
if [ -e "$MACHINES" ]; then
  say "config: $MACHINES exists, left alone"
else
  say "config: create $MACHINES from config/machines.example.json"
  do_run mkdir -p "$CONFIG_DIR"
  do_run cp "$REPO/config/machines.example.json" "$MACHINES"
fi

# 3. Render systemd templates ---------------------------------------------
# @REPO@    this checkout
# @PYTHON3@ the python3 on this PATH
# @PATH@    RE_STUDIO_UNIT_PATH if set (verbatim). Otherwise the dirs of python3/herdr/claude/node,
#           then ~/.local/bin, then /usr/bin. /usr/bin and ~/.local/bin are never taken from the tool loop.
PYTHON3_BIN="$(command -v python3)"
if [ -n "${RE_STUDIO_UNIT_PATH:-}" ]; then
  UNIT_PATH="$RE_STUDIO_UNIT_PATH"
  say "unit PATH: from RE_STUDIO_UNIT_PATH"
else
  path_parts=()
  for tool in python3 herdr claude node; do
    if p="$(command -v "$tool" 2>/dev/null)"; then
      d="$(dirname "$p")"
      case "$d" in /usr/bin|"$BIN_DIR") continue ;; esac
      case " ${path_parts[*]-} " in *" $d "*) ;; *) path_parts+=("$d") ;; esac
    else
      say "  note: $tool not on PATH, not added to the unit PATH"
    fi
  done
  path_parts+=("$BIN_DIR" /usr/bin)
  UNIT_PATH="$(IFS=:; echo "${path_parts[*]}")"
fi

render() {
  python3 -c '
import sys
text = open(sys.argv[1]).read()
for key, val in (("@REPO@", sys.argv[2]), ("@PYTHON3@", sys.argv[3]), ("@PATH@", sys.argv[4]), ("@PLUGIN_ID@", sys.argv[5])):
    text = text.replace(key, val)
sys.stdout.write(text)
' "$1" "$REPO" "$PYTHON3_BIN" "$UNIT_PATH" "$PLUGIN_ID"
}

for unit in "${UNITS[@]}"; do
  src="$REPO/systemd/$unit"
  dst="$UNIT_DIR/$unit"
  [ -f "$src" ] || die "missing template: $src"
  rendered="$(render "$src")"
  if [ -f "$dst" ] && [ "$rendered" = "$(cat "$dst")" ]; then
    say "unit: $unit up to date"
  elif [ "$DRY" -eq 1 ]; then
    say "  [dry-run] render $unit -> $dst"
  else
    mkdir -p "$UNIT_DIR"
    printf '%s\n' "$rendered" > "$dst"
    say "unit: rendered $unit -> $dst"
  fi
done

# 4. Link scripts into ~/.local/bin ----------------------------------------
# mini-room.py is a script run on a Mini (by onmini), not a command for this laptop.
links=("$REPO"/bin/* "$REPO/lib/re_studio_config.py")
do_run mkdir -p "$BIN_DIR"
for f in "${links[@]}"; do
  [ -f "$f" ] || continue
  name="$(basename "$f")"
  [ "$name" = "mini-room.py" ] && continue
  dst="$BIN_DIR/$name"
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    warn "$dst exists and is not a symlink, skipped"
    continue
  fi
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$f" ]; then
    say "link: $name already linked"
    continue
  fi
  say "link: $dst -> $f"
  do_run ln -sfn "$f" "$dst"
done

# 5. Omarchy bar plugin -----------------------------------------------------
# Rendered from omarchy-plugin/ into Omarchy's plugins dir, only if that dir exists (no Omarchy, no change).
OMARCHY_PLUGINS="$INSTALL_HOME/.config/omarchy/plugins"
if [ -d "$OMARCHY_PLUGINS" ]; then
  plugin_dst="$OMARCHY_PLUGINS/$PLUGIN_ID"
  for f in "$REPO"/omarchy-plugin/*; do
    [ -f "$f" ] || continue
    name="$(basename "$f")"
    dst="$plugin_dst/$name"
    rendered="$(render "$f")"
    if [ -f "$dst" ] && [ "$rendered" = "$(cat "$dst")" ]; then
      say "plugin: $PLUGIN_ID/$name up to date"
    elif [ "$DRY" -eq 1 ]; then
      say "  [dry-run] render plugin $name -> $dst"
    else
      mkdir -p "$plugin_dst"
      printf '%s\n' "$rendered" > "$dst"
      say "plugin: rendered $name -> $dst"
    fi
  done
else
  say "plugin: no Omarchy plugins dir at $OMARCHY_PLUGINS, skipped"
fi

# 6. Git hooks --------------------------------------------------------------
# In a git checkout, point core.hooksPath at hooks/ so the pre-push privacy scan runs. Idempotent; honours --dry-run.
if [ -e "$REPO/.git" ] && command -v git >/dev/null 2>&1 && git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ "$(git -C "$REPO" config --local --get core.hooksPath || true)" = "hooks" ]; then
    say "hooks: core.hooksPath already hooks"
  else
    say "hooks: set core.hooksPath to hooks (pre-push privacy scan)"
    do_run git -C "$REPO" config --local core.hooksPath hooks
  fi
  if [ ! -f "${PRIVACY_WORDS:-$REPO/.privacy-words}" ]; then
    warn "NO .privacy-words: the pre-push scan will not check your own names. Copy .privacy-words.example to .privacy-words and fill it in."
  fi
else
  say "hooks: not a git checkout, skipped"
fi

# 7. systemd ----------------------------------------------------------------
if [ -n "$SANDBOX" ]; then
  say "systemd: sandbox install, systemctl skipped"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  do_run systemctl --user daemon-reload
  if [ "$ENABLE" -eq 1 ]; then
    for unit in "${UNITS[@]}"; do
      do_run systemctl --user enable --now "$unit"
    done
  else
    say "systemd: daemon-reloaded. Services not enabled (rerun with --enable to start them)"
  fi
else
  [ "$ENABLE" -eq 0 ] || die "--enable needs a systemd user session (none here)"
  warn "no systemd user session here: units rendered, daemon-reload skipped"
fi

if [ "$DRY" -eq 1 ]; then say "done (dry run, nothing changed)"; else say "done"; fi
