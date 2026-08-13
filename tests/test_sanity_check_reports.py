from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from app.core.sanity_checks import write_check


TITLE = "BulkSeq Studio validation checks"


def test_gui_sanity_report_uses_route_neutral_title(tmp_path: Path) -> None:
    report = write_check(
        tmp_path,
        "01_input_validation",
        [{"status": "PASS", "message": "Synthetic validation passed."}],
    )
    assert report.is_file()
    text = (tmp_path / "checks" / "sanity_checks.txt").read_text(encoding="utf-8")
    assert text.startswith(f"{TITLE}\n{'=' * len(TITLE)}\n")
    assert "RNA-seq Sanity Checks" not in text


def test_workflow_aggregate_report_uses_route_neutral_title(tmp_path: Path) -> None:
    check = tmp_path / "checks" / "11_normalization_qc.json"
    check.parent.mkdir(parents=True)
    check.write_text(
        json.dumps({
            "check": "11_normalization_qc",
            "status": "PASS",
            "messages": [{"status": "PASS", "message": "Synthetic normalization passed."}],
        }),
        encoding="utf-8",
    )
    output = tmp_path / "checks" / "sanity_checks.txt"
    script = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "aggregate_sanity_checks.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--checks", str(check), "--out", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    text = output.read_text(encoding="utf-8")
    assert text.startswith(f"{TITLE}\n{'=' * len(TITLE)}\nOverall: PASS\n")
    assert "RNA-seq Sanity Checks" not in text
