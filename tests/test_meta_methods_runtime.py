from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]


def _runtime():
    runtime = rscript_runtime("DESeq2", "metaRNASeq", "metafor", "HTSFilter")
    if runtime is None:
        reason = "Rscript with the pinned meta-analysis packages is unavailable"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R_META") else pytest.skip)(reason)
    return runtime


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    command, convert = _runtime()
    return subprocess.run(
        [*command, convert(ROOT / "tests" / script), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=240, check=False,
    )


def test_meta_r_suite_is_an_effective_required_gate() -> None:
    result = _run("test_meta_analysis.R")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL_META_TESTS_PASS" in result.stdout
    bad = _run("test_meta_analysis.R", "--inject-failure")
    assert bad.returncode != 0, bad.stdout + bad.stderr
    assert "META_TESTS_FAILED" in bad.stdout


def test_pooled_family_references_and_mutation_controls() -> None:
    result = _run("meta_pooled_methods.R")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "META_POOLED_METHODS_PASS" in result.stdout
    for mutation in ("--inject-old-family", "--inject-protected-column"):
        bad = _run("meta_pooled_methods.R", mutation)
        assert bad.returncode != 0, bad.stdout + bad.stderr
        assert "Error" in bad.stderr


def test_seeded_pooled_null_calibration_and_old_family_control() -> None:
    result = _run("meta_pooled_null_calibration.R")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NULL_CALIBRATION_PASS" in result.stdout
    old = _run("meta_pooled_null_calibration.R", "--inject-old-family")
    assert old.returncode != 0, old.stdout + old.stderr
    assert "Null calibration failed" in old.stderr
