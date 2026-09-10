from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "summarize_alignment.py"

_PAIRED_HIGH = """1000 reads; of these:
  1000 (100.00%) were paired; of these:
    50 (5.00%) aligned concordantly 0 times
    800 (80.00%) aligned concordantly exactly 1 time
    150 (15.00%) aligned concordantly >1 times
98.00% overall alignment rate
"""

_SINGLE_LOW = """1000 reads; of these:
  1000 (100.00%) were unpaired; of these:
    550 (55.00%) aligned 0 times
    300 (30.00%) aligned exactly 1 time
    150 (15.00%) aligned >1 times
45.00% overall alignment rate
"""


def _run(args: list[str], out: Path) -> dict:
    subprocess.run([sys.executable, str(_SCRIPT), *args, "--out", str(out)], check=True)
    return json.loads(out.read_text(encoding="utf-8"))


def test_paired_hisat2_summary_passes(tmp_path: Path):
    summary = tmp_path / "sampleA_hisat2_summary.txt"
    summary.write_text(_PAIRED_HIGH, encoding="utf-8")
    out = tmp_path / "06_alignment_qc.json"
    payload = _run(["--hisat2-summaries", str(summary)], out)
    assert payload["status"] == "PASS"
    assert "sampleA" in payload["messages"][0]["message"]
    assert "80.0%" in payload["messages"][0]["message"]


def test_single_end_hisat2_summary_below_threshold_fails(tmp_path: Path):
    summary = tmp_path / "sampleB_hisat2_summary.txt"
    summary.write_text(_SINGLE_LOW, encoding="utf-8")
    out = tmp_path / "06_alignment_qc.json"
    payload = _run(["--hisat2-summaries", str(summary)], out)
    assert payload["status"] == "REVIEW_REQUIRED"
    assert "30.0%" in payload["messages"][0]["message"]


def test_negative_missing_field_reports_unparsable(tmp_path: Path):
    summary = tmp_path / "sampleC_hisat2_summary.txt"
    summary.write_text("garbage summary with no recognizable fields\n", encoding="utf-8")
    out = tmp_path / "06_alignment_qc.json"
    payload = _run(["--hisat2-summaries", str(summary)], out)
    assert payload["status"] == "REVIEW_REQUIRED"
    assert "could not parse" in payload["messages"][0]["message"]
