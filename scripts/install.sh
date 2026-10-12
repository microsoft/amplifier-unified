#!/usr/bin/env bash
# Install Unified for this user. Existing Amplifier CLI state is never reset.
set -Eeuo pipefail
umask 077
version=latest
verbose=false
no_open=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) [ "$#" -ge 2 ] || { echo '--version needs a release number' >&2; exit 2; }; version="${2#v}"; shift 2 ;;
    --verbose) verbose=true; shift ;;
    --no-open) no_open=true; shift ;;
    --anonymous) export AMPLIFIER_GITHUB_ACCESS=anonymous; shift ;;
    --help) echo 'Usage: install.sh [--version 0.20.101] [--verbose] [--no-open] [--anonymous]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
anonymous_downloads() {
  export AMPLIFIER_GITHUB_ACCESS=anonymous
  # Match reset.repository_environment before the Python app is installed.
  # Child Git/uv processes must not inherit a signed-in helper or SSH rewrite.
  for install_config_var in ${!GIT_CONFIG_@}; do unset "$install_config_var"; done
  unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GIT_ASKPASS SSH_ASKPASS
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0
  export GIT_CONFIG_COUNT=4
  export GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=''
  export GIT_CONFIG_KEY_1=http.extraHeader GIT_CONFIG_VALUE_1=''
  export GIT_CONFIG_KEY_2=http.cookieFile GIT_CONFIG_VALUE_2=''
  export GIT_CONFIG_KEY_3=core.askPass GIT_CONFIG_VALUE_3=''
}
if [ "${AMPLIFIER_GITHUB_ACCESS:-}" = anonymous ]; then anonymous_downloads; fi
case "$(uname -s)" in Darwin|Linux) ;; *) echo 'Use macOS, Linux, or a Linux distribution in WSL with systemd enabled.' >&2; exit 1 ;; esac
if [ "$(id -u)" = 0 ]; then echo 'Run this as your regular user, without sudo.' >&2; exit 1; fi
command -v curl >/dev/null || { echo 'Install curl, then run this command again.' >&2; exit 1; }
command -v git >/dev/null && git --version >/dev/null 2>&1 || { echo 'Git is needed for Amplifier components. On macOS, run xcode-select --install, then try again.' >&2; exit 1; }
if [ "$version" != latest ] && ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo 'Choose a stable release number, such as 0.20.101.' >&2; exit 2; fi
install_work="$(mktemp -d "${TMPDIR:-/tmp}/amplifier-install.XXXXXX")"
install_log="$(mktemp "${TMPDIR:-/tmp}/amplifier-install-log.XXXXXX")"
trap 'rm -rf "$install_work"' EXIT
trap 'echo "Installation stopped. Your CLI configuration is unchanged. Details: $install_log" >&2' ERR
run() {
  if $verbose; then "$@" 2>&1 | tee -a "$install_log"; else "$@" >>"$install_log" 2>&1; fi
}
echo 'Preparing Amplifier…'
if command -v uv >/dev/null 2>&1; then
  uv_command="$(command -v uv)"
else
  run curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$install_work/uv.sh"
  run env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$install_work/uv.sh"
  uv_command="$HOME/.local/bin/uv"
fi
release_base=https://github.com/microsoft/amplifier-unified/releases
if [ "$version" = latest ]; then
  resolved="$(curl --proto '=https' --tlsv1.2 -fsSL -o /dev/null -w '%{url_effective}' "$release_base/latest")"
  version="${resolved##*/v}"
  [[ "$resolved" = "$release_base/tag/v$version" && "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Could not determine the latest stable release.' >&2; exit 1; }
fi
wheel="amplifier_unified-${version}-py3-none-any.whl"
echo "Installing Amplifier $version…"
run curl --proto '=https' --tlsv1.2 -fsSL "$release_base/download/v$version/$wheel" -o "$install_work/$wheel"
run curl --proto '=https' --tlsv1.2 -fsSL "$release_base/download/v$version/SHA256SUMS" -o "$install_work/SHA256SUMS"
# Python and Unified live in uv-managed environments; system Python is unchanged.
run "$uv_command" python install 3.13
run "$uv_command" run --no-project --python 3.13 python -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); name=sys.argv[2]; rows=[line.split() for line in (p/"SHA256SUMS").read_text().splitlines()]; expected=next((r[0] for r in rows if len(r)==2 and r[1].lstrip("*")==name),None); actual=hashlib.sha256((p/name).read_bytes()).hexdigest(); sys.exit(0 if expected==actual else "Release checksum verification failed")' "$install_work" "$wheel"
if ! run "$uv_command" tool install --upgrade --python 3.13 "$install_work/$wheel"; then
  # A verified public release may have public Git dependencies blocked by a
  # signed-in account. Retry only installation, once, with child-only policy.
  if [ "${AMPLIFIER_GITHUB_ACCESS:-}" = anonymous ]; then exit 1; fi
  echo 'Retrying public dependencies without GitHub sign-in…'
  (anonymous_downloads
   run "$uv_command" tool install --upgrade --python 3.13 "$install_work/$wheel")
fi
tool_bin="$("$uv_command" tool dir --bin)"
echo 'Starting your service…'
if $no_open; then
  run "$tool_bin/amplifier-unified" --no-open
else
  run "$tool_bin/amplifier-unified"
fi
echo 'Amplifier is ready. Finish setup in the web app.'
# Include the actual serving URL even when browser opening is disabled/unavailable.
grep '^Open:' "$install_log" | tail -1 || true
echo "Launch again: $tool_bin/amplifier-unified"
echo "Installation log: $install_log"
