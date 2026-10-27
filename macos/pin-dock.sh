#!/usr/bin/env bash
# Put "Dev Studio Claude" and "Dev Studio Codex" in the Dock, drop any stale Dev Studio
# tile, and restart the Dock ONCE (two writes with a restart between lose one of them).
set -eu
apps=("$HOME/Applications/Dev Studio Claude.app" "$HOME/Applications/Dev Studio Codex.app")
tmp=$(mktemp -t dock.XXXXXX)
trap 'rm -f -- "$tmp"' EXIT
defaults export com.apple.dock "$tmp"
python3 - "$tmp" "${apps[@]}" <<'EOF'
import plistlib, sys, os, urllib.parse
path, apps = sys.argv[1], sys.argv[2:]
with open(path, "rb") as f:
    d = plistlib.load(f)
keep = []
for a in d.get("persistent-apps", []):
    url = str(a.get("tile-data", {}).get("file-data", {}).get("_CFURLString", ""))
    p = urllib.parse.unquote(url.replace("file://", "")).rstrip("/")
    if "Dev Studio" in p and (not os.path.isdir(p) or p not in apps):
        continue   # stale or old Dev Studio tile
    if p in apps and any(p == urllib.parse.unquote(str(k.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")).replace("file://", "")).rstrip("/") for k in keep):
        continue   # duplicate
    keep.append(a)
present = {urllib.parse.unquote(str(a.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")).replace("file://", "")).rstrip("/") for a in keep}
for app in apps:
    if os.path.isdir(app) and app not in present:
        keep.append({"tile-data": {"file-data": {"_CFURLString": "file://" + urllib.parse.quote(app) + "/", "_CFURLStringType": 15}}})
d["persistent-apps"] = keep
with open(path, "wb") as f:
    plistlib.dump(d, f)
print("dock tiles:", [os.path.basename(urllib.parse.unquote(str(a["tile-data"]["file-data"]["_CFURLString"])).rstrip("/")) for a in keep if "Studio" in str(a)])
EOF
defaults import com.apple.dock "$tmp"
killall Dock
echo "Dock updated."
