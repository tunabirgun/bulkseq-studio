from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workflow" / "scripts" / "check_sample_structure.py"
AGGREGATE = ROOT / "workflow" / "scripts" / "aggregate_sanity_checks.py"


def _run(tmp_path: Path, matrix: dict[str, dict[str, float]]) -> Path:
    samples = tmp_path / "samples.tsv"
    samples.write_text(
        "sample_id\tcondition\ncontrol_1\tcontrol\ncontrol_2\tcontrol\ntreatment_1\ttreatment\ntreatment_2\ttreatment\n",
        encoding="utf-8",
    )
    correlations = tmp_path / "pearson.csv"
    labels = list(matrix)
    with correlations.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([""] + labels)
        for label in labels:
            writer.writerow([label] + [matrix[label][other] for other in labels])
    output = tmp_path / "22_sample_structure_qc.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--correlations", str(correlations), "--samples", str(samples), "--out", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return output


def test_compact_replicates_pass_sample_structure_qc(tmp_path: Path) -> None:
    output = _run(tmp_path, {
        "control_1": {"control_1": 1, "control_2": .995, "treatment_1": .95, "treatment_2": .949},
        "control_2": {"control_1": .995, "control_2": 1, "treatment_1": .951, "treatment_2": .95},
        "treatment_1": {"control_1": .95, "control_2": .951, "treatment_1": 1, "treatment_2": .994},
        "treatment_2": {"control_1": .949, "control_2": .95, "treatment_1": .994, "treatment_2": 1},
    })
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["assessment"] == "ASSESSED"
    assert all(not group["advisory"] for group in payload["evidence"]["groups"])


def test_dispersed_replicates_emit_warning_and_renderable_limitation(tmp_path: Path) -> None:
    output = _run(tmp_path, {
        "control_1": {"control_1": 1, "control_2": .995, "treatment_1": .994, "treatment_2": .97},
        "control_2": {"control_1": .995, "control_2": 1, "treatment_1": .993, "treatment_2": .969},
        "treatment_1": {"control_1": .994, "control_2": .993, "treatment_1": 1, "treatment_2": .97},
        "treatment_2": {"control_1": .97, "control_2": .969, "treatment_1": .97, "treatment_2": 1},
    })
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "WARNING"
    assert payload["assessment"] == "ADVISORY"
    assert any(group["condition"] == "treatment" and group["advisory"] for group in payload["evidence"]["groups"])
    message = payload["messages"][0]["message"]
    assert "not an outlier call" in message
    assert "not a small-n hypothesis test" in message

    report = tmp_path / "sanity_checks.txt"
    completed = subprocess.run(
        [sys.executable, str(AGGREGATE), "--checks", str(output), "--out", str(report)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "22_sample_structure_qc: WARNING" in report.read_text(encoding="utf-8")
    assert "interpret differential-expression and enrichment results cautiously" in report.read_text(encoding="utf-8")
