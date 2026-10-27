#!/usr/bin/env bash
# Live team dashboard shown in the "✨ studio" pane.
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
  "Ask the messenger \"what's going on?\" for a plain-English status report."
  "Herdr keeps the team running even if you close this window."
  "Nothing reaches the main branch until the architect signs off."
  "Say \"use opus\" in a builder to force the big brain for a hard task."
)

if [ "$STUDIO_ROLE" = workshop ]; then
  ACCENT=$C_GOLD; TITLE="W O R K S H O P"; MASCOT="🔨"
elif [ "$STUDIO_ROLE" = solo ]; then
  ACCENT=$C_CYAN; TITLE="S O L O   S E S S I O N"; MASCOT="🎬"
else
  ACCENT=$C_PINK; TITLE="H E A D Q U A R T E R S"; MASCOT="🏛"
fi

PREV_BLOCKED=''; PREV_DONE=''

banner() {
  printf '%s\n' "${ACCENT}${C_BOLD}  ╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸${C_RESET}"
  printf '%s\n' "${ACCENT}${C_BOLD}   ✦ D E V   S T U D I O ✦  ${C_RESET}${C_DIM}$MASCOT${C_RESET}"
  printf '%s\n' "${C_PURPLE}   $TITLE${C_RESET}"
  printf '%s\n' "${ACCENT}${C_BOLD}  ╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸${C_RESET}"
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
  "$HERDR" agent list 2>/dev/null | jq -r '
    [.. | objects | select(has("pane_id") and ((.agent_status? // .status?) | type) == "string")]
    | .[] | "\(.name // .label // .pane_id)\t\(.agent_status // .status)"' 2>/dev/null
}

team_board() { # frame agent-list
  local frame=$1 list=$2 name status icon note
  printf '\n  %s%sTHE TEAM%s\n' "$C_BOLD" "$C_CYAN" "$C_RESET"
  if [ -z "$list" ]; then
    printf '  %s(waiting for agents to check in…)%s\n' "$C_DIM" "$C_RESET"
    return
  fi
  while IFS="$(printf '\t')" read -r name status; do
    case $status in
      working) icon="${C_GOLD}${SPIN[$((frame % 10))]}${C_RESET}"; note="${C_GOLD}on it${C_RESET}" ;;
      blocked) icon="🔴"; note="${C_RED}${C_BOLD}NEEDS YOU${C_RESET}" ;;
      done)    icon="✅"; note="${C_GREEN}finished, take a look${C_RESET}" ;;
      idle)    icon="🟢"; note="${C_DIM}ready${C_RESET}" ;;
      *)       icon="⚪"; note="${C_DIM}$status${C_RESET}" ;;
    esac
    printf '  %s  %-26s %s\n' "$icon" "$name" "$note"
  done <<EOF
$list
EOF
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

extras() {
  printf '\n'
  if [ -n "${ROUTER_CMD:-}" ] && command -v "$ROUTER_CMD" >/dev/null 2>&1; then
    printf '  🧭 router  %son%s (%s picks each builder turn)\n' "$C_GREEN" "$C_RESET" "$ROUTER_CMD"
  else
    printf '  🧭 router  %soff%s (builders use %s)\n' "$C_DIM" "$C_RESET" "$BUILDER_MODEL"
  fi
  [ "$STUDIO_ROLE" = hq ] && [ -n "${WORKSHOP_MACHINE:-}" ] && printf '  🔗 workshop  %s\n' "$WORKSHOP_MACHINE"
  local mem color=$C_GREEN
  mem=$(mem_available_mb)
  [ "${mem:-0}" -lt "${MEM_RESERVE_MB:-1536}" ] && color=$C_RED
  printf '  🧠 memory  %s%s MB free%s   💻 load %s\n' "$color" "$mem" "$C_RESET" "$(uptime | sed 's/.*load averages*: //')"
}

# The whole screen, built in one go so it can be drawn without flicker.
render() { # frame agent-list
  local tip=${TIPS[$(( ($1 / 15) % ${#TIPS[@]} ))]}
  banner
  project_line
  team_board "$1" "$2"
  extras
  printf '\n  %s💡 %s%s\n' "$C_DIM" "$tip" "$C_RESET"
  printf '\n  %s%s · refreshing every %ss · Ctrl-C to close%s\n' "$C_DIM" "$(date '+%a %l:%M %p')" "$REFRESH" "$C_RESET"
}

trap 'tput cnorm 2>/dev/null; exit 0' INT TERM
tput civis 2>/dev/null
clear
frame=0
while :; do
  list=$(agents)
  chime "$list"
  out=$(render "$frame" "$list")
  tput cup 0 0 2>/dev/null || clear 2>/dev/null
  printf '%s' "$out"
  tput ed 2>/dev/null
  [ "${STUDIO_DASH_ONCE:-0}" = 1 ] && break
  sleep "$REFRESH"
  frame=$((frame + 1))
done
tput cnorm 2>/dev/null || true
exit 0
