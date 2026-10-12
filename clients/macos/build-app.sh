#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ "$(uname -s)" == Darwin ]] || { echo 'Build this app on macOS.' >&2; exit 1; }
swift test
swift build -c release --arch arm64 --arch x86_64
companion_bin_dir=$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)
companion_app="$PWD/dist/Amplifier Tempo.app"
mkdir -p "$companion_app/Contents/MacOS" "$companion_app/Contents/Resources"
cp "$companion_bin_dir/AmplifierTempo" "$companion_app/Contents/MacOS/"
cp Info.plist "$companion_app/Contents/Info.plist"
# SwiftPM's generated Bundle.module accessor looks beside the executable or
# under Bundle.main.resourceURL. Preserve its resource bundle name.
cp -R "$companion_bin_dir/AmplifierCompanion_CompanionApp.bundle" "$companion_app/Contents/Resources/"
codesign --force --deep --options runtime --sign "${COMPANION_SIGNING_IDENTITY:--}" "$companion_app"
codesign --verify --deep --strict "$companion_app"
ditto -c -k --keepParent "$companion_app" "$PWD/dist/Amplifier-Tempo-macOS.zip"
echo "Built: $companion_app"
echo 'Developer preview only until Developer ID signing, notarization, and device acceptance pass.'
