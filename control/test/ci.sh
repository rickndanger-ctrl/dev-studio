#!/usr/bin/env bash
# CI gate for re-studio. Runs every check, prints each result, and exits 0 only if all passed.
#   1. bash -n on every bash script: bin/*, install.sh, test/*.sh
#   2. py_compile on every Python file: *.py and Python scripts in bin/
#   2b. shellcheck on the bash scripts, only when installed (otherwise SKIP)
#   3. unit tests: python3 -m unittest discover -s test -p 'test_*.py'
#   4. screenshot of the viewer, only when chromium and node 22+ exist (otherwise SKIP)
#   5. privacy scan of the tracked files (test/privacy.sh)
# Usage: bash test/ci.sh          (run from anywhere; every path is taken from this script's own folder)
# Works when this repo sits inside another git repo (e.g. as control/ in the public package): file lists are
# scoped to this folder, so only this part is checked. RE_STUDIO_CI_NO_SHOT=1 skips the screenshot.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(dirname "$HERE")"
cd "$REPO" || exit 1
export RE_STUDIO_CI=1     # lets tests know they run inside ci.sh (the nested-layout test must not call ci.sh again)

fail=0
pass_count=0
report() { # report <status> <name> [detail]
  printf '%-5s %s%s\n' "$1" "$2" "${3:+ ($3)}"
  case "$1" in
    PASS) pass_count=$((pass_count + 1)) ;;
    FAIL) fail=1 ;;
  esac
}

# Files git knows about: tracked, plus new files that are not ignored (so a check run before commit sees them).
# git ls-files lists paths relative to, and only under, the current folder, so inside an outer repo this is scoped to
# this part. Outside any git repo it falls back to find.
tracked() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files --cached --others --exclude-standard -- "$@"
  else
    for pat in "$@"; do find . -path ./.git -prune -o -type f -path "./${pat#./}" -print -o -type f -path "./${pat#./}/*" -print; done | sed 's|^\./||'
  fi
}

is_bash_script() { head -1 "$1" 2>/dev/null | grep -qE '^#!.*\bbash\b'; }
is_python_script() { head -1 "$1" 2>/dev/null | grep -qE '^#!.*\bpython3?\b'; }

# 1. bash syntax -----------------------------------------------------------
bash_files=()
while IFS= read -r f; do
  case "$f" in
    bin/*|install.sh|test/*.sh) is_bash_script "$f" && bash_files+=("$f") ;;
  esac
done < <(tracked bin install.sh 'test/*.sh')
if [ "${#bash_files[@]}" -eq 0 ]; then
  report FAIL "bash -n: no bash scripts found"
fi
bash_bad=0
for f in "${bash_files[@]}"; do
  if out="$(bash -n "$f" 2>&1)"; then :; else
    bash_bad=1
    printf '%s\n' "$out" >&2
  fi
done
if [ "$bash_bad" -eq 0 ]; then
  report PASS "bash -n (${#bash_files[@]} scripts)"
else
  report FAIL "bash -n" "see errors above"
fi

# 1b. shellcheck (optional: SKIP when it is not installed) ------------------
if [ -f test/shellcheck.sh ]; then
  bash test/shellcheck.sh
  case $? in
    0) report PASS "shellcheck" ;;
    77) report SKIP "shellcheck" "not installed" ;;
    *) report FAIL "shellcheck" "see findings above" ;;
  esac
fi

# 2. Python compile --------------------------------------------------------
py_files=()
while IFS= read -r f; do
  case "$f" in
    *.py) py_files+=("$f") ;;
    bin/*) is_python_script "$f" && py_files+=("$f") ;;
  esac
done < <(tracked '*.py' bin)
py_tmp="$(mktemp -d)"
trap 'rm -rf "$py_tmp"' EXIT
py_bad=0
for i in "${!py_files[@]}"; do
  f="${py_files[$i]}"
  if out="$(python3 -c '
import py_compile, sys
py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)
' "$f" "$py_tmp/$i.pyc" 2>&1)"; then :; else
    py_bad=1
    printf '%s\n' "$out" >&2
  fi
done
if [ "$py_bad" -eq 0 ]; then
  report PASS "py_compile (${#py_files[@]} files)"
else
  report FAIL "py_compile" "see errors above"
fi

# 3. unit tests ------------------------------------------------------------
# PRIVACY_WORDS and PRIVACY_ALLOW are dropped for this step only: the tests set their own, and an inherited value
# would change what they check. The privacy step below still honours them.
if env -u PRIVACY_WORDS -u PRIVACY_ALLOW python3 -m unittest discover -s test -p 'test_*.py'; then
  report PASS "unit tests"
else
  report FAIL "unit tests"
fi

# 3b. privacy scan (when test/privacy.sh exists) ----------------------------
if [ -f test/privacy.sh ]; then
  if bash test/privacy.sh; then
    report PASS "privacy scan"
  else
    report FAIL "privacy scan"
  fi
else
  report SKIP "privacy scan" "test/privacy.sh not present"
fi

# 4. screenshot (optional) -------------------------------------------------
chrome="${CHROME:-}"
[ -n "$chrome" ] || chrome="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
node_major=""
if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
fi
if [ -n "${RE_STUDIO_CI_NO_SHOT:-}" ]; then
  report SKIP "screenshot" "RE_STUDIO_CI_NO_SHOT is set"
elif [ -z "$chrome" ]; then
  report SKIP "screenshot" "no chromium on PATH"
elif [ -z "$node_major" ] || [ "$node_major" -lt 22 ]; then
  report SKIP "screenshot" "needs node 22+, found ${node_major:-none}"
else
  shots="$(mktemp -d)"
  # A free port per run, so parallel CI runs do not collide. Never 8765 (the live viewer): retry if the OS gives it.
  port=8765
  while [ "$port" = 8765 ]; do
    port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  done
  if RE_STUDIO_CONFIG="$REPO/test/fixtures/ci-machines.json" CHROME="$chrome" bash "$REPO/test/shot.sh" "$shots" "$port"; then
    report PASS "screenshot" "$shots, port $port"
  else
    report FAIL "screenshot"
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "CI OK ($pass_count checks passed)"
else
  echo "CI FAILED"
fi
exit "$fail"
