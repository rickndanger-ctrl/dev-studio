#!/usr/bin/env bash
# Builds "Dev Studio Claude.app" / "Dev Studio Codex.app" in ~/Applications and pins it to the Dock.
# The app is a tiny bundle whose only job is to run `studio up --<kind>` in GUI mode.
# Usage: macos/make-app.sh claude|codex
set -eu
STUDIO_HOME=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=../lib/common.sh
. "$STUDIO_HOME/lib/common.sh"
is_macos || die "This step only runs on a Mac."

kind=${1:?usage: make-app.sh claude|codex}
case $kind in
  claude) name="Dev Studio Claude"; icon="icon-solo" ;;
  codex)  name="Dev Studio Codex";  icon="icon-hq" ;;
  *) die "kind must be claude or codex" ;;
esac
app="$HOME/Applications/$name.app"
bundle_id="dev.studio.$kind"

# The old single "Dev Studio.app" is replaced by one app per kind.
if [ -d "$HOME/Applications/Dev Studio.app" ]; then
  rm -rf -- "$HOME/Applications/Dev Studio.app"
  dock_tmp=$(mktemp -t dock.XXXXXX) || exit 1
  defaults export com.apple.dock - 2>/dev/null | python3 -c '
import plistlib, sys
d = plistlib.load(sys.stdin.buffer)
d["persistent-apps"] = [a for a in d.get("persistent-apps", []) if "Dev%20Studio.app/" not in str(a)]
sys.stdout.buffer.write(plistlib.dumps(d))' > "$dock_tmp" 2>/dev/null && defaults import com.apple.dock "$dock_tmp" 2>/dev/null
  rm -f -- "$dock_tmp"
fi

say "Building $name.app…"
rm -rf -- "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundleDisplayName</key><string>$name</string>
  <key>CFBundleIdentifier</key><string>$bundle_id</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSAppleEventsUsageDescription</key><string>Dev Studio opens your terminal with the team ready to go.</string>
</dict>
</plist>
EOF

# Dock apps start with a bare PATH, so add the usual Homebrew/npm/local locations.
cat > "$app/Contents/MacOS/launch" <<EOF
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH"
mkdir -p "\$HOME/.dev-studio/logs"
if ! STUDIO_GUI=1 "$STUDIO_HOME/bin/studio" up --$kind >>"\$HOME/.dev-studio/logs/launch.log" 2>&1; then
  osascript -e 'display alert "Dev Studio hit a snag" message "Open Terminal and run: studio doctor\\n\\nDetails are in ~/.dev-studio/logs/launch.log" as critical'
fi
EOF
chmod +x "$app/Contents/MacOS/launch"

# Icon: build every size macOS wants from the 1024px PNG.
iconset=$(mktemp -d)/AppIcon.iconset
mkdir -p "$iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$STUDIO_HOME/assets/$icon.png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$STUDIO_HOME/assets/$icon.png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
rm -rf -- "$(dirname "$iconset")"
touch "$app"   # make Finder pick up the new icon

# Pin to the Dock once.
if defaults read com.apple.dock persistent-apps 2>/dev/null | grep -qF "$name.app"; then
  ok "$name is already in your Dock."
else
  defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://${app// /%20}/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
  killall Dock
  ok "$name added to your Dock."
fi
