#!/usr/bin/env bash
# The README and the install guide state the oldest glibc the AppImage runs on. That is a
# property of the libraries the build bundles, so it moves whenever the build runner does.
# Measure it from the AppImage and fail when the documentation no longer matches.
# Usage: check_appimage_glibc.sh APPIMAGE [README.md] [docs_src/content.mjs]
set -euo pipefail
appimage="$1"; readme="${2:-README.md}"; guide="${3:-docs_src/content.mjs}"

documented=$(grep -hoE 'glibc [0-9]+\.[0-9]+ or newer' "$readme" "$guide" | grep -oE '[0-9]+\.[0-9]+' | sort -u)
if [ -z "$documented" ]; then
  echo "::error::Neither $readme nor $guide states a glibc floor ('glibc X.Y or newer')."; exit 1
fi
if [ "$(printf '%s\n' "$documented" | wc -l)" -ne 1 ]; then
  echo "::error::$readme and $guide state different glibc floors: $(echo $documented)"; exit 1
fi

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
cp "$appimage" "$work/a.AppImage"; chmod +x "$work/a.AppImage"
(cd "$work" && ./a.AppImage --appimage-extract >/dev/null)
measured=$(find "$work/squashfs-root" -type f -print0 \
  | xargs -0 -n 64 sh -c 'for f; do file -b "$f" | grep -q "^ELF" && objdump -T "$f" 2>/dev/null; done; true' _ \
  | grep -oE 'GLIBC_[0-9]+(\.[0-9]+)+' | sed 's/^GLIBC_//' | sort -u -V | tail -1)
if [ -z "$measured" ]; then
  echo "::error::No GLIBC symbol versions found in $appimage; the measurement read nothing."; exit 1
fi

echo "documented glibc floor: $documented; highest GLIBC symbol the bundled binaries need: $measured"
if [ "$measured" != "$documented" ]; then
  echo "::error::The AppImage needs glibc $measured, but the documentation says $documented or newer."; exit 1
fi
