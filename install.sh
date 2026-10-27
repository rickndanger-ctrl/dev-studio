#!/usr/bin/env bash
# Dev Studio installer. Run once on each Mac:
#   ./install.sh            a complete, lean team on this Mac (solo, the default)
#   ./install.sh hq         two-Mac setup: the architect's office
#   ./install.sh workshop   two-Mac setup: the builders' workshop
set -eu
STUDIO_HOME=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib/common.sh
. "$STUDIO_HOME/lib/common.sh"
is_macos || die "The installer is for macOS."

ask() { # question default -> answer
  local a
  printf '%s %s[%s]%s ' "${C_CYAN}?${C_RESET} $1" "$C_DIM" "$2" "$C_RESET" >&2
  read -r a
  printf '%s' "${a:-$2}"
}

cat <<EOF

${C_PINK}${C_BOLD}  ✦ D E V   S T U D I O ✦${C_RESET}
${C_DIM}  your AI dev team, one click away${C_RESET}

EOF

# 1. Which machine is this?
role=${1:-solo}
case $role in solo|hq|workshop) ;; *) die "Role must be solo, hq or workshop." ;; esac

# 2. Tools.
command -v brew >/dev/null 2>&1 || die "Homebrew is needed first. Install it from https://brew.sh then run this again."
for tool in herdr jq; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool is installed"
  else say "Installing $tool…"; brew install "$tool"; fi
done
command -v claude >/dev/null 2>&1 || warn "Claude Code isn't installed. Get it from https://code.claude.com/docs/en/setup"

# 3. Settings.
load_config
projects=$PROJECTS_DIR
for d in "$HOME/Developer" "$HOME/Projects" "$HOME/code" "$HOME/dev" "$HOME/GitHub"; do
  [ -d "$d" ] && { projects=$d; break; }
done
projects=$(ask "Where do your project folders live?" "$projects")
case $projects in "~"*) projects="$HOME${projects#\~}" ;; esac
set_config STUDIO_ROLE "$role"
set_config PROJECTS_DIR "$projects"
if [ "$role" != hq ]; then
  set_config BUILDERS "$(ask "How many builders? (fewer = less memory)" "$BUILDERS")"
fi
ok "Settings saved to $STUDIO_CONF"

# 4. The `studio` command.
bindir=/opt/homebrew/bin
[ -w "$bindir" ] || { bindir=$HOME/.local/bin; mkdir -p "$bindir"; }
ln -sf "$STUDIO_HOME/bin/studio" "$bindir/studio"
ok "The 'studio' command is ready ($bindir/studio)"

# 5. Optional router.
if ! command -v "${ROUTER_CMD:-jev-claude}" >/dev/null 2>&1; then
  say "Optional: jev-router lets each builder pick the cheapest model that can handle each turn."
  say "Install later with: npm install -g jev-router  (needs a JEV_API_KEY in ~/.jev-router.env)"
fi

# 6. Connect the workshop to HQ so one Herdr window shows both machines.
if [ "$role" = hq ]; then
  host=$(ask "Workshop Mac's SSH name to connect now (blank to skip, e.g. mini-two.local)" "")
  if [ -n "$host" ]; then
    if herdr machine add "$host"; then
      set_config WORKSHOP_MACHINE "$host"
      ok "Connected to the workshop at $host"
    else
      warn "Couldn't connect. On the workshop Mac turn on System Settings › General › Sharing › Remote Login, then run: herdr machine add $host"
    fi
  fi
fi

# 7. Dock app.
"$STUDIO_HOME/macos/make-app.sh" "$role"

echo
"$STUDIO_HOME/bin/studio" doctor || true
cat <<EOF

${C_PINK}${C_BOLD}All set.${C_RESET} Click ${C_BOLD}$(case $role in solo) echo "Dev Studio" ;; hq) echo "Studio HQ" ;; *) echo "Studio Workshop" ;; esac)${C_RESET} in your Dock.
The first time, macOS will ask to let it control your terminal. Click OK.
EOF
