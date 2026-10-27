#!/usr/bin/env bash
# Builds "Studio HQ.app" or "Studio Workshop.app" in ~/Applications and pins it to the Dock.
# The app is a tiny bundle whose only job is to run `studio up` in GUI mode.
# Usage: macos/make-app.sh hq|workshop
set -eu
STUDIO_HOME=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=../lib/common.sh
. "$STUDIO_HOME/lib/common.sh"
is_macos || die "This step only runs on a Mac."

role=${1:?usage: make-app.sh solo|hq|workshop}
case $role in
  solo)     name="Dev Studio";      icon="icon-solo" ;;
  hq)       name="Studio HQ";       icon="icon-hq" ;;
  workshop) name="Studio Workshop"; icon="icon-workshop" ;;
  *) die "role must be solo, hq or workshop" ;;
esac
app="$HOME/Applications/$name.app"
bundle_id="dev.studio.$role"

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
if ! STUDIO_GUI=1 "$STUDIO_HOME/bin/studio" up >>"\$HOME/.dev-studio/logs/launch.log" 2>&1; then
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
