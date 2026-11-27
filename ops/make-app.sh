#!/bin/bash
# Builds ~/Applications/Field Guide.app, the Mac app that opens Field Guide's chat window.
#
#   ops/make-app.sh            the app's executable is a compiled launcher (ops/launcher.c) that runs Python inside
#                              the app's own process, so the Dock shows "Field Guide"
#   ops/make-app.sh --script   the executable is a small shell script that starts the venv's Python; the Dock then
#                              shows "Python", so this is only for comparison
#
# The code stays outside the bundle: the app runs app/server.py from this checkout with this checkout's .venv.
# Run it from the checkout the app should use, after the venv exists. It downloads nothing and needs no sudo.
# Ad-hoc signing only (no --deep, no xattr). Running it again replaces the app.
# SURVIVAL_LLM_APP_DEST=<folder> builds into that folder instead of ~/Applications (for test builds).
# The icon is the Starholm tile, ops/icon/starholm-1024.png (ops/icon/starholm.svg rendered), as in the downloadable app.
set -euo pipefail

MODE=launcher
case "${1:-}" in
  "") ;;
  --script) MODE=script ;;
  *) echo "Usage: ops/make-app.sh [--script]" >&2; exit 2 ;;
esac

[ -n "${HOME:-}" ] && [ -d "$HOME" ] || { echo "HOME is not set to a folder." >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV="$ROOT/.venv"
PYTHON="$VENV/bin/python"
SERVER="$ROOT/app/server.py"
ICON_PNG="$ROOT/ops/icon/starholm-1024.png"
NAME="Field Guide"
BUNDLE_ID="local.survival-llm.app"
DEST_DIR="${SURVIVAL_LLM_APP_DEST:-$HOME/Applications}"  # the env var is for test builds; the default is ~/Applications
DEST="$DEST_DIR/$NAME.app"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/app.log"

[ -f "$SERVER" ] || { echo "Missing $SERVER" >&2; exit 1; }
[ -x "$PYTHON" ] || { echo "Missing $PYTHON: create the venv first." >&2; exit 1; }
[ -f "$ICON_PNG" ] || { echo "Missing $ICON_PNG" >&2; exit 1; }
[ "$(sips -g pixelWidth "$ICON_PNG" | awk '/pixelWidth/ {print $2}')" = 1024 ] \
  || { echo "$ICON_PNG must be 1024 px wide." >&2; exit 1; }

mkdir -p "$DEST_DIR"
[ -d "$LOG_DIR" ] || mkdir -m 700 "$LOG_DIR" 2>/dev/null || true  # the app also runs without a log
STAGE="$(mktemp -d "$DEST_DIR/.survival-llm-build.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Icon: the standard iconset sizes, scaled from the 1024 px PNG.
ICONSET="$STAGE/AppIcon.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.reference</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null

EXE="$APP/Contents/MacOS/$NAME"
if [ "$MODE" = launcher ]; then
  # The headers and library of the Homebrew Python the venv was made from, found through the venv's own pyvenv.cfg.
  PYBIN="$(sed -n 's/^home = //p' "$VENV/pyvenv.cfg")"
  PYCONFIG="$(ls "$PYBIN"/python3.*-config 2>/dev/null | head -n 1 || true)"
  [ -x "$PYCONFIG" ] || { echo "No python3-config next to the venv's Python ($PYBIN)." >&2; exit 1; }
  read -r -a CFLAGS <<< "$("$PYCONFIG" --cflags)"
  read -r -a LDFLAGS <<< "$("$PYCONFIG" --ldflags --embed)"
  /usr/bin/cc -O2 "${CFLAGS[@]}" \
    -DPYTHON_EXE="\"$PYTHON\"" -DSERVER_SCRIPT="\"$SERVER\"" -DLOG_DIR="\"$LOG_DIR\"" \
    -o "$EXE" "$ROOT/ops/launcher.c" "${LDFLAGS[@]}"
else
  {
    echo '#!/bin/bash'
    echo '# Starts Field Guide: this checkout'"'"'s server with this checkout'"'"'s venv. There is no terminal, so log to a file.'
    printf 'LOG_DIR=%q\n' "$LOG_DIR"
    printf 'LOG=%q\n' "$LOG"
    echo 'umask 077'
    echo 'unset PYTHONPATH PYTHONHOME'
    echo 'export PYTHONNOUSERSITE=1'
    echo '# Log only when the log can be opened; otherwise start the app without one.'
    echo 'if { [ -d "$LOG_DIR" ] || mkdir -m 700 "$LOG_DIR"; } 2>/dev/null && : 2>/dev/null >> "$LOG"; then'
    printf '  exec %q -s %q >> "$LOG" 2>&1\n' "$PYTHON" "$SERVER"
    echo 'fi'
    printf 'exec %q -s %q\n' "$PYTHON" "$SERVER"
  } > "$EXE"
  chmod 755 "$EXE"
fi

codesign --force --sign - "$APP"
codesign --verify --strict "$APP"

# Put the new app in place before the old one goes: set the old one aside, move the new one in, and put the old one
# back if that move fails. The aside copy sits in the staging folder, which the exit trap deletes, unless the old app
# cannot be put back: then the trap is cleared so that folder, and the old app in it, stay.
ASIDE="$STAGE/previous.app"
if [ -e "$DEST" ]; then mv "$DEST" "$ASIDE"; fi
if ! mv "$APP" "$DEST"; then
  if [ ! -e "$ASIDE" ]; then
    echo "Could not move the new app into $DEST_DIR." >&2
  elif mv "$ASIDE" "$DEST"; then
    echo "Could not move the new app into $DEST_DIR; the previous app is back in place." >&2
  else
    trap - EXIT
    echo "Could not move the new app into $DEST_DIR, and the previous app could not be put back: it is kept at $ASIDE" >&2
  fi
  exit 1
fi
echo "Built $DEST ($MODE executable); log: $LOG"
