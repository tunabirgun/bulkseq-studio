from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
import shutil
import subprocess
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def ingest_script(tmp_path: Path) -> Path:
    scripts = tmp_path / "workflow" / "scripts"
    shutil.copytree(
        ROOT / "workflow" / "scripts",
        scripts,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return scripts / "ingest_counts.py"


def _run_ingest(
    script: Path,
    tmp_path: Path,
    matrix_text: str,
    *extra: str,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    matrix = tmp_path / "matrix.csv"
    samples = tmp_path / "samples.tsv"
    counts = tmp_path / "results" / "counts" / "counts.txt"
    summary = tmp_path / "results" / "counts" / "counts.txt.summary"
    matrix.write_text(matrix_text, encoding="utf-8")
    samples.write_text(
        "sample_id\tcondition\nS1\tcontrol\nS2\ttreated\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--matrix",
            str(matrix),
            "--samples",
            str(samples),
            "--out",
            str(counts),
            "--summary",
            str(summary),
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, counts, summary


@pytest.mark.parametrize(
    ("bad_value", "expected"),
    [
        ("oops", "numeric"),
        ("", "missing"),
        ("NA", "missing"),
        ("NaN", "finite"),
        ("inf", "finite"),
        ("-0.1", "negative"),
        ("1.25", "estimated"),
    ],
    ids=("text", "blank", "na", "nan", "infinity", "small-negative", "single-fraction"),
)
def test_ingest_rejects_one_invalid_cell_before_writing_outputs(
    ingest_script: Path,
    tmp_path: Path,
    bad_value: str,
    expected: str,
) -> None:
    matrix = (
        "gene_id,S1,S2\n"
        "G1,10,20\n"
        f"G2,{bad_value},40\n"
        "G3,50,60\n"
        "G4,70,80\n"
    )
    result, counts, summary = _run_ingest(ingest_script, tmp_path, matrix)

    assert result.returncode != 0
    assert expected in (result.stdout + result.stderr).lower()
    assert not counts.exists()
    assert not summary.exists()


def test_integer_counts_and_one_million_totals_are_preserved_with_a_warning(
    ingest_script: Path,
    tmp_path: Path,
) -> None:
    matrix = (
        "gene_id,S1,S2\n"
        "G1,600000,250000\n"
        "G2,400000,750000\n"
    )
    result, counts, summary = _run_ingest(ingest_script, tmp_path, matrix)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "near 1,000,000" in (result.stdout + result.stderr)
    imported = pd.read_csv(counts, sep="\t", comment="#", dtype=str)
    assert imported[["S1", "S2"]].to_dict("list") == {
        "S1": ["600000", "400000"],
        "S2": ["250000", "750000"],
    }
    assert summary.exists()


def test_large_integer_counts_are_not_routed_through_float(
    ingest_script: Path,
    tmp_path: Path,
) -> None:
    matrix = (
        "gene_id,S1,S2\n"
        "G1,9007199254740993,9007199254740995\n"
        "G2,7,9\n"
    )
    result, counts, _summary = _run_ingest(ingest_script, tmp_path, matrix)

    assert result.returncode == 0, result.stdout + result.stderr
    imported = pd.read_csv(counts, sep="\t", comment="#", dtype=str)
    assert imported.loc[0, "S1"] == "9007199254740993"
    assert imported.loc[0, "S2"] == "9007199254740995"


def test_out_of_range_count_fails_before_outputs(
    ingest_script: Path,
    tmp_path: Path,
) -> None:
    matrix = (
        "gene_id,S1,S2\n"
        "G1,9223372036854775808,10\n"
        "G2,20,30\n"
    )
    result, counts, summary = _run_ingest(ingest_script, tmp_path, matrix)

    assert result.returncode != 0
    assert "signed 64-bit count range" in (result.stdout + result.stderr)
    assert not counts.exists()
    assert not summary.exists()


def test_assigned_totals_use_exact_integer_summation(
    ingest_script: Path,
    tmp_path: Path,
) -> None:
    maximum = "9223372036854775807"
    matrix = (
        "gene_id,S1,S2\n"
        f"G1,{maximum},1\n"
        f"G2,{maximum},2\n"
    )
    result, _counts, summary = _run_ingest(ingest_script, tmp_path, matrix)

    assert result.returncode == 0, result.stdout + result.stderr
    lines = summary.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "Assigned\t18446744073709551614\t3"


def test_explicit_estimated_counts_use_the_documented_rounding_convention(
    ingest_script: Path,
    tmp_path: Path,
) -> None:
    source = {
        "S1": ["1.5", "2.5", "3.4"],
        "S2": ["4.5", "5.5", "6.6"],
    }
    matrix = (
        "gene_id,S1,S2\n"
        f"G1,{source['S1'][0]},{source['S2'][0]}\n"
        f"G2,{source['S1'][1]},{source['S2'][1]}\n"
        f"G3,{source['S1'][2]},{source['S2'][2]}\n"
    )
    result, counts, _summary = _run_ingest(
        ingest_script,
        tmp_path,
        matrix,
        "--estimated-counts",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    imported = pd.read_csv(counts, sep="\t", comment="#", dtype=str)
    expected = {
        sample: [str(int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)))
                 for value in values]
        for sample, values in source.items()
    }
    assert imported[["S1", "S2"]].to_dict("list") == expected
