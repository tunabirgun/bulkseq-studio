#!/usr/bin/env bash
# CI only: download one GitHub release asset and verify its SHA-256.
#
# github.com/<repo>/releases/download/... has returned HTTP 504 for long stretches while the
# API asset route served the same bytes, failing builds that never reached project code. Try
# the release URL with retries first, then the API route. The digest check is the same either
# way, so the route cannot change what is accepted; a mismatching download is deleted.
#
# Portable to bash 3.2 and BSD tools (macOS runs the tests): no empty-array expansion under
# `set -u`, and the digest comes from Python rather than a platform-specific sha256sum.
#
# Usage: ci_fetch_release_asset.sh OWNER/REPO TAG ASSET SHA256 DEST   (GH_TOKEN optional)
set -euo pipefail
repo=$1 tag=$2 asset=$3 sha=$4 dest=$5
retry=(--retry 4 --retry-all-errors --retry-delay 10 --connect-timeout 30)
auth=()
[ -n "${GH_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GH_TOKEN")

if ! curl -fsSL "${retry[@]}" -o "$dest" "https://github.com/$repo/releases/download/$tag/$asset"; then
  echo "::warning::Release download of $asset failed; using the GitHub API asset route."
  id=$(curl -fsSL "${retry[@]}" ${auth[@]+"${auth[@]}"} "https://api.github.com/repos/$repo/releases/tags/$tag" |
    python3 -c 'import json, sys; print(next(a["id"] for a in json.load(sys.stdin)["assets"] if a["name"] == sys.argv[1]))' "$asset")
  curl -fsSL "${retry[@]}" ${auth[@]+"${auth[@]}"} -H "Accept: application/octet-stream" -o "$dest" \
    "https://api.github.com/repos/$repo/releases/assets/$id"
fi
actual=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$dest")
if [ "$actual" != "$sha" ]; then
  rm -f "$dest"
  echo "::error::SHA-256 mismatch for $asset: expected $sha, got $actual." >&2
  exit 1
fi
echo "$dest: SHA-256 OK"
