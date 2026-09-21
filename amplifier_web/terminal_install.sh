#!/bin/bash
# Amplifier Terminal Setup v1 -- prepared by your authenticated Unified service.
set -euo pipefail
umask 077
if [ "$(uname -s)" != '__SYSTEM__' ]; then
  echo 'This setup file is for a different operating system. Choose your computer on the setup page.' >&2; exit 1
fi
case "$(uname -m)" in arm64|aarch64) ;; *) echo 'This release requires an ARM64 computer. Your current installation has not changed.' >&2; exit 1 ;; esac
if [ '__SYSTEM__' = Darwin ] && [ "$(sw_vers -productVersion | cut -d. -f1)" -lt 26 ]; then
  echo 'This connected-client release requires macOS 26 or later. Your current installation has not changed.' >&2; exit 1
fi
root="${AMPLIFIER_TERMINAL_HOME:-$HOME/.local/share/amplifier-terminal}"
mkdir -p "$root/versions" "$root/downloads"
chmod 700 "$root"
work="$(mktemp -d)"
candidate=''
cleanup() {
  result=$?
  rm -rf "$work"
  if [ -n "$candidate" ] && [ ! -f "$candidate/.activated" ]; then rm -rf "$candidate"; fi
  if [ "$result" != 0 ]; then echo 'Setup did not finish. Existing clients are unchanged. Correct the problem or download a new setup file and retry.' >&2; fi
}
trap cleanup EXIT
decode() { if [ '__SYSTEM__' = Darwin ]; then /usr/bin/base64 -D; else base64 --decode; fi; }
checksum() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
decode > "$work/profile.json" <<'AMPLIFIER_PROFILE'
__PROFILE_BASE64__
AMPLIFIER_PROFILE
decode > "$work/__WHEEL_NAME__" <<'AMPLIFIER_WHEEL'
__WHEEL_BASE64__
AMPLIFIER_WHEEL
decode > "$work/install.py" <<'AMPLIFIER_INSTALLER'
__INSTALLER_BASE64__
AMPLIFIER_INSTALLER
if [ "$(checksum "$work/__WHEEL_NAME__")" != '__WHEEL_SHA256__' ]; then echo 'Client verification failed.' >&2; exit 1; fi
echo '1/4 Preparing the terminal runtime…'
archive="$root/downloads/__UV_NAME__-__UV_VERSION__.tar.gz"
if [ ! -f "$archive" ] || [ "$(checksum "$archive")" != '__UV_SHA256__' ]; then
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --connect-timeout 20 --max-time 300 \
    'https://github.com/astral-sh/uv/releases/download/__UV_VERSION__/__UV_NAME__.tar.gz' -o "$work/uv.tar.gz"
  if [ "$(checksum "$work/uv.tar.gz")" != '__UV_SHA256__' ]; then echo 'Runtime bootstrap verification failed.' >&2; exit 1; fi
  mv "$work/uv.tar.gz" "$archive"
fi
tar -xzf "$archive" -C "$work"
uv="$work/__UV_NAME__/uv"
export UV_PYTHON_INSTALL_DIR="$root/python" UV_CACHE_DIR="$root/cache"
candidate="$(mktemp -d "$root/versions/client-__VERSION__-XXXXXXXX")"
"$uv" --no-config venv --python 3.13 --managed-python "$candidate"
echo '2/4 Installing the prebuilt terminal client…'
"$uv" --no-config pip install --python "$candidate/bin/python" --no-sources --only-binary :all: \
  --index-url https://pypi.org/simple "$work/__WHEEL_NAME__"
echo '3/4 Connecting to your Unified service…'
"$candidate/bin/python" "$work/install.py" "$work/profile.json" "$root" "$candidate"
echo '4/4 Ready. Open the launcher shown above whenever you want to return.'
echo 'Your conversations and tools continue running on the connected service.'
