#!/bin/bash
# Build "SimpliEarn Desk.app" in ~/Applications. Re-run after moving the OMS folder or changing Node.
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")" && pwd)"
STRATEGIES="$DESKTOP/../strategies"
PYTHON="$(cd "$STRATEGIES" && pwd)/.venv/bin/python"
APP="$HOME/Applications/SimpliEarn Desk.app"

[ -x "$PYTHON" ] || { echo "missing $PYTHON: set up strategies/.venv first (see strategies/README.md)"; exit 1; }
"$PYTHON" -m pip install -q pywebview nbclient

# Let any script or kernel using this environment `import oms_client`, wherever the file lives.
SITE="$("$PYTHON" -c 'import site; print(site.getsitepackages()[0])')"
(cd "$STRATEGIES" && pwd) > "$SITE/simpli_oms_strategies.pth"

# App icon (ui/icon.png for the Dock, AppIcon.icns for Finder)
[ -f "$DESKTOP/AppIcon.icns" ] || "$PYTHON" "$DESKTOP/make_icon.py"

# Finder and launchd start apps with a bare PATH, so remember where node lives (nvm installs it per user).
NODE_BIN="$(dirname "$(command -v node || true)")"
mkdir -p "$DESKTOP/data"
printf '{"node_bin": "%s"}\n' "$NODE_BIN" > "$DESKTOP/data/config.json"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/MacOS/SimpliEarnDesk" <<EOF
#!/bin/bash
cd "$DESKTOP"
exec "$PYTHON" app.py >> "$DESKTOP/data/logs/app.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/SimpliEarnDesk"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SimpliEarn Desk</string>
  <key>CFBundleDisplayName</key><string>SimpliEarn Desk</string>
  <key>CFBundleIdentifier</key><string>com.simpliearn.desk</string>
  <key>CFBundleExecutable</key><string>SimpliEarnDesk</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

if [ -f "$DESKTOP/AppIcon.icns" ]; then cp "$DESKTOP/AppIcon.icns" "$APP/Contents/Resources/"; fi
echo "Built $APP"
