#!/usr/bin/env bash
# Build a Linux AppImage from a PyInstaller onedir build of BulkSeq Studio.
#
# Usage: build_appimage.sh <onedir_dir> <version> <output_dir>
#   <onedir_dir>  PyInstaller onedir holding the 'BulkSeqStudio' binary and its '_internal/' sibling.
#   <version>     e.g. 0.12.1
#   <output_dir>  where BulkSeqStudio-<version>-x86_64.AppImage is written.
#
# Requires appimagetool (set APPIMAGETOOL, or have it on PATH). The onedir already bundles all of
# Qt (PyInstaller), so Qt is NOT re-bundled here; this only wraps the intact onedir. The app sets the
# QtWebEngine flags itself (os.environ.setdefault before QApplication), so AppRun sets no env vars.
set -euo pipefail

ONEDIR="${1:?onedir path}"
VERSION="${2:?version}"
OUTDIR="${3:?output dir}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
ICON="$REPO/app/assets/icons/bulkseq_256.png"
TOOL="${APPIMAGETOOL:-$(command -v appimagetool || echo "$HOME/bench_work/appimagetool-x86_64.AppImage")}"

[ -x "$ONEDIR/BulkSeqStudio" ] || { echo "no BulkSeqStudio binary in $ONEDIR" >&2; exit 1; }
[ -e "$ICON" ] || { echo "icon not found: $ICON" >&2; exit 1; }
[ -x "$TOOL" ] || { echo "appimagetool not found (set APPIMAGETOOL): $TOOL" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/bsq_appimage.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
APPDIR="$WORK/BulkSeqStudio.AppDir"
mkdir -p "$APPDIR/usr/bin"

# Copy the onedir CONTENTS into usr/bin: the binary and '_internal/' stay siblings (PyInstaller
# sys._MEIPASS requirement); the spaced parent name 'BulkSeq Studio' is dropped.
cp -a "$ONEDIR"/. "$APPDIR/usr/bin/"
[ -e "$APPDIR/usr/bin/_internal/PySide6/Qt/libexec/QtWebEngineProcess" ] || {
    echo "QtWebEngineProcess missing after copy — sibling integrity broken" >&2; exit 1; }

# AppRun: resolve own dir, exec the binary in place, forward args. Set NO env vars.
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "${HERE}/usr/bin/BulkSeqStudio" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# One root .desktop; Exec is a nominal token (runtime launches via AppRun). Icon= matches the
# root icon basename without extension.
cat > "$APPDIR/bulkseqstudio.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=BulkSeq Studio
Exec=bulkseqstudio
Icon=bulkseqstudio
Categories=Science;Biology;Education;
Terminal=false
EOF

cp "$ICON" "$APPDIR/bulkseqstudio.png"
cp "$APPDIR/bulkseqstudio.png" "$APPDIR/.DirIcon"

mkdir -p "$OUTDIR"
OUT="$OUTDIR/BulkSeqStudio-${VERSION}-x86_64.AppImage"
# Embed zsync update information so `AppImageUpdate <file>` upgrades in place from the latest
# GitHub release. This also makes appimagetool write the companion <OUT>.zsync next to the
# AppImage; publish both as release assets. Override UPDATE_INFO to point elsewhere.
UPDATE_INFO="${UPDATE_INFO:-gh-releases-zsync|tunabirgun|bulkseq-studio|latest|BulkSeqStudio-*-x86_64.AppImage.zsync}"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream -u "$UPDATE_INFO" "$APPDIR" "$OUT"
chmod +x "$OUT"
# appimagetool writes the companion .zsync into the CWD, not next to $OUT; move it
# beside the AppImage so both are in $OUTDIR ready to publish.
if [ ! -f "$OUT.zsync" ] && [ -f "$(basename "$OUT").zsync" ]; then
    mv -f "$(basename "$OUT").zsync" "$OUT.zsync"
fi
echo "APPIMAGE=$OUT"
ls -lh "$OUT"
[ -f "$OUT.zsync" ] && { echo "ZSYNC=$OUT.zsync"; ls -lh "$OUT.zsync"; } || echo "WARNING: no .zsync produced"
