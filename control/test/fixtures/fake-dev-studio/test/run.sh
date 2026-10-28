#!/usr/bin/env bash
# Check the syntax of the scripts, then the output of the command.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bash -n "$here/install.sh"
bash -n "$here/bin/studio"
out="$("$here/bin/studio")"
[ "$out" = "fake studio 0.1.0" ] || { echo "unexpected output: $out" >&2; exit 1; }
echo "run: ok"
