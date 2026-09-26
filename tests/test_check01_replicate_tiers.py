from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.core.metadata import validate_metadata

SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "validate_metadata.py"
FEWER_THAN_TWO = "Condition '{}' has fewer than two biological replicates."
FEWER_THAN_THREE = "Condition '{}' has fewer than the recommended three biological replicates."


def _sheet(conditions: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "sample_id": [f"s{i}" for i in range(len(conditions))],
        "condition": conditions,
        "layout": "single",
        "fastq_1": [f"s{i}.fq.gz" for i in range(len(conditions))],
    })


# Oracle written from the documented rule, not from either implementation.
CASES = [
    (["a", "a", "a", "b", "b", "b"], "PASS", []),
    (["a", "a", "b", "b"], "WARNING", [FEWER_THAN_THREE.format("a"), FEWER_THAN_THREE.format("b")]),
    (["a", "b", "b", "b"], "WARNING", [FEWER_THAN_TWO.format("a")]),
    (["a", "a", "a", "b", "b", "b", ""], "REVIEW_REQUIRED", ["1 sample(s) have empty or unknown condition."]),
]


@pytest.mark.parametrize("conditions,status,expected", CASES)
def test_workflow_check_01_and_interface_report_the_same_replicate_findings(tmp_path, conditions, status, expected):
    sheet = _sheet(conditions)
    samples = tmp_path / "samples.tsv"
    sheet.to_csv(samples, sep="\t", index=False)
    out = tmp_path / "01_input_validation.json"
    done = subprocess.run([sys.executable, str(SCRIPT), "--samples", str(samples), "--out", str(out)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    workflow = json.loads(out.read_text(encoding="utf-8"))
    workflow_msgs = [m["message"] for m in workflow["messages"] if m["status"] != "PASS"]
    assert workflow["status"] == status
    assert sorted(workflow_msgs) == sorted(expected)
    interface = validate_metadata(sheet, allow_pending_sra=True)
    interface_msgs = {m["message"] for m in interface if m["status"] != "PASS"}
    assert set(expected) <= interface_msgs
