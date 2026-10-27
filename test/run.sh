#!/usr/bin/env bash
# End-to-end test of `studio up` and `studio dash` against the mock Herdr.
# Usage: test/run.sh
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf -- "$T"' EXIT

export HOME=$T/home MOCK_STATE=$T/state MOCK_LOG=$T/log HERDR=$ROOT/test/mock-herdr
export STUDIO_DATA=$HOME/.dev-studio STUDIO_CONF=$HOME/.dev-studio/studio.conf
mkdir -p "$HOME/Developer/OMAC" "$STUDIO_DATA"
git -C "$HOME/Developer/OMAC" init -q
pass=0; fail=0
check() { # description, then a command that must succeed
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); echo "  ok   $d"
  else fail=$((fail + 1)); echo "  FAIL $d"; fi
}
has() { grep -qF -- "$1" "$MOCK_LOG"; }
creates() { grep -c 'workspace create' "$MOCK_LOG"; }

echo "HQ layout"
printf 'STUDIO_ROLE=hq\nSOUNDS=0\n' > "$STUDIO_CONF"
STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach
check "workspace labelled omac-hq"           has "--label omac-hq"
check "studio uses its own herdr session"    has "--session dev-studio workspace create"
check "architect runs opus"                  has "claude --name omac-architect --model opus"
check "messenger runs haiku"                 has "claude --name omac-messenger --model haiku"
check "dashboard pane started"               has "bin/studio dash"
check "peer settings passed"                 has "settings/peer.json"
check "role brief has project name"          grep -q "omac-builder-1" "$STUDIO_DATA/run/omac-architect.md"
check "lessons appended to brief"            grep -q "Lessons" "$STUDIO_DATA/run/omac-architect.md"
check "last project remembered"              grep -q "LAST_PROJECT=.*OMAC" "$STUDIO_CONF"
check "workspace focused"                    has "workspace focus w1"
check "architect named in herdr"             has "agent rename w1:p1 omac-architect"
check "architect keeps 60% (herdr ratio = original's share)" has "pane split w1:p1 --direction right --ratio 0.60"

echo "Herdr server not running yet"
: > "$MOCK_STATE"; : > "$MOCK_LOG"; touch "$MOCK_STATE.down"
STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach
check "starts the studio's own herdr server"  has "--session dev-studio server"
check "then builds the team"                  has "--label omac-hq"

echo "Second click reuses the running team"
"$ROOT/bin/studio" up OMAC --no-attach
check "no second workspace created"          test "$(creates)" = 1

echo "Workshop layout, 3 builders, no router installed"
"$ROOT/bin/studio" role workshop
echo "BUILDERS=3" >> "$STUDIO_CONF"
STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach   # real free memory would cap builders
check "workspace labelled omac-workshop"     has "--label omac-workshop"
check "builder-3 exists"                     has "claude --name omac-builder-3 --model sonnet --worktree builder-3"
check "builders in separate worktrees"       has "--worktree builder-1"
check "workshop messenger has unique name"   has "--name omac-shop-messenger"
check "pane splits: 2 + (3-1) = 4"           test "$(grep -c 'pane split w2:' "$MOCK_LOG")" = 4

echo "Router installed: builders let it pick the model"
mkdir -p "$T/bin"; printf '#!/bin/sh\n' > "$T/bin/jev-claude"; chmod +x "$T/bin/jev-claude"
: > "$MOCK_STATE"
PATH=$T/bin:$PATH STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach
check "builder launched through jev-claude"  has "jev-claude --name omac-builder-1 --worktree builder-1"

echo "Dashboard renders"
rc=0
out=$(env -u TERM STUDIO_DASH_ONCE=1 "$ROOT/bin/studio" dash 2>&1) || rc=$?
check "exits cleanly without a terminal" test "$rc" = 0
[ "$rc" = 0 ] || printf '%s\n' "$out"
check "shows the team"     grep -q "omac-architect" <<<"$out"
check "flags blocked agent" grep -q "NEEDS YOU" <<<"$out"
check "unnamed agent shown by pane" grep -q "w2:p1" <<<"$out"
check "board header"       grep -q "HAIKU RELAYS" <<<"$out"
check "role cards"         grep -q "RELAY" <<<"$out"
check "shows project"      grep -q "OMAC" <<<"$out"

echo "Solo layout (default role), plenty of memory"
: > "$MOCK_STATE"; : > "$MOCK_LOG"
printf 'PROJECTS_DIR=%s\nSOUNDS=0\n' "$HOME/Developer" > "$STUDIO_CONF"
STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach
check "workspace labelled omac-solo"         has "--label omac-solo"
check "architect runs opus"                  has "claude --name omac-architect --model opus"
check "two builders"                         has "--name omac-builder-2"
check "haiku relay on by default"            has "--name omac-messenger --model haiku"
check "dashboard gets 25% (herdr ratio = original's share)" has "pane split w1:p1 --direction down --ratio 0.75"

echo "Solo with little memory free: builders capped"
: > "$MOCK_STATE"; : > "$MOCK_LOG"
echo "BUILDERS=4" >> "$STUDIO_CONF"
err=$(STUDIO_FAKE_MEM_MB=3000 "$ROOT/bin/studio" up OMAC --no-attach 2>&1 >/dev/null)
# (3000 - 1536) / 600 - architect - relay = 0, floored to 1 builder
check "warns about memory"                   grep -q "Only 3000MB" <<<"$err"
check "only builder-1 started"               sh -c "grep -q omac-builder-1 '$MOCK_LOG' && ! grep -q omac-builder-2 '$MOCK_LOG'"

echo "Solo with the relay turned off"
: > "$MOCK_STATE"; : > "$MOCK_LOG"
echo "MESSENGER=0" >> "$STUDIO_CONF"
STUDIO_FAKE_MEM_MB=8000 "$ROOT/bin/studio" up OMAC --no-attach
check "no messenger"                         sh -c "! grep -q messenger '$MOCK_LOG'"
check "architect told there is no relay"     grep -q "Solo studio" "$STUDIO_DATA/run/omac-architect.md"

echo; echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
