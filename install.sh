#!/usr/bin/env bash
# Dev Studio installer. Run once on each Mac:
#   ./install.sh                 asks a couple of questions
#   STUDIO_YES=1 ./install.sh    takes the defaults (for installs over SSH)
set -eu
STUDIO_HOME=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib/common.sh
. "$STUDIO_HOME/lib/common.sh"
is_macos || die "The installer is for macOS."

ask() { # question default -> answer
  local a=''
  if [ "${STUDIO_YES:-0}" != 1 ] && [ -t 0 ]; then
    printf '%s %s[%s]%s ' "${C_CYAN}?${C_RESET} $1" "$C_DIM" "$2" "$C_RESET" >&2
    read -r a
  fi
  printf '%s' "${a:-$2}"
}

cat <<EOF

${C_PINK}${C_BOLD}  ✦ D E V   S T U D I O ✦${C_RESET}
${C_DIM}  your AI dev team, one click away${C_RESET}

EOF

# 1. Tools. Herdr and jq come from Homebrew; Claude Code / Codex you install yourself.
command -v brew >/dev/null 2>&1 || warn "Homebrew isn't installed, so I can't install anything missing. https://brew.sh"
for tool in herdr jq; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool is installed"
  elif command -v brew >/dev/null 2>&1; then say "Installing $tool…"; brew install "$tool"
  else die "$tool is missing and there is no Homebrew to install it."; fi
done
command -v claude >/dev/null 2>&1 || warn "Claude Code isn't installed. Get it from https://code.claude.com/docs/en/setup"
command -v codex  >/dev/null 2>&1 || say "Codex isn't installed (only needed for 'studio up --codex')."
command -v python3 >/dev/null 2>&1 || die "python3 is missing (the router and board need it): xcode-select --install"

# 2. Settings.
load_config
projects=$PROJECTS_DIR
for d in "$HOME/Developer" "$HOME/Projects" "$HOME/code" "$HOME/dev" "$HOME/GitHub"; do
  [ -d "$d" ] && { projects=$d; break; }
done
projects=$(ask "Where do your project folders live?" "$projects")
case $projects in "~"*) projects="$HOME${projects#\~}" ;; esac
mkdir -p "$projects"
set_config PROJECTS_DIR "$projects"
set_config WORKERS "$(ask "How many workers? (fewer = less memory)" "$WORKERS")"
set_config ROUTER "$(ask "Message router: jev, clef or rules?" "$ROUTER")"
ok "Settings saved to $STUDIO_CONF"

# 3. The `studio` command.
bindir=/opt/homebrew/bin
[ -w "$bindir" ] || { bindir=$HOME/.local/bin; mkdir -p "$bindir"; }
ln -sf "$STUDIO_HOME/bin/studio" "$bindir/studio"
ok "The 'studio' command is ready ($bindir/studio)"

# 4. The router's key.
if [ -z "${TYPESAFE_API_KEY:-}" ] && ! grep -qs 'TYPESAFE_API_KEY' "$HOME/.zshrc"; then
  warn "No TYPESAFE_API_KEY in ~/.zshrc: the JEV router will fall back to plain rules until you add one."
fi

# 5. Dock app.
"$STUDIO_HOME/macos/make-app.sh" claude
"$STUDIO_HOME/macos/make-app.sh" codex
"$STUDIO_HOME/macos/pin-dock.sh"

echo
"$STUDIO_HOME/bin/studio" doctor || true
cat <<EOF

${C_PINK}${C_BOLD}All set.${C_RESET} Click ${C_BOLD}Dev Studio Claude${C_RESET} (or Codex) in your Dock.
The first time, macOS will ask to let it control your terminal. Click OK.
EOF
