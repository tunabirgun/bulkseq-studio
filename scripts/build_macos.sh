#!/usr/bin/env bash
# Build, sign, notarize, and package BulkSeq Studio for macOS (Apple Silicon only).
#
# Pipeline: PyInstaller (produces the .app via the spec's BUNDLE() stanza, macOS only)
#           -> codesign --deep --options runtime
#           -> create-dmg
#           -> notarytool submit --wait
#           -> stapler staple
#
# Must run on macOS (codesign/notarytool/stapler are Apple tools; this repo is
# developed on Windows, so this script itself is untested end-to-end — see the
# NOT VERIFIED note at the bottom and in AGENTS-facing docs).
#
# SIGNING IS OPTIONAL: with no CODESIGN_IDENTITY set, this script builds and
# leaves an unsigned dist/BulkSeq Studio.app in place, then exits 0. This lets
# CI run the same script on every PR (no secrets available on forks) while
# still doing the full sign/notarize/dmg pipeline on pushes where secrets are
# configured. An unsigned/un-notarized .app will not open on a clean Mac
# without a Gatekeeper override — signing is what this whole script is for.
#
# Environment variables (never hardcoded, never echoed):
#   CODESIGN_IDENTITY   Required to sign. e.g. "Developer ID Application: NAME (TEAMID)"
#                        Must already be present in the local keychain (or the
#                        CI runner's temporary keychain).
#   APPLE_TEAM_ID       Required to notarize. 10-character Developer Team ID.
#
# One of these three credential sets is required to notarize (checked in this order):
#   NOTARY_KEYCHAIN_PROFILE                                   profile name stored via
#                                                              `xcrun notarytool store-credentials`
#   APPLE_API_KEY_ID + APPLE_API_ISSUER + APPLE_API_KEY_PATH   App Store Connect API key
#   APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD                     Apple ID + app-specific password
#
# If CODESIGN_IDENTITY is set but no credential set above is present, the app
# is signed but notarization/dmg are skipped (a signed-but-unnotarized app
# still triggers a Gatekeeper warning on a clean Mac, just a less scary one).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "build_macos.sh must run on macOS (codesign/notarytool/stapler are Apple tools)." >&2
    exit 1
fi

PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "no python3 found (looked for .venv/bin/python3, then PATH)" >&2; exit 1; }

VERSION="$("$PY" -c "
import re
print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"', open('app/constants.py').read()).group(1))
")"
echo "BulkSeq Studio version $VERSION"

echo "[1/5] Building the .app with PyInstaller..."
rm -rf build dist
"$PY" -m PyInstaller packaging/BulkSeqStudio.spec --noconfirm
APP="$ROOT/dist/BulkSeq Studio.app"
[ -d "$APP" ] || { echo "PyInstaller did not produce $APP (check that the spec's IS_MACOS branch ran)" >&2; exit 1; }

if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    echo "CODESIGN_IDENTITY not set — leaving an UNSIGNED build at: $APP"
    echo "This is expected on PR builds without signing secrets. Skipping sign/dmg/notarize."
    exit 0
fi

echo "[2/5] Codesigning (Hardened Runtime, entitlements, deep)..."
# --deep recursively signs everything inside the bundle in one pass. Apple's
# own guidance for complex bundles now favors signing inside-out (nested
# frameworks first) over --deep, but --deep is what this project has asked
# for here and is the approach used in the PyInstaller/PySide6 community
# threads researched for this script (see packaging/entitlements.plist and
# the pyinstaller#8927 issue linked there) — the --onedir + QtWebEngine
# notarization path has open, unresolved reports upstream, so treat a first
# real run of this step on real hardware as a verification step, not a
# formality.
codesign --force --deep --options runtime --timestamp \
    --entitlements "$ROOT/packaging/entitlements.plist" \
    --sign "$CODESIGN_IDENTITY" \
    "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "[3/5] Building the DMG with create-dmg..."
if ! command -v create-dmg >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        brew install create-dmg
    else
        echo "create-dmg not found and Homebrew is unavailable; install create-dmg and retry." >&2
        exit 1
    fi
fi
OUTDIR="$ROOT/installer_output"
mkdir -p "$OUTDIR"
DMG="$OUTDIR/BulkSeqStudio-${VERSION}-macos-arm64.dmg"
rm -f "$DMG"
create-dmg \
    --volname "BulkSeq Studio $VERSION" \
    --volicon "$ROOT/app/assets/icons/bulkseq.icns" \
    --window-size 540 380 \
    --icon-size 128 \
    --icon "BulkSeq Studio.app" 140 170 \
    --app-drop-link 400 170 \
    "$DMG" \
    "$APP"
[ -f "$DMG" ] || { echo "create-dmg did not produce $DMG" >&2; exit 1; }

if [ -z "${APPLE_TEAM_ID:-}" ]; then
    echo "APPLE_TEAM_ID not set — app is signed but DMG will NOT be notarized/stapled."
    echo "  Signed app:  $APP"
    echo "  Unnotarized DMG: $DMG"
    exit 0
fi

NOTARY_ARGS=()
if [ -n "${NOTARY_KEYCHAIN_PROFILE:-}" ]; then
    NOTARY_ARGS=(--keychain-profile "$NOTARY_KEYCHAIN_PROFILE")
elif [ -n "${APPLE_API_KEY_ID:-}" ] && [ -n "${APPLE_API_ISSUER:-}" ] && [ -n "${APPLE_API_KEY_PATH:-}" ]; then
    NOTARY_ARGS=(--key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER")
elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]; then
    NOTARY_ARGS=(--apple-id "$APPLE_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID")
else
    echo "APPLE_TEAM_ID is set but no credential set (NOTARY_KEYCHAIN_PROFILE, or" >&2
    echo "APPLE_API_KEY_ID/APPLE_API_ISSUER/APPLE_API_KEY_PATH, or" >&2
    echo "APPLE_ID/APPLE_APP_SPECIFIC_PASSWORD) is present." >&2
    exit 1
fi

echo "[4/5] Submitting to Apple notary service (this can take several minutes)..."
xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait

echo "[5/5] Stapling the notarization ticket..."
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"

echo ""
echo "Done."
echo "  Signed, notarized DMG: $DMG"
