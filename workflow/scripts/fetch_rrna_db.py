#!/usr/bin/env python3
# Stage the SortMeRNA rRNA reference FASTA. Default: download the SortMeRNA release
# database.tar.gz and extract smr_v4.3_default_db.fasta. A custom database (config
# sortmerna.database) may be a local FASTA path (copied), a direct FASTA URL, or a
# tarball URL (the default member is extracted). Uses only the Python standard library.
#
# The realized FASTA decides which reads are removed before alignment, so it is pinned by
# SHA-256 like every other reference artifact: the default download is verified against the
# digests below, and whatever is staged -- pinned or user-supplied -- is hashed into a sidecar
# so the run's provenance records the bytes that were actually used.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

DEFAULT_URL = "https://github.com/sortmerna/sortmerna/releases/download/v4.3.4/database.tar.gz"
MEMBER = "smr_v4.3_default_db.fasta"
# Measured from DEFAULT_URL: archive 236650154 B, extracted member 146741693 B.
DEFAULT_ARCHIVE_SHA256 = "7bf684b9a0eb37e9b74c97e92f5a1f9acb9175f6fab03812d458da9049ce9660"
DEFAULT_MEMBER_SHA256 = "50e80a1a2d1e8c4e2265d3d84c082f258166d4d8f628cd0956c04b15c5fc1a53"


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: str, expected: str, what: str) -> str:
    """Hash `path`; abort (removing it) when a digest was expected and does not match."""
    observed = sha256(path)
    if expected and observed != expected:
        os.remove(path)
        sys.exit(
            f"{what} SHA-256 mismatch: expected {expected}, got {observed}. "
            "The download was corrupted or the source changed; nothing was staged."
        )
    return observed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--database", default="", help="custom local FASTA path, FASTA URL, or tarball URL; empty = default")
    ap.add_argument("--sidecar", default="", help="integrity JSON path; default <out>.integrity.json")
    ap.add_argument("--expected-sha256", default="",
                    help="pin the staged FASTA to this digest; overrides the built-in default pin")
    args = ap.parse_args()

    out = args.out
    sidecar = args.sidecar or (out + ".integrity.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    db = (args.database or "").strip()
    expected = args.expected_sha256.strip().lower()
    source = db or DEFAULT_URL
    extracted_member = None

    # Custom local FASTA file: copy it.
    if db and os.path.isfile(db):
        shutil.copyfile(db, out)
        print(f"rRNA DB: copied local file {db}")
    else:
        is_url = db.startswith(("http://", "https://"))
        # Custom direct-FASTA URL: download as-is.
        if is_url and db.lower().endswith((".fasta", ".fa")):
            urllib.request.urlretrieve(db, out)
            print(f"rRNA DB: downloaded FASTA {db}")
        else:
            # Otherwise a tarball URL (custom or the default release): extract the default member.
            tar_url = db if is_url else DEFAULT_URL
            if db and not is_url:
                sys.exit(f"sortmerna.database '{db}' is not an existing file or a URL.")
            source = tar_url
            # The built-in pin describes the bundled release only; a custom URL is verified
            # solely against an explicit --expected-sha256.
            if tar_url == DEFAULT_URL and not expected:
                expected = DEFAULT_MEMBER_SHA256
            with tempfile.TemporaryDirectory() as tmp:
                tgz = os.path.join(tmp, "database.tar.gz")
                print(f"rRNA DB: downloading {tar_url}")
                urllib.request.urlretrieve(tar_url, tgz)
                # Check the archive before spending the extraction on it.
                verify(tgz, DEFAULT_ARCHIVE_SHA256 if tar_url == DEFAULT_URL else "",
                       "rRNA database archive")
                with tarfile.open(tgz) as tf:
                    member = next((m for m in tf.getmembers() if os.path.basename(m.name) == MEMBER), None)
                    if member is None:
                        sys.exit(f"{MEMBER} not found in {tar_url}")
                    member.name = os.path.basename(member.name)
                    tf.extract(member, tmp)
                shutil.move(os.path.join(tmp, MEMBER), out)
                extracted_member = MEMBER
                print(f"rRNA DB: extracted {MEMBER} -> {out}")

    observed = verify(out, expected, "rRNA database FASTA")
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump({
            "source": source,
            "member": extracted_member,
            "sha256": observed,
            "byte_size": os.path.getsize(out),
            "verified_against_pin": bool(expected),
        }, handle, indent=2)
    print(f"rRNA DB: sha256={observed} verified={bool(expected)} -> {sidecar}")


if __name__ == "__main__":
    main()
