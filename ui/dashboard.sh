#!/usr/bin/env bash
# Live team board shown in the "✨ studio" pane, styled after the cross-session
# board: header strip, one card per role, the relay's session log, a status footer.
# Light on resources: one Herdr call and one git call every refresh, nothing in between.
set -u
STUDIO_HOME=${STUDIO_HOME:-$(cd "$(dirname "$0")/.." && pwd)}
# shellcheck source=../lib/common.sh
. "$STUDIO_HOME/lib/common.sh"
load_config

REFRESH=${STUDIO_REFRESH:-2}
SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
TIPS=(
  "Talk to the architect like a creative director: say what you want, not how."
  "A red light means an agent is waiting on you. Click its pane to answer."
  "Builders each work in their own copy of the project, so they never collide."
  "Ask the relay \"what's going on?\" for a plain-English status report."
  "Herdr keeps the team running even if you close this window."
  "Nothing reaches the main branch until the architect signs off."
  "Say \"use opus\" in a builder to force the big brain for a hard task."
)

if [ "$STUDIO_ROLE" = workshop ]; then
  ACCENT=$C_GOLD; TITLE="WORKSHOP"
elif [ "$STUDIO_ROLE" = solo ]; then
  ACCENT=$C_CYAN; TITLE="SOLO"
else
  ACCENT=$C_PINK; TITLE="HQ"
fi

PREV_BLOCKED=''; PREV_DONE=''
# Fit the pane: Herdr gives the board a strip of the screen, not the whole thing.
# Ask the tty directly (`tput cols` inside $( ) sees a pipe and answers 80x24).
measure() {
  local size
  size=$(stty size </dev/tty 2>/dev/null)
  LINES=${size%% *}; COLS=${size##* }
  [ "$COLS" -gt 20 ] 2>/dev/null || COLS=80; [ "$LINES" -gt 5 ] 2>/dev/null || LINES=24
  COMPACT=0; [ "$LINES" -lt 22 ] && COMPACT=1
  RULE=$(printf '%*s' $((COLS - 4)) '' | tr ' ' '─')
}
measure

banner() {
  if [ "$COLS" -lt 100 ]; then
    printf '%s\n' "${ACCENT}${C_BOLD}  ✦ DEV STUDIO · $TITLE${C_RESET}  ${C_PURPLE}OPUS ARCHITECTS · HAIKU RELAYS · SONNET EXECUTES${C_RESET}"
  else
    printf '%s\n' "${ACCENT}${C_BOLD}  ✦ DEV STUDIO · CROSS-SESSION${C_RESET}${C_DIM}  ·  $TITLE  ·  ${C_RESET}${C_PURPLE}OPUS ARCHITECTS · HAIKU RELAYS · SONNET EXECUTES${C_RESET}"
    printf '  %s■%s %s · session architect   %s■%s %s · message relay   %s■%s %s · executes fixes\n' \
      "$C_PINK" "$C_RESET" "$ARCHITECT_MODEL" "$C_GOLD" "$C_RESET" "$MESSENGER_MODEL" "$C_CYAN" "$C_RESET" "$BUILDER_MODEL"
  fi
  [ "$COMPACT" = 1 ] || printf '%s\n' "${C_DIM}  $RULE${C_RESET}"
}

project_line() {
  local dir=${LAST_PROJECT:-$PWD} branch changed
  branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null) || { printf '  📁 %s\n' "$(basename "$dir")"; return; }
  changed=$(git -C "$dir" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  printf '  📁 %s%s%s  🌿 %s  ✏️  %s changed\n' "$C_BOLD" "$(basename "$dir")" "$C_RESET" "$branch" "$changed"
}

# One line per agent: "name<TAB>status". A real herdr 0.9.3 agent looks like
# {"name":"omac-architect","pane_id":"w1:p1","agent_status":"working",...};
# `name` is only there once `studio` has named the agent.
agents() {
  hd agent list 2>/dev/null | jq -r '
    [.. | objects | select(has("pane_id") and ((.agent_status? // .status?) | type) == "string")]
    | .[] | "\(.name // .label // .pane_id)\t\(.agent_status // .status)"' 2>/dev/null
}

# Status as icon + note, like the board's little bars.
status_cell() { # frame status -> "icon note"
  case $2 in
    working) printf '%s%s%s %s▰▰▰▱▱ on it%s' "$C_GOLD" "${SPIN[$(($1 % 10))]}" "$C_RESET" "$C_GOLD" "$C_RESET" ;;
    blocked) printf '🔴 %s%sNEEDS YOU%s' "$C_RED" "$C_BOLD" "$C_RESET" ;;
    done)    printf '✅ %sfinished, take a look%s' "$C_GREEN" "$C_RESET" ;;
    idle)    printf '🟢 %s▰▰▰▰▰ ready%s' "$C_DIM" "$C_RESET" ;;
    *)       printf '⚪ %s%s%s' "$C_DIM" "$2" "$C_RESET" ;;
  esac
}

# A card: title, then every agent whose name matches the pattern.
card() { # frame list color title pattern
  local frame=$1 list=$2 color=$3 title=$4 pat=$5 rows name status
  rows=$(printf '%s\n' "$list" | awk -F'\t' -v p="$pat" '$1 ~ p')
  [ -n "$rows" ] || return 0
  [ "$COMPACT" = 1 ] || printf '\n'   # short pane: every row counts
  printf '  %s%s┌ %s %s%s\n' "$color" "$C_BOLD" "$title" "$C_RESET" "${C_DIM}${RULE:0:$((COLS - 8 - ${#title}))}${C_RESET}"
  while IFS="$(printf '\t')" read -r name status; do
    printf '  %s│%s  %-26s %s\n' "$color" "$C_RESET" "$name" "$(status_cell "$frame" "$status")"
  done <<EOF
$rows
EOF
}

team_board() { # frame agent-list
  local frame=$1 list=$2 known other
  if [ -z "$list" ]; then
    printf '\n  %s(waiting for agents to check in…)%s\n' "$C_DIM" "$C_RESET"
    return
  fi
  card "$frame" "$list" "$C_PINK" "🏛 ARCHITECT · $ARCHITECT_MODEL · audits & signs off" '-architect$'
  card "$frame" "$list" "$C_GOLD" "📣 RELAY · $MESSENGER_MODEL · routes peer messages" '-messenger$'
  card "$frame" "$list" "$C_CYAN" "🔨 BUILDERS · $BUILDER_MODEL · execute fixes" '-builder-[0-9]+$'
  known='-architect$|-messenger$|-builder-[0-9]+$'
  other=$(printf '%s\n' "$list" | awk -F'\t' -v p="$known" '$1 !~ p {print $1}' | head -1)
  [ -n "$other" ] && card "$frame" "$list" "$C_PURPLE" "⚪ OTHER SESSIONS" "^($(printf '%s\n' "$list" | awk -F'\t' -v p="$known" '$1 !~ p {print $1}' | paste -sd'|' -))\$"
}

# The relay keeps .studio/status.md in the project; show its last few lines.
session_log() {
  local f=${LAST_PROJECT:-$PWD}/.studio/status.md
  printf '\n  %s%sSESSION LOG%s %s%s%s\n' "$C_BOLD" "$C_PURPLE" "$C_RESET" "$C_DIM" "${RULE:0:48}" "$C_RESET"
  if [ -s "$f" ]; then
    # An agent writes this file: drop control characters so it can't paint the terminal.
    grep -v '^[[:space:]]*$' "$f" | tail -5 | LC_ALL=C tr -d '\000-\010\013-\037\177' \
      | cut -c1-$((COLS - 6)) | sed "s/^/  ${C_DIM}·${C_RESET} /"
  else
    printf '  %s· the relay writes .studio/status.md as the team works%s\n' "$C_DIM" "$C_RESET"
  fi
}

# Chime once when someone newly needs you or finishes. Runs in the main shell so it
# remembers what it already announced.
chime() {
  local blocked done_
  blocked=$(printf '%s\n' "$1" | awk -F'\t' '$2 == "blocked" {print $1}' | tr '\n' ' ')
  done_=$(printf '%s\n' "$1" | awk -F'\t' '$2 == "done" {print $1}' | tr '\n' ' ')
  [ -n "$blocked" ] && [ "$blocked" != "$PREV_BLOCKED" ] && play_sound Ping
  [ -n "$done_" ] && [ "$done_" != "$PREV_DONE" ] && play_sound Glass
  PREV_BLOCKED=$blocked; PREV_DONE=$done_
}

footer() { # agent-list
  local total active mem color=$C_GREEN router
  total=$(printf '%s\n' "$1" | grep -c . | tr -d ' ')
  active=$(printf '%s\n' "$1" | awk -F'\t' '$2 == "working" || $2 == "blocked"' | grep -c . | tr -d ' ')
  if [ -n "${ROUTER_CMD:-}" ] && command -v "$ROUTER_CMD" >/dev/null 2>&1; then router="${C_GREEN}jev on${C_RESET}"
  else router="${C_DIM}jev off${C_RESET}"; fi
  mem=$(mem_available_mb)
  [ "${mem:-0}" -lt "${MEM_RESERVE_MB:-1536}" ] && color=$C_RED
  [ "$COMPACT" = 1 ] || printf '\n%s\n' "${C_DIM}  $RULE${C_RESET}"
  printf '  peers %s%s/%s active%s  ·  inbound %saccept%s  ·  %s  ·  🧠 %s%s MB free%s  ·  load %s\n' \
    "$C_BOLD" "$active" "$total" "$C_RESET" "$C_GREEN" "$C_RESET" "$router" "$color" "$mem" "$C_RESET" \
    "$(uptime | sed 's/.*load averages*: //' | cut -d' ' -f1)"
  [ "$STUDIO_ROLE" = hq ] && [ -n "${WORKSHOP_MACHINE:-}" ] && printf '  🔗 workshop  %s\n' "$WORKSHOP_MACHINE"
  return 0
}

# The whole screen, built in one go so it can be drawn without flicker.
render() { # frame agent-list
  local tip=${TIPS[$(( ($1 / 15) % ${#TIPS[@]} ))]}
  banner
  project_line
  team_board "$1" "$2"
  if [ "$COMPACT" = 1 ]; then   # short pane: cards and the peers line only
    footer "$2"
    return
  fi
  session_log
  footer "$2"
  printf '  %s💡 %s%s\n' "$C_DIM" "$tip" "$C_RESET"
  printf '  %s%s · refreshing every %ss · Ctrl-C to close%s\n' "$C_DIM" "$(date '+%a %l:%M %p')" "$REFRESH" "$C_RESET"
}

trap 'tput cnorm 2>/dev/null; exit 0' INT TERM
tput civis 2>/dev/null
clear
frame=0
while :; do
  measure
  list=$(agents)
  chime "$list"
  out=$(render "$frame" "$list")
  tput cup 0 0 2>/dev/null || clear 2>/dev/null
  # Clear to the end of every line as it is redrawn, so a shorter line never
  # shows the tail of the one drawn there a frame ago.
  printf '%s\n' "$out" | awk '{ printf "%s\033[K\n", $0 }'
  tput ed 2>/dev/null
  [ "${STUDIO_DASH_ONCE:-0}" = 1 ] && break
  sleep "$REFRESH"
  frame=$((frame + 1))
done
tput cnorm 2>/dev/null || true
exit 0
