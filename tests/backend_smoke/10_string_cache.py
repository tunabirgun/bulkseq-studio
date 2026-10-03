from __future__ import annotations

import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, "Redirect refused", headers, fp)


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
cache = root / "projects/ppi/results/networks/string_cache"
cache.mkdir(parents=True, exist_ok=True)
base = "https://stringdb-downloads.org/download"
files = (
    ("protein.aliases.v12.0", "4932.protein.aliases.v12.0.txt.gz"),
    ("protein.info.v12.0", "4932.protein.info.v12.0.txt.gz"),
    ("protein.links.v12.0", "4932.protein.links.v12.0.txt.gz"),
)
limit = 150 * 1024 * 1024


def check_advertised(used: int, length: int) -> None:
    if length <= 0 or used + length > limit:
        raise ValueError("STRING advertised transfer exceeds cap")


if sys.argv[1:] == ["--negative-cap"]:
    check_advertised(0, limit + 1)
elif sys.argv[1:] == ["--negative-redirect"]:
    NoRedirect().redirect_request(urllib.request.Request(base), None, 302,
                                  "redirect", {}, base)
elif sys.argv[1:]:
    raise SystemExit("Unknown STRING cache argument")
opener = urllib.request.build_opener(NoRedirect)
total = 0
for collection, name in files:
    target = cache / name
    if target.exists():
        raise SystemExit(f"STRING cache target already exists: {name}")
    url = f"{base}/{collection}/{name}"
    with opener.open(urllib.request.Request(url, method="HEAD"), timeout=20) as response:
        if response.status != 200 or response.url != url:
            raise SystemExit(f"Unapproved STRING HEAD response: {name}")
        advertised = int(response.headers.get("Content-Length", "-1"))
        check_advertised(total, advertised)
    part = target.with_suffix(target.suffix + ".part")
    with opener.open(urllib.request.Request(url, method="GET"), timeout=45) as response:
        if response.status != 200 or response.url != url:
            raise SystemExit(f"Unapproved STRING GET response: {name}")
        with part.open("xb") as handle:
            transferred = 0
            while block := response.read(1024 * 1024):
                transferred += len(block)
                if total + transferred > limit or transferred > advertised:
                    raise SystemExit(f"STRING transfer exceeds approved cap: {name}")
                handle.write(block)
    if transferred != advertised:
        raise SystemExit(f"STRING HEAD/GET size mismatch: {name}")
    with part.open("rb") as handle:
        if handle.read(2) != b"\x1f\x8b":
            raise SystemExit(f"STRING archive header is invalid: {name}")
    part.rename(target)
    total += transferred
    print(f"Cached approved STRING {name}: {transferred} bytes")
print(f"Approved STRING aggregate transfer: {total} bytes")
