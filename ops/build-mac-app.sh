#!/bin/bash
# Builds Starholm.app, the self-contained Mac app people download, with its .dmg and .zip, into dist/:
#
#   ops/build-mac-app.sh
#
# The app carries everything it runs: a relocatable Python (python-build-standalone) with the packages in
# requirements.lock, the app's code from the public file set (ops/make_public_tree.py, so only what is committed in
# HEAD and only what the public tree holds), and pdftotext and pdfinfo (Poppler, built here for macOS 14, static, with
# no Homebrew library). It needs no Homebrew, Python, terminal or checkout on the Mac that opens it. Apple silicon only,
# macOS 14 or newer. User data never goes in the bundle: ops/bundle-launcher.c sends it to
# ~/Library/Application Support/Starholm/ (or $STARHOLM_DATA_DIR).
#
# What this build Mac needs: Apple silicon, the Xcode Command Line Tools (cc, codesign, hdiutil) and the internet for
# the first build. The .dmg is laid out by ops/make-dmg.sh (dmgbuild, ops/mac-dmg-tools.lock), with no Finder scripting. Downloads are pinned by SHA-256 and kept in dist/.cache/, so later builds reuse them; Poppler is
# compiled once into dist/.cache/ too.
#
# Signing: ad hoc by default (codesign --sign -), which is what an unsigned download carries. To sign for real later,
# set STARHOLM_SIGN_IDENTITY to the "Developer ID Application: ..." identity: every Mach-O file is then signed inside
# out with the hardened runtime, a secure timestamp and ops/mac-entitlements.plist, and the .dmg is signed too.
# Notarizing (xcrun notarytool submit dist/Starholm-<version>.dmg --wait, then xcrun stapler staple) comes after.
#
# Settings: STARHOLM_VERSION is the version in Info.plist and the file names. It defaults to APP_VERSION in
# upgrade/appupdate.py, the one place the version lives; set it only for a test build (the update flow's 1.0.3).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CODE_VERSION="$(sed -n 's/^APP_VERSION = "\([0-9.]*\)".*/\1/p' "$ROOT/upgrade/appupdate.py")"
VERSION="${STARHOLM_VERSION:-$CODE_VERSION}"
[ -n "$VERSION" ] || { echo "build-mac-app: no APP_VERSION in upgrade/appupdate.py" >&2; exit 1; }
SIGN_IDENTITY="${STARHOLM_SIGN_IDENTITY:--}"
NAME="Starholm"
BUNDLE_ID="app.starholm.mac"  # app/server.py APP_BUNDLE_IDS lists it, so Restart works in this app
MIN_MACOS="14.0"

PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_SHA256="7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540"
PY_VER="3.13"
POPPLER_VER="26.11.0"
POPPLER_URL="https://poppler.freedesktop.org/poppler-$POPPLER_VER.tar.xz"
POPPLER_SHA256="41c32680160addf5a8dd7a830e4869c01e9f6bee1abb91e44fabd1ba9d7828fb"
FREETYPE_VER="2.14.3"
FREETYPE_URL="https://download.savannah.gnu.org/releases/freetype/freetype-$FREETYPE_VER.tar.xz"
FREETYPE_SHA256="36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f"

DIST="$ROOT/dist"
CACHE="$DIST/.cache"
APP="$DIST/$NAME.app"
DMG="$DIST/$NAME-$VERSION.dmg"
ZIP="$DIST/$NAME-$VERSION.zip"
ICON_PNG="$ROOT/ops/icon/starholm-1024.png"
JOBS=4  # compile jobs: a few, so the build Mac stays cool

say() { printf '\n== %s\n' "$*"; }
fail() { echo "build-mac-app: $*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || fail "build on an Apple silicon Mac."
xcrun --find cc >/dev/null 2>&1 || fail "install the Xcode Command Line Tools: xcode-select --install"
[ -f "$ICON_PNG" ] || fail "missing $ICON_PNG"
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  echo "Note: the app is built from HEAD; uncommitted changes are not in it." >&2
fi

# A clean environment for every tool that runs Python or compiles: nothing from the builder's shell leaks in.
CLEAN_ENV=(env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 TMPDIR="${TMPDIR:-/tmp}")

fetch() {  # fetch <url> <sha256> <file in $CACHE/dl>
  local url="$1" sum="$2" out="$CACHE/dl/$3"
  mkdir -p "$CACHE/dl"
  if [ ! -f "$out" ] || [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" != "$sum" ]; then
    echo "Downloading $url"
    curl -q --proto =https --proto-redir =https --max-redirs 5 -fsSL -o "$out.part" "$url"
    mv "$out.part" "$out"
  fi
  [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" = "$sum" ] || { rm -f "$out"; fail "$3 does not match its SHA-256."; }
}

# ---------- 1. Python ----------
say "Python $PY_VER (python-build-standalone)"
fetch "$PBS_URL" "$PBS_SHA256" python.tar.gz
PBS="$CACHE/pbs-$PBS_SHA256"
if [ ! -x "$PBS/python/bin/python$PY_VER" ]; then
  rm -rf "$PBS" && mkdir -p "$PBS"
  tar -xzf "$CACHE/dl/python.tar.gz" -C "$PBS"
fi
BUILD_PY="$PBS/python/bin/python$PY_VER"  # runs the build's own Python steps; the bundle gets a fresh copy

# ---------- 2. Poppler (pdftotext, pdfinfo) for macOS 14, static ----------
POPPLER_OUT="$CACHE/poppler-$POPPLER_VER-ft$FREETYPE_VER-macos$MIN_MACOS"
if [ ! -x "$POPPLER_OUT/bin/pdftotext" ] || [ ! -x "$POPPLER_OUT/bin/pdfinfo" ]; then
  say "Poppler $POPPLER_VER with FreeType $FREETYPE_VER (compiled once, kept in dist/.cache)"
  fetch "$POPPLER_URL" "$POPPLER_SHA256" "poppler-$POPPLER_VER.tar.xz"
  fetch "$FREETYPE_URL" "$FREETYPE_SHA256" "freetype-$FREETYPE_VER.tar.xz"
  TOOLS="$CACHE/build-tools"
  if [ ! -x "$TOOLS/bin/cmake" ] || [ ! -x "$TOOLS/bin/ninja" ]; then
    rm -rf "$TOOLS"
    "${CLEAN_ENV[@]}" "$BUILD_PY" -I -m venv "$TOOLS"
    "${CLEAN_ENV[@]}" PIP_CONFIG_FILE=/dev/null "$TOOLS/bin/python" -I -m pip --isolated --disable-pip-version-check \
      install --quiet --index-url https://pypi.org/simple --require-hashes --only-binary :all: \
      -r "$ROOT/ops/mac-build-tools.lock"
  fi
  WORK="$CACHE/poppler-build"
  rm -rf "$WORK" "$POPPLER_OUT" && mkdir -p "$WORK"
  tar -xf "$CACHE/dl/freetype-$FREETYPE_VER.tar.xz" -C "$WORK"
  tar -xf "$CACHE/dl/poppler-$POPPLER_VER.tar.xz" -C "$WORK"
  CMAKE_ENV=("${CLEAN_ENV[@]}" PATH="$TOOLS/bin:/usr/bin:/bin:/usr/sbin:/sbin" MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS")
  COMMON=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64
          -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" -DBUILD_SHARED_LIBS=OFF
          -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local;/opt/local"
          -DCMAKE_FIND_FRAMEWORK=LAST -DCMAKE_INSTALL_PREFIX="$WORK/prefix")
  # FreeType with nothing optional (no PNG, HarfBuzz, Brotli or bzip2): Poppler only needs it to read fonts.
  nice -n 10 "${CMAKE_ENV[@]}" cmake -S "$WORK/freetype-$FREETYPE_VER" -B "$WORK/ft" "${COMMON[@]}" \
    -DFT_DISABLE_ZLIB=OFF -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_PNG=ON -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_BROTLI=ON
  nice -n 10 "${CMAKE_ENV[@]}" cmake --build "$WORK/ft" -j "$JOBS"
  "${CMAKE_ENV[@]}" cmake --install "$WORK/ft" >/dev/null
  # Poppler with only what text extraction needs: no image decoders, colour management, signatures, network or
  # wrappers, and the generic font setup instead of fontconfig.
  nice -n 10 "${CMAKE_ENV[@]}" cmake -S "$WORK/poppler-$POPPLER_VER" -B "$WORK/pp" "${COMMON[@]}" \
    -DCMAKE_PREFIX_PATH="$WORK/prefix" -DFONT_CONFIGURATION=generic \
    -DENABLE_UTILS=ON -DENABLE_CPP=OFF -DENABLE_GLIB=OFF -DENABLE_GOBJECT_INTROSPECTION=OFF -DENABLE_QT5=OFF \
    -DENABLE_QT6=OFF -DENABLE_BOOST=OFF -DENABLE_BROTLI=OFF -DENABLE_LIBOPENJPEG=OFF -DENABLE_LIBJPEG=OFF \
    -DENABLE_LCMS=OFF -DENABLE_LIBCURL=OFF -DENABLE_LIBTIFF=OFF -DENABLE_NSS3=OFF \
    -DENABLE_GPGME=OFF -DENABLE_HARFBUZZ=OFF -DENABLE_LIBPNG=OFF -DBUILD_MANUAL_TESTS=OFF -DBUILD_GTK_TESTS=OFF \
    -DBUILD_QT5_TESTS=OFF -DBUILD_QT6_TESTS=OFF -DBUILD_CPP_TESTS=OFF -DRUN_GPERF_IF_PRESENT=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_PNG=ON -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON -DCMAKE_DISABLE_FIND_PACKAGE_TIFF=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_Cairo=ON -DCMAKE_DISABLE_FIND_PACKAGE_Boost=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_Fontconfig=ON -DCMAKE_DISABLE_FIND_PACKAGE_GLIB=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_GTK=ON
  nice -n 10 "${CMAKE_ENV[@]}" cmake --build "$WORK/pp" -j "$JOBS" --target pdftotext pdfinfo
  mkdir -p "$POPPLER_OUT/bin" "$POPPLER_OUT/licenses"
  cp "$WORK/pp/utils/pdftotext" "$WORK/pp/utils/pdfinfo" "$POPPLER_OUT/bin/"
  strip -x "$POPPLER_OUT/bin/pdftotext" "$POPPLER_OUT/bin/pdfinfo"
  cp "$WORK/poppler-$POPPLER_VER/COPYING" "$POPPLER_OUT/licenses/poppler-COPYING"
  cp "$WORK/poppler-$POPPLER_VER/COPYING3" "$POPPLER_OUT/licenses/poppler-COPYING3"
  cp "$WORK/freetype-$FREETYPE_VER/LICENSE.TXT" "$POPPLER_OUT/licenses/freetype-LICENSE.TXT"
  cp "$WORK/freetype-$FREETYPE_VER/docs/FTL.TXT" "$POPPLER_OUT/licenses/freetype-FTL.TXT"
  rm -rf "$WORK"
fi

# ---------- 3. The bundle ----------
say "Assembling $NAME.app"
STAGE="$(mktemp -d "$DIST/.stage.XXXXXX")"
PUBLIC="$(mktemp -d "${TMPDIR:-/tmp}/starholm-public.XXXXXX")"  # make_public_tree.py wants a folder outside the repo
trap 'rm -rf "$STAGE" "$PUBLIC"' EXIT
NEW="$STAGE/$NAME.app"
RES="$NEW/Contents/Resources"
mkdir -p "$NEW/Contents/MacOS" "$RES"

# The app's code: the public file set from HEAD, then only what runs (no tests, docs, build tools or README).
"${CLEAN_ENV[@]}" "$BUILD_PY" -I "$ROOT/ops/make_public_tree.py" "$PUBLIC" >/dev/null
mkdir "$RES/starholm"
for item in app library maps upgrade countries guides core.py chat.py memory.py emergency.py family_plan.py \
            recommended-models.json LICENSE THIRD_PARTY_NOTICES.md SOURCES.md; do
  [ -e "$PUBLIC/$item" ] || fail "the public tree has no $item"
  cp -R "$PUBLIC/$item" "$RES/starholm/"
done
rm -f "$RES/starholm/countries/validate.py" "$RES/starholm/library/make_free_sources.py"

# Python: a fresh copy of the runtime, then the locked packages installed into it the same way requirements.lock's
# INSTALL block does for a venv (hashes required, wheels only except proxy-tools, nothing from outside settings).
cp -R "$PBS/python" "$RES/python"
PY="$RES/python/bin/python$PY_VER"
PIP=("${CLEAN_ENV[@]}" PIP_CONFIG_FILE=/dev/null "$PY" -I -m pip --isolated --disable-pip-version-check install
     --quiet --no-warn-script-location --index-url https://pypi.org/simple --require-hashes --only-binary :all:)
"${PIP[@]}" -r "$ROOT/requirements-build.lock"
"${PIP[@]}" --no-binary proxy-tools --no-build-isolation --check-build-dependencies -r "$ROOT/requirements.lock"
"${CLEAN_ENV[@]}" "$PY" -I -c "import webview, objc, lxml, libzim, pypdf, trafilatura, truststore, cryptography"

# Take out what the app never runs: the installers, Tk, the test suite, headers and the extra commands, and the
# _dbm module, which has GNU gdbm (GPL-3.0) built in (dbm falls back to its SQLite and pure-Python backends).
SP="$RES/python/lib/python$PY_VER/site-packages"
rm -rf "$SP"/pip "$SP"/pip-* "$SP"/setuptools "$SP"/setuptools-* "$SP"/_distutils_hack "$SP"/distutils-precedence.pth \
       "$SP"/pkg_resources "$SP"/wheel "$SP"/wheel-*
STD="$RES/python/lib/python$PY_VER"
rm -rf "$STD"/test "$STD"/idlelib "$STD"/tkinter "$STD"/turtledemo "$STD"/ensurepip "$STD"/lib-dynload/_tkinter.* \
       "$STD"/lib-dynload/_dbm.* "$STD"/lib-dynload/_gdbm.* "$STD"/turtle.py \
       "$RES/python/lib"/tcl* "$RES/python/lib"/tk* "$RES/python/lib"/itcl* "$RES/python/lib"/thread* \
       "$RES/python/lib"/libtcl* "$RES/python/lib/pkgconfig" "$RES/python/include" "$RES/python/share"
find "$RES/python/bin" -mindepth 1 ! -name "python$PY_VER" -delete
find "$RES" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$RES" \( -name '*.pyc' -o -name '.DS_Store' \) -delete

# Bytecode, compiled now so the app never writes into its bundle: unchecked-hash .pyc files are used as they are,
# never rechecked against the source and never rewritten, by the app and by the child processes it starts with -I.
# A few third-party files are templates or Python 2 examples that don't compile; those stay as source only.
"${CLEAN_ENV[@]}" "$PY" -I -m compileall -q -f -j "$JOBS" --invalidation-mode unchecked-hash \
  "$RES/python/lib/python$PY_VER" "$RES/starholm" >/dev/null || true

# Poppler.
mkdir -p "$RES/poppler"
cp -R "$POPPLER_OUT/bin" "$POPPLER_OUT/licenses" "$RES/poppler/"
"$RES/poppler/bin/pdftotext" -v >/dev/null 2>&1 || fail "the bundled pdftotext doesn't run"

# The launcher, linked against the bundled libpython, found through an rpath inside the bundle.
read -r -a PY_CFLAGS <<< "-I$PBS/python/include/python$PY_VER"
xcrun cc -O2 -arch arm64 -mmacosx-version-min="$MIN_MACOS" "${PY_CFLAGS[@]}" -o "$NEW/Contents/MacOS/$NAME" \
  "$ROOT/ops/bundle-launcher.c" -L"$RES/python/lib" -lpython"$PY_VER" \
  -Wl,-rpath,@executable_path/../Resources/python/lib

# The icon: the standard iconset sizes from the 1024 px PNG.
ICONSET="$STAGE/AppIcon.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"

cat > "$NEW/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.reference</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT License. See Contents/Resources/starholm/LICENSE.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
plutil -lint "$NEW/Contents/Info.plist" >/dev/null

# ---------- 4. Checks: Apple silicon only, macOS 14, nothing pointing outside the bundle ----------
say "Checking every Mach-O file"
MACHO_LIST="$STAGE/macho.txt"
find "$NEW" -type f -print0 | while IFS= read -r -d '' f; do
  case "$(file -b "$f")" in *Mach-O*) printf '%s\n' "$f" ;; esac
done > "$MACHO_LIST"
problems=0
while IFS= read -r f; do
  archs="$(lipo -archs "$f" 2>/dev/null || true)"
  if [ "$archs" != arm64 ]; then
    case " $archs " in
      *" arm64 "*) lipo -thin arm64 "$f" -output "$f.thin" && mv "$f.thin" "$f" ;;  # universal: keep arm64 only
      *) echo "not arm64: $f ($archs)"; problems=1 ;;
    esac
  fi
  minos="$(vtool -show-build "$f" 2>/dev/null | awk '/minos/ {print $2; exit}')"
  if [ -n "$minos" ] && [ "$(printf '%s\n%s\n' "$minos" "$MIN_MACOS" | sort -V | tail -n 1)" != "$MIN_MACOS" ]; then
    echo "needs macOS $minos: $f"; problems=1
  fi
  if otool -L "$f" | tail -n +2 | awk '{print $1}' | grep -E '^/(opt|usr/local|Users|private|tmp)/' ; then
    echo "links outside the bundle: $f"; problems=1
  fi
done < "$MACHO_LIST"
[ "$problems" = 0 ] || fail "some Mach-O files can't ship (above)."
if grep -rlI -e "$HOME" -e "$ROOT" "$NEW" | head -5 | grep . ; then
  fail "the files above hold a path from this build Mac."
fi

# ---------- 5. Signing, inside out ----------
say "Signing (${SIGN_IDENTITY/#-/ad hoc})"
SIGN=(codesign --force --sign "$SIGN_IDENTITY")
if [ "$SIGN_IDENTITY" != "-" ]; then
  SIGN+=(--options runtime --timestamp --entitlements "$ROOT/ops/mac-entitlements.plist")
fi
# Deepest paths first, so each file is signed before whatever holds it; the main executable goes last, with the bundle.
grep -v "/Contents/MacOS/$NAME\$" "$MACHO_LIST" | awk '{print length, $0}' | sort -rn | cut -d' ' -f2- |
  while IFS= read -r f; do "${SIGN[@]}" "$f" 2>/dev/null; done
"${SIGN[@]}" "$NEW"
codesign --verify --deep --strict "$NEW"

# ---------- 6. Into dist/, with the .dmg and the .zip ----------
say "Packaging"
rm -rf "$APP" "$DMG" "$ZIP"
mv "$NEW" "$APP"
# The .dmg window: Starholm left, Applications right, the background from ops/dmg/ (ops/make-dmg.sh checks it).
STARHOLM_DMG_PYTHON="$BUILD_PY" STARHOLM_SIGN_IDENTITY="$SIGN_IDENTITY" "$ROOT/ops/make-dmg.sh" "$APP" "$DMG"
ditto -c -k --keepParent "$APP" "$ZIP"

say "Done"
echo "App: $APP ($(du -sh "$APP" | cut -f1))"
for f in "$DMG" "$ZIP"; do
  echo "$(basename "$f"): $(stat -f %z "$f") bytes, SHA-256 $(shasum -a 256 "$f" | cut -d' ' -f1)"
done
