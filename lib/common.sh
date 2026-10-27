# shellcheck shell=bash
# Shared helpers for Dev Studio. Written for the bash 3.2 that ships with macOS:
# no associative arrays, no mapfile, no ${var,,}.

STUDIO_HOME=${STUDIO_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
STUDIO_DATA=${STUDIO_DATA:-$HOME/.dev-studio}
STUDIO_CONF=${STUDIO_CONF:-$STUDIO_DATA/studio.conf}
HERDR=${HERDR:-herdr}

# Colors (disabled when not writing to a terminal).
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_PINK=$'\033[38;5;205m'; C_CYAN=$'\033[38;5;51m'; C_GOLD=$'\033[38;5;220m'
  C_GREEN=$'\033[38;5;84m'; C_RED=$'\033[38;5;203m'; C_PURPLE=$'\033[38;5;141m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_PINK=''; C_CYAN=''; C_GOLD=''
  C_GREEN=''; C_RED=''; C_PURPLE=''
fi

say()  { printf '%s\n' "${C_CYAN}▸${C_RESET} $*"; }
ok()   { printf '%s\n' "${C_GREEN}✔${C_RESET} $*"; }
warn() { printf '%s\n' "${C_GOLD}!${C_RESET} $*" >&2; }
die()  { printf '%s\n' "${C_RED}✖${C_RESET} $*" >&2; exit 1; }

is_macos() { [ "$(uname -s)" = "Darwin" ]; }

# Defaults, then the user's config on top.
load_config() {
  STUDIO_ROLE=solo            # solo | hq | workshop
  PROJECTS_DIR=$HOME/Developer
  LAST_PROJECT=''
  BUILDERS=2                  # builder panes (solo and workshop)
  MESSENGER=1                 # 1 = Haiku relay pane in solo mode (0 saves ~600MB)
  MB_PER_AGENT=600            # memory budget per Claude session, used to cap builders
  MEM_RESERVE_MB=1536         # memory left free for macOS and your other apps
  ROUTER_CMD=jev-claude       # used by builders when installed; blank to disable
  ARCHITECT_MODEL=opus
  BUILDER_MODEL=sonnet        # used when the router is not installed
  MESSENGER_MODEL=haiku
  WORKSHOP_MACHINE=''         # herdr machine name of the workshop, set on HQ
  SOUNDS=1
  # shellcheck source=/dev/null
  [ -f "$STUDIO_CONF" ] && . "$STUDIO_CONF"
  return 0
}

# Update one KEY=value line in the config file, adding it if missing.
set_config() {
  local key=$1 value=$2 tmp
  mkdir -p "$STUDIO_DATA"
  touch "$STUDIO_CONF"
  tmp=$(mktemp "$STUDIO_DATA/.conf.XXXXXX")
  grep -v "^$key=" "$STUDIO_CONF" > "$tmp" || true
  printf '%s=%q\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$STUDIO_CONF"
}

# Lowercase slug safe for herdr agent names: [a-z][a-z0-9_-]{0,31}
slug() {
  local s
  s=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | sed 's/--*/-/g; s/^-//; s/-$//')
  case $s in [a-z]*) ;; *) s="p$s" ;; esac
  printf '%s' "$s" | cut -c1-20
}

# Memory the Mac can hand out right now, in MB (free + reclaimable).
mem_available_mb() {
  if [ -n "${STUDIO_FAKE_MEM_MB:-}" ]; then printf '%s' "$STUDIO_FAKE_MEM_MB"; return; fi
  if is_macos; then
    vm_stat | awk -v ps="$(sysctl -n hw.pagesize)" '
      /Pages free/ {f = $3 + 0} /Pages inactive/ {i = $3 + 0}
      /Pages speculative/ {s = $3 + 0} /Pages purgeable/ {p = $3 + 0}
      END {printf "%d", (f + i + s + p) * ps / 1048576}'
  else
    awk '/MemAvailable/ {printf "%d", $2 / 1024}' /proc/meminfo
  fi
}

play_sound() {
  [ "${SOUNDS:-1}" = 1 ] && is_macos || return 0
  [ -f "/System/Library/Sounds/$1.aiff" ] && (afplay "/System/Library/Sounds/$1.aiff" >/dev/null 2>&1 &)
  return 0
}
