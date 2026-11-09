#!/usr/bin/env bash
# CI only: download one GitHub release asset and verify its SHA-256.
#
# github.com/<repo>/releases/download/... has returned HTTP 504 for long stretches while the
# API asset route served the same bytes, failing builds that never reached project code. Try
# the release URL with retries first, then the API route. The digest check is the same either
# way, so the route cannot change what is accepted.
#
# Usage: ci_fetch_release_asset.sh OWNER/REPO TAG ASSET SHA256 DEST   (GH_TOKEN optional)
set -euo pipefail
repo=$1 tag=$2 asset=$3 sha=$4 dest=$5
retry=(--retry 4 --retry-all-errors --retry-delay 10 --connect-timeout 30)
auth=()
[ -n "${GH_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GH_TOKEN")

if ! curl -fsSL "${retry[@]}" -o "$dest" "https://github.com/$repo/releases/download/$tag/$asset"; then
  echo "::warning::Release download of $asset failed; using the GitHub API asset route."
  id=$(curl -fsSL "${retry[@]}" "${auth[@]}" "https://api.github.com/repos/$repo/releases/tags/$tag" |
    python3 -c 'import json, sys; print(next(a["id"] for a in json.load(sys.stdin)["assets"] if a["name"] == sys.argv[1]))' "$asset")
  curl -fsSL "${retry[@]}" "${auth[@]}" -H "Accept: application/octet-stream" -o "$dest" \
    "https://api.github.com/repos/$repo/releases/assets/$id"
fi
echo "$sha  $dest" | sha256sum --check --strict
