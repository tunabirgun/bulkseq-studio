#!/usr/bin/env python3
# Stage the SortMeRNA rRNA reference FASTA. Default: download the SortMeRNA release
# database.tar.gz and extract smr_v4.3_default_db.fasta. A custom database (config
# sortmerna.database) may be a local FASTA path (copied), a direct FASTA URL, or a
# tarball URL (the default member is extracted). Uses only the Python standard library.
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

DEFAULT_URL = "https://github.com/sortmerna/sortmerna/releases/download/v4.3.4/database.tar.gz"
MEMBER = "smr_v4.3_default_db.fasta"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--database", default="", help="custom local FASTA path, FASTA URL, or tarball URL; empty = default")
    args = ap.parse_args()

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    db = (args.database or "").strip()

    # Custom local FASTA file: copy it.
    if db and os.path.isfile(db):
        shutil.copyfile(db, out)
        print(f"rRNA DB: copied local file {db}")
        return

    is_url = db.startswith(("http://", "https://"))
    # Custom direct-FASTA URL: download as-is.
    if is_url and db.lower().endswith((".fasta", ".fa")):
        urllib.request.urlretrieve(db, out)
        print(f"rRNA DB: downloaded FASTA {db}")
        return

    # Otherwise a tarball URL (custom or the default release): extract the default member.
    tar_url = db if is_url else DEFAULT_URL
    if db and not is_url:
        sys.exit(f"sortmerna.database '{db}' is not an existing file or a URL.")
    with tempfile.TemporaryDirectory() as tmp:
        tgz = os.path.join(tmp, "database.tar.gz")
        print(f"rRNA DB: downloading {tar_url}")
        urllib.request.urlretrieve(tar_url, tgz)
        with tarfile.open(tgz) as tf:
            member = next((m for m in tf.getmembers() if os.path.basename(m.name) == MEMBER), None)
            if member is None:
                sys.exit(f"{MEMBER} not found in {tar_url}")
            member.name = os.path.basename(member.name)
            tf.extract(member, tmp)
        shutil.move(os.path.join(tmp, MEMBER), out)
        print(f"rRNA DB: extracted {MEMBER} -> {out}")


if __name__ == "__main__":
    main()
