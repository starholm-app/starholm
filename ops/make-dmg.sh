#!/bin/bash
# Makes the drag-to-install .dmg from a built Starholm.app, then checks its window layout:
#
#   ops/make-dmg.sh <Starholm.app> <out.dmg>
#   ops/make-dmg.sh --check <Starholm.dmg>      only check an existing .dmg's layout
#
# ops/build-mac-app.sh and ops/release-mac.sh call it. The window opens at 660 x 400 points with no toolbar or sidebar,
# Starholm on the left, the Applications alias on the right, and the background from ops/dmg/ (an arrow and "Drag
# Starholm to Applications", Retina-ready). dmgbuild (ops/mac-dmg-tools.lock, pinned with hashes) writes the
# .DS_Store directly and builds the image with diskutil and hdiutil, so nothing scripts Finder and no permission prompt
# appears. ops/dmg/check-layout.py then reads the settings back from the finished image.
#
# Python: dmgbuild needs 3.11 or newer. It runs on $STARHOLM_DMG_PYTHON, which ops/build-mac-app.sh sets to its pinned
# python-build-standalone; on its own, this script uses that Python from dist/.cache/ if a build has fetched it.
# Signing: with STARHOLM_SIGN_IDENTITY set to a real identity, the .dmg is signed too.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CACHE="$ROOT/dist/.cache"
LOCK="$ROOT/ops/mac-dmg-tools.lock"
fail() { echo "make-dmg: $*" >&2; exit 1; }

[ $# -eq 2 ] || { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
CHECK_ONLY=0
if [ "$1" = --check ]; then
  CHECK_ONLY=1
  OUT="$2"
  [ -f "$OUT" ] || fail "no .dmg at $OUT"
else
  APP="$(cd "$1" && pwd -P)" || fail "no app at $1"
  OUT="$2"
  [ -f "$APP/Contents/Info.plist" ] || fail "$APP is not an app bundle."
  [ "$(basename "$APP")" = Starholm.app ] || fail "the app must be named Starholm.app (ops/dmg/settings.py places it by name)."
fi

PY="${STARHOLM_DMG_PYTHON:-}"
if [ -z "$PY" ]; then
  PY="$(find "$CACHE" -maxdepth 4 -path '*/pbs-*/python/bin/python3.1[1-9]' -type f 2>/dev/null | sort | tail -n 1)"
fi
[ -n "$PY" ] && [ -x "$PY" ] || fail "no Python 3.11+ for dmgbuild: set STARHOLM_DMG_PYTHON, or run ops/build-mac-app.sh once."

CLEAN_ENV=(env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 TMPDIR="${TMPDIR:-/tmp}")
TOOLS="$CACHE/dmg-tools-$(shasum -a 256 "$LOCK" | cut -c1-16)"
if [ ! -x "$TOOLS/bin/dmgbuild" ]; then
  rm -rf "$TOOLS"
  mkdir -p "$CACHE"
  "${CLEAN_ENV[@]}" "$PY" -I -m venv "$TOOLS"
  "${CLEAN_ENV[@]}" PIP_CONFIG_FILE=/dev/null "$TOOLS/bin/python" -I -m pip --isolated --disable-pip-version-check \
    install --quiet --index-url https://pypi.org/simple --require-hashes --only-binary :all: -r "$LOCK"
fi

check() {
  "${CLEAN_ENV[@]}" "$TOOLS/bin/python" -I "$ROOT/ops/dmg/check-layout.py" "$OUT" >/dev/null \
    || { "${CLEAN_ENV[@]}" "$TOOLS/bin/python" -I "$ROOT/ops/dmg/check-layout.py" "$OUT" || true; fail "the .dmg's layout is off (above)."; }
}
if [ "$CHECK_ONLY" = 1 ]; then check; exit 0; fi

rm -f "$OUT"
"${CLEAN_ENV[@]}" "$TOOLS/bin/python" -I -m dmgbuild -s "$ROOT/ops/dmg/settings.py" \
  -D app="$APP" -D settings_dir="$ROOT/ops/dmg" Starholm "$OUT" >/dev/null
SIGN_IDENTITY="${STARHOLM_SIGN_IDENTITY:--}"
[ "$SIGN_IDENTITY" = "-" ] || codesign --force --sign "$SIGN_IDENTITY" --timestamp "$OUT"
hdiutil verify -quiet "$OUT" || fail "$OUT doesn't verify."
check
