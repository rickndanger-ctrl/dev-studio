# shellcheck shell=bash
# Shared helpers for Dev Studio. Written for the bash 3.2 that ships with macOS:
# no associative arrays, no mapfile, no ${var,,}.

STUDIO_HOME=${STUDIO_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
STUDIO_DATA=${STUDIO_DATA:-$HOME/.dev-studio}
STUDIO_CONF=${STUDIO_CONF:-$STUDIO_DATA/studio.conf}
HERDR=${HERDR:-herdr}
PYTHON=${PYTHON:-python3}

# Every Herdr call goes through here. The studio lives in its own named Herdr
# session (own server, own window), so a fresh client opens straight on the team
# and never shows, or touches, whatever the default Herdr session is doing.
hd() {
  if [ -n "${STUDIO_SESSION:-}" ]; then "$HERDR" --session "$STUDIO_SESSION" "$@"
  else "$HERDR" "$@"; fi
}

# The herdr command line other programs (router, board) should use for CLI calls.
herdr_cli_cmd() {
  if [ -n "${STUDIO_SESSION:-}" ]; then printf '%s --session %s' "$HERDR" "$STUDIO_SESSION"
  else printf '%s' "$HERDR"; fi
}

# The command a terminal window runs to attach to the studio's session.
# The Herdr TUI remembers which saved SSH machine it showed last (in
# ~/.local/state/herdr/client) and opens on that machine again, so a client
# would come up showing some other Mac's workspace. Give the studio's client its
# own state folder: with no saved machines there, it can only show the studio.
# Only the client gets this; the server (and so every pane's shell) keeps the
# normal environment.
herdr_attach_cmd() {
  local p
  p=$(command -v "$HERDR")
  if [ -n "${STUDIO_SESSION:-}" ]; then
    printf 'env XDG_STATE_HOME=%s %s --session %s' "$STUDIO_DATA/herdr-state" "$p" "$STUDIO_SESSION"
  else printf '%s' "$p"; fi
}

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
  STUDIO_SESSION=dev-studio   # named Herdr session for the team; blank = the default one
  STUDIO_SUPERVISOR=${STUDIO_SUPERVISOR:-launchd}   # launchd (KeepAlive) keeps the Herdr server up; nohup = plain background
  AGENT_KIND=claude           # claude | codex (the default kind for every role)
  KIND_ARCH=$AGENT_KIND       # each role's kind for this team; set by `studio up` flags or team.json
  KIND_MAIN=$AGENT_KIND
  KIND_WORKER=$AGENT_KIND     # kind of every worker, unless WORKER_KINDS lists them one by one
  WORKER_KINDS=''             # e.g. "codex claude": worker-1 codex, worker-2 claude
  WORKERS_MIXED=0             # 1 = `studio up --workers-mixed` (odd workers codex, even claude)
  ROUTER=jev                 # jev | clef | rules   (jev = TypeSafe's Jev, needs TYPESAFE_API_KEY)
  CLEF_URL=''                 # a System One-compatible endpoint for ROUTER=clef
  STUDIO_WORKSPACE=$HOME/DevStudio   # the team's own home: `studio up` with no project opens here
  PROJECTS_DIR=$HOME/Developer        # where `studio up --pick` looks for projects
  LAST_PROJECT=''
  WORKERS=2                   # delegated peer sessions (haiku)
  MB_PER_AGENT=600            # memory budget per agent session, used to cap workers
  MEM_RESERVE_MB=1536         # memory left free for macOS and your other apps
  ARCHITECT_MODEL=opus        # Claude models
  MAIN_MODEL=sonnet
  WORKER_MODEL=haiku
  CODEX_ARCHITECT_MODEL=gpt-6.1-sol   # Codex models (your ~/.codex/config.toml default)
  CODEX_MAIN_MODEL=gpt-6.1-sol
  CODEX_WORKER_MODEL=gpt-6.1-sol
  CODEX_SANDBOX=''            # codex --sandbox for codex roles: read-only | workspace-write | danger-full-access; blank = codex's default
  WEB_PORT=8790               # the board as a web page (studio web): loopback and Tailscale only
  STUDIO_NAME=$(scutil --get ComputerName 2>/dev/null || hostname -s)   # shown in the web page header
  PEER_URL=''                 # the other Mac's board, e.g. http://100.64.0.2:8787 (header link)
  PEER_NAME='the other Mac'
  BOARD_WINDOW=auto           # auto = open the board in a Chrome app window beside the team; none = don't
  TERMINAL_APP=auto           # auto picks an OMAC terminal tile when OMAC runs; or omac|ghostty|iterm|terminal
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
