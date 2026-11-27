#!/bin/sh
# Installs Starholm on a Mac from the command line:
#
#   curl -fsSL https://starholm.app/install.sh | sh
#
# It downloads the newest Starholm zip, checks its SHA-256 against the published SHA256SUMS, checks the app's code
# signature, puts Starholm.app in /Applications (or ~/Applications when /Applications isn't writable) and opens it.
# It needs no administrator password. It changes nothing else on the Mac: your library, chats and settings live in
# ~/Library/Application Support/Starholm/ and are kept when the app is replaced.
#
# Apple silicon (M1 or later) and macOS 14 Sonoma or newer only.
#
# Settings, for testing:
#   STARHOLM_DOWNLOAD_BASE   where SHA256SUMS and the zips are (https only)
#   STARHOLM_INSTALL_DIR     the folder Starholm.app goes into
#   STARHOLM_NO_OPEN=1       install without opening the app
set -eu

# The download address. ops/build-site.sh fills this line in from ops/launch-config.sh; keep it on one line.
DEFAULT_DOWNLOAD_BASE="https://download.starholm.app"

APP_NAME="Starholm"
MIN_MACOS_MAJOR=14

say() { printf '%s\n' "$*"; }
fail() { printf 'Starholm install: %s\n' "$*" >&2; exit 1; }

main() {
  BASE="${STARHOLM_DOWNLOAD_BASE:-$DEFAULT_DOWNLOAD_BASE}"
  BASE="${BASE%/}"
  case "$BASE" in
    https://*) ;;
    *) fail "the download address must start with https:// (got $BASE)." ;;
  esac

  # ---------- this Mac ----------
  [ "$(uname -s)" = Darwin ] || fail "Starholm for this computer is coming soon. This installer is for Macs."
  arch="$(uname -m)"
  if [ "$arch" != arm64 ]; then
    # A Terminal running under Rosetta reports x86_64 on Apple silicon too.
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != 1 ]; then
      fail "Starholm needs a Mac with Apple silicon (M1 or later). Intel Macs are not supported."
    fi
  fi
  os_version="$(sw_vers -productVersion 2>/dev/null || echo 0)"
  os_major="${os_version%%.*}"
  case "$os_major" in ''|*[!0-9]*) os_major=0 ;; esac
  if [ "$os_major" -lt "$MIN_MACOS_MAJOR" ]; then
    fail "Starholm needs macOS $MIN_MACOS_MAJOR Sonoma or newer. This Mac has macOS $os_version. Update it in System Settings, General, Software Update."
  fi
  for tool in curl shasum ditto codesign; do
    command -v "$tool" >/dev/null 2>&1 || fail "this Mac is missing $tool, which macOS normally includes."
  done

  # ---------- where it goes ----------
  if [ -n "${STARHOLM_INSTALL_DIR:-}" ]; then
    dest="$STARHOLM_INSTALL_DIR"
  elif [ -w /Applications ]; then
    dest="/Applications"
  else
    dest="$HOME/Applications"
  fi
  mkdir -p "$dest" || fail "can't create $dest."
  [ -w "$dest" ] || fail "can't write to $dest."
  target="$dest/$APP_NAME.app"

  if pgrep -x "$APP_NAME" >/dev/null 2>&1; then
    fail "Starholm is open. Quit it (Starholm menu, Quit Starholm), then run this again."
  fi

  work="$(mktemp -d "${TMPDIR:-/tmp}/starholm-install.XXXXXX")" || fail "can't make a temporary folder."
  trap 'rm -rf "$work"' EXIT
  trap 'exit 130' INT TERM

  fetch() {  # fetch <url> <file>
    curl --proto '=https' --proto-redir '=https' --tlsv1.2 --max-redirs 3 --retry 3 --retry-delay 2 \
      -fL --progress-bar -o "$2" "$1"
  }

  # ---------- which release ----------
  say "Checking the newest Starholm release..."
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --max-redirs 3 --retry 3 -fsSL \
    -o "$work/SHA256SUMS" "$BASE/mac/SHA256SUMS" || fail "couldn't download $BASE/mac/SHA256SUMS. Check your internet connection and try again."
  # The versioned zip: "<64 hex>  Starholm-<version>.zip" (the latest copy is the same file under another name).
  line="$(grep -E "^[0-9a-f]{64}  $APP_NAME-[0-9][0-9A-Za-z.+-]*\.zip\$" "$work/SHA256SUMS" | grep -v -- '-latest\.zip$' | tail -n 1 || true)"
  [ -n "$line" ] || fail "SHA256SUMS names no Starholm zip. Try again later."
  want="${line%%  *}"
  zip_name="${line#*  }"
  version="${zip_name#"$APP_NAME"-}"
  version="${version%.zip}"

  # ---------- download and check ----------
  say "Downloading Starholm $version..."
  fetch "$BASE/mac/$zip_name" "$work/$zip_name" || fail "the download failed. Check your internet connection and try again."
  got="$(shasum -a 256 "$work/$zip_name" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || fail "the download doesn't match its published SHA-256, so it was not installed. Try again; if it keeps happening, write to hello@starholm.app."
  say "SHA-256 checked: $got"

  mkdir "$work/unpacked"
  ditto -x -k "$work/$zip_name" "$work/unpacked" || fail "couldn't unpack the download."
  new="$work/unpacked/$APP_NAME.app"
  [ -f "$new/Contents/Info.plist" ] && [ -x "$new/Contents/MacOS/$APP_NAME" ] || fail "the download doesn't hold Starholm.app."
  codesign --verify --deep --strict "$new" 2>/dev/null || fail "the app's code signature doesn't check out, so it was not installed."

  # ---------- install ----------
  if [ -e "$target" ]; then
    say "Replacing the Starholm already in $dest (your library, chats and settings are kept)."
    mv "$target" "$work/previous.app" || fail "couldn't move the old Starholm.app aside."
  fi
  if ! ditto "$new" "$target"; then
    rm -rf "$target"
    [ -e "$work/previous.app" ] && mv "$work/previous.app" "$target"
    fail "couldn't copy Starholm.app into $dest."
  fi
  say "Installed Starholm $version in $dest."

  if [ "${STARHOLM_NO_OPEN:-}" = 1 ]; then
    say "Open it from $target."
  else
    say "Opening Starholm..."
    open "$target" || fail "installed, but couldn't open it. Open Starholm from $dest."
  fi
}

# Everything runs from here, so a download cut off half way through never runs part of this script.
main "$@"
