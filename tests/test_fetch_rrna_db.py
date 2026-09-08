from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "fetch_rrna_db.py"


def test_copies_local_fasta(tmp_path):
    src = tmp_path / "my_rrna.fasta"
    src.write_text(">r1\nACGTACGTACGT\n", encoding="utf-8")
    out = tmp_path / "references" / "rrna_db.fasta"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--database", str(src)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").startswith(">r1")


def test_rejects_nonexistent_non_url_database(tmp_path):
    out = tmp_path / "rrna_db.fasta"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--database", "/no/such/file.txt"],
        capture_output=True, text=True,
    )
    # A database string that is neither an existing file nor a URL is an error,
    # not a silent default download.
    assert r.returncode != 0
    assert not out.exists()


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_local_fasta_gets_an_integrity_sidecar(tmp_path):
    import hashlib
    import json

    src = tmp_path / "my_rrna.fasta"
    body = b">r1\nACGTACGTACGT\n"
    src.write_bytes(body)
    out = tmp_path / "references" / "rrna_db.fasta"
    r = _run("--out", str(out), "--database", str(src))
    assert r.returncode == 0, r.stderr
    sidecar = json.loads((tmp_path / "references" / "rrna_db.fasta.integrity.json").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == hashlib.sha256(body).hexdigest()
    assert sidecar["byte_size"] == len(body)
    # No pin was supplied for a user-provided file, and the sidecar must say so rather than
    # implying the bytes were checked against anything.
    assert sidecar["verified_against_pin"] is False


def test_wrong_digest_fails_and_stages_nothing(tmp_path):
    src = tmp_path / "my_rrna.fasta"
    src.write_text(">r1\nACGTACGTACGT\n", encoding="utf-8")
    out = tmp_path / "references" / "rrna_db.fasta"
    r = _run("--out", str(out), "--database", str(src), "--expected-sha256", "0" * 64)
    assert r.returncode != 0
    assert "SHA-256 mismatch" in (r.stdout + r.stderr)
    # A half-staged FASTA would be accepted by the next run as a valid reference.
    assert not out.exists()
    assert not (tmp_path / "references" / "rrna_db.fasta.integrity.json").exists()


def test_matching_digest_is_accepted(tmp_path):
    import hashlib
    import json

    body = b">r1\nACGTACGTACGT\n"
    src = tmp_path / "my_rrna.fasta"
    src.write_bytes(body)
    out = tmp_path / "rrna_db.fasta"
    r = _run("--out", str(out), "--database", str(src),
             "--expected-sha256", hashlib.sha256(body).hexdigest())
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "rrna_db.fasta.integrity.json").read_text(encoding="utf-8"))["verified_against_pin"] is True


def test_default_pins_are_real_digests():
    # An empty or malformed constant would disable verification on the default route while
    # every other test here still passed.
    source = SCRIPT.read_text(encoding="utf-8")
    for name in ("DEFAULT_ARCHIVE_SHA256", "DEFAULT_MEMBER_SHA256"):
        value = re.search(rf'^{name} = "([^"]*)"', source, re.M).group(1)
        assert re.fullmatch(r"[0-9a-f]{64}", value), f"{name} is not a SHA-256 digest: {value!r}"
