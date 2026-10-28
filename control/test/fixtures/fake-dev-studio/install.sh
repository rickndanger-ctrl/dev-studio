#!/usr/bin/env bash
# Copy bin/studio into PREFIX/bin (default: ./out).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
prefix="${1:-$here/out}"
mkdir -p "$prefix/bin"
cp "$here/bin/studio" "$prefix/bin/studio"
echo "installed $prefix/bin/studio"
