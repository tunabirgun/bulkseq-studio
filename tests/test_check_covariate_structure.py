from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow" / "scripts"))
import check_covariate_structure as ccs  # noqa: E402


def _samples(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["sample_id", "condition", "layout", "fastq_1", "batch", "site"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def _pca(path: Path, coords: dict[str, tuple[float, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "PC1", "PC2"])
        for sid, (pc1, pc2) in coords.items():
            writer.writerow([sid, pc1, pc2])


def test_batch_explaining_pc1_is_review_required():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "batch": ["x", "x", "y", "y"],
        "site": ["p", "q", "p", "q"],
    }
    pca = {"s1": (-10.0, 1.0), "s2": (-9.0, -1.0), "s3": (10.0, 1.0), "s4": (9.0, -1.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "REVIEW_REQUIRED"
    batch_msgs = [m for m in result["messages"] if "batch" in m["message"]]
    assert batch_msgs and batch_msgs[0]["status"] == "REVIEW_REQUIRED"
    assert "aliased" in batch_msgs[0]["message"]


def test_incidental_column_passes():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "site": ["p", "q", "p", "q"],
    }
    pca = {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03), "s4": (9.0, 0.06)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "PASS"
    assert any("site" in m["message"] for m in result["messages"])


def test_column_in_design_formula_is_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "batch": ["x", "x", "y", "y"],
    }
    pca = {"s1": (-10.0, 0.0), "s2": (-9.0, 0.0), "s3": (10.0, 0.0), "s4": (9.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ batch + condition", "condition")
    assert not any("batch" in m["message"] for m in result["messages"])


def test_single_level_column_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "organism": ["human", "human", "human", "human"],
    }
    pca = {"s1": (-1.0, 0.0), "s2": (-1.0, 0.0), "s3": (1.0, 0.0), "s4": (1.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert not any("organism" in m["message"] for m in result["messages"])


def test_sample_level_column_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "notes": ["n1", "n2", "n3", "n4"],
    }
    pca = {"s1": (-1.0, 0.0), "s2": (-1.0, 0.0), "s3": (1.0, 0.0), "s4": (1.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert not any("notes" in m["message"] for m in result["messages"])


def test_negative_wrong_column_set_hides_the_batch_effect():
    # Feeding the check a column set that omits the batch column that actually explains PC1
    # must not surface REVIEW_REQUIRED -- this is the gate's own negative test.
    samples = ["s1", "s2", "s3", "s4"]
    columns = {"condition": ["A", "A", "B", "B"], "site": ["p", "q", "p", "q"]}
    pca = {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03), "s4": (9.0, 0.06)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "PASS"


def test_pasilla_replicate_two_levels_four_samples_is_pass_not_review_required():
    # H4: raw R^2 has a null expectation of (k-1)/(n-1); a 2-level, 4-sample column at
    # raw R^2=0.69 was the defect this fix exists to catch -- it must now be PASS, with
    # the adjusted R^2 and F-test p-value printed rather than hidden.
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "B", "A", "B"],
        "replicate": ["1", "1", "2", "2"],
    }
    pc2 = {"s1": -0.5, "s2": -2.0, "s3": 1.5, "s4": 0.5}
    pca = {sid: (0.0, pc2[sid]) for sid in samples}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    rep_msgs = [m for m in result["messages"] if "replicate" in m["message"]]
    assert rep_msgs and rep_msgs[0]["status"] == "PASS"
    assert "adjusted R^2" in rep_msgs[0]["message"] and "p=" in rep_msgs[0]["message"]
    assert "numeric column treated as categorical" in rep_msgs[0]["message"]


def test_real_batch_effect_in_six_samples_is_still_flagged():
    samples = [f"s{i}" for i in range(1, 7)]
    columns = {
        "condition": ["A", "A", "A", "B", "B", "B"],
        "batch": ["x", "x", "y", "x", "y", "y"],
    }
    pca = {
        "s1": (-1.0, 10.0), "s2": (-1.2, 9.5), "s3": (-0.9, -9.8),
        "s4": (1.0, 9.6), "s5": (0.8, -10.2), "s6": (1.1, -9.6),
    }
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    batch_msgs = [m for m in result["messages"] if "batch" in m["message"]]
    assert batch_msgs and batch_msgs[0]["status"] == "REVIEW_REQUIRED"
    assert result["status"] == "REVIEW_REQUIRED"


def test_excluded_columns_match_app_constants_schema():
    constants_path = Path(__file__).resolve().parents[1] / "app" / "constants.py"
    text = constants_path.read_text(encoding="utf-8")
    required = re.search(r"REQUIRED_METADATA_COLUMNS\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    optional = re.search(r"OPTIONAL_METADATA_COLUMNS\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    schema_columns = set(re.findall(r'"([^"]+)"', required)) | set(re.findall(r'"([^"]+)"', optional))
    assert ccs.EXCLUDED_COLUMNS.issubset(schema_columns)
