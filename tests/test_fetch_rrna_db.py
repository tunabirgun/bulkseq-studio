from __future__ import annotations

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
