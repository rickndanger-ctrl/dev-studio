#!/usr/bin/env bash
# Runs shellcheck (default severity) on every tracked file that has a bash shebang or a "# shellcheck shell=bash"
# line (the public lint job's regex): the same selection as the public lint workflow.
# Exit 0: clean. 1: findings. 77: shellcheck is not installed (a skip, not a failure).
# SHELLCHECK=<path> picks the binary (default: shellcheck on PATH). Run from anywhere.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
sc="${SHELLCHECK:-}"
[ -n "$sc" ] || sc="$(command -v shellcheck || true)"
if [ -z "$sc" ] || [ ! -x "$sc" ]; then
  echo "SKIP shellcheck (not installed)"
  exit 77
fi
files=()
while IFS= read -r f; do
  [ -f "$f" ] || continue
  if grep -aqE '^#!.*bash|^# shellcheck shell=bash' "$f" 2>/dev/null; then
    files+=("$f")
  fi
done < <(
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -- .
  else
    find . -path ./.git -prune -o -type f -print | sed 's|^\./||'
  fi
)
[ "${#files[@]}" -gt 0 ] || { echo "no bash files found" >&2; exit 1; }
"$sc" "${files[@]}"
