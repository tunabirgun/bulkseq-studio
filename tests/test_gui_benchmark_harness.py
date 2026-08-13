from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
HARNESS_DIR = REPO / "installer_output" / "gui-benchmark-runs"


def _load_full_harness():
    sys.path.insert(0, str(HARNESS_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "bulkseq_run_gui_full_test", HARNESS_DIR / "run_gui_full.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HARNESS_DIR))


def _write_project(project: Path, payload: bytes, declared_md5: str) -> Path:
    (project / "config").mkdir(parents=True)
    cache = project.parent / "cache"
    cache.mkdir()
    (cache / "sample_R1.fastq.gz").write_bytes(payload)
    with (project / "config" / "samples.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", "fastq_1", "fastq_2", "fastq_1_md5", "fastq_2_md5"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({
            "sample_id": "sample",
            "fastq_1": "data/raw/sample_R1.fastq.gz",
            "fastq_2": "",
            "fastq_1_md5": declared_md5,
            "fastq_2_md5": "",
        })
    return cache


def test_fastq_cache_seed_copies_and_records_declared_input(tmp_path: Path) -> None:
    harness = _load_full_harness()
    project = tmp_path / "project"
    payload = b"synthetic-fastq\n"
    expected = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    cache = _write_project(project, payload, expected)

    evidence = harness.seed_fastq_inputs(project, cache)

    target = project / "data" / "raw" / "sample_R1.fastq.gz"
    assert target.read_bytes() == payload
    assert evidence["method"] == "verified local FASTQ copy"
    assert evidence["files"] == [{
        "target": "data/raw/sample_R1.fastq.gz",
        "source": str((cache / "sample_R1.fastq.gz").resolve()),
        "bytes": len(payload),
        "md5": expected,
        "copy_method": "shutil.copyfile",
    }]


def test_fastq_cache_seed_rejects_wrong_md5_without_output(tmp_path: Path) -> None:
    harness = _load_full_harness()
    project = tmp_path / "project"
    cache = _write_project(project, b"corrupt\n", "0" * 32)

    with pytest.raises(harness.HarnessFailure, match="MD5 mismatch"):
        harness.seed_fastq_inputs(project, cache)

    assert not (project / "data" / "raw" / "sample_R1.fastq.gz").exists()


def test_fastq_cache_seed_refuses_existing_target(tmp_path: Path) -> None:
    harness = _load_full_harness()
    project = tmp_path / "project"
    payload = b"synthetic-fastq\n"
    expected = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    cache = _write_project(project, payload, expected)
    target = project / "data" / "raw" / "sample_R1.fastq.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-owned\n")

    with pytest.raises(harness.HarnessFailure, match="Refusing to overwrite"):
        harness.seed_fastq_inputs(project, cache)

    assert target.read_bytes() == b"user-owned\n"


def test_full_harness_rejects_nonpositive_validation_timeout() -> None:
    harness = _load_full_harness()
    with pytest.raises(SystemExit):
        harness.parse_args([
            "--benchmark", "pasilla_paired_subset",
            "--run-id", "test",
            "--validation-timeout-minutes", "0",
        ])


def test_large_input_timeout_is_used_for_validation_dry_run_and_launch() -> None:
    preflight_source = (HARNESS_DIR / "run_gui_preflight.py").read_text(encoding="utf-8")
    full_source = (HARNESS_DIR / "run_gui_full.py").read_text(encoding="utf-8")
    assert preflight_source.count("timeout_s=validation_timeout_s,") >= 2
    assert "timeout_s=validation_timeout_s + 600.0," in preflight_source
    assert "timeout_s=max(180.0, launch_timeout_s)," in full_source
    assert "launch_timeout_s=args.validation_timeout_minutes * 60.0," in full_source
    assert "validate_current_preflight(" not in preflight_source
    assert "validate_current_preflight(" not in full_source
    assert "GUI background fingerprint-and-validation worker" in preflight_source
