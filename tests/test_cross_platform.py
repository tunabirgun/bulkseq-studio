"""Cross-platform correctness guards (Windows, Linux, macOS).

These cover defects that are live on the platforms already supported, not only on
the macOS port: a Stop that leaks tool processes on Linux, and sample ids that
collide on any case-insensitive filesystem (NTFS and APFS both, by default).
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.core import snakemake_runner
from app.core.metadata import validate_metadata


# ---- Stop must reach the whole process tree ---------------------------------

def test_native_launch_creates_its_own_process_group() -> None:
    # Windows gets CREATE_NEW_PROCESS_GROUP; POSIX needs setsid() via
    # start_new_session, or os.killpg has no group to signal and Snakemake's
    # children (STAR, featureCounts, Rscript) survive Stop.
    src = inspect.getsource(snakemake_runner.SnakemakeRunner.start)
    assert "start_new_session" in src


def test_stop_signals_the_group_not_just_the_child() -> None:
    for method in (snakemake_runner.SnakemakeRunner._stop_native_tree,
                   snakemake_runner.SnakemakeRunner._reap_local):
        src = inspect.getsource(method)
        assert "_signal_native_group" in src, f"{method.__name__} can leak the tool processes"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
def test_process_group_kill_reaches_a_grandchild(tmp_path) -> None:
    # End-to-end: a shell that spawns a long-lived grandchild. Killing only the
    # direct child would leave the grandchild running.
    marker = tmp_path / "alive"
    script = f"(while true; do touch {marker!s}; sleep 0.2; done) & wait"
    proc = subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True)
    try:
        os.killpg(os.getpgid(proc.pid), 15)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:  # pragma: no cover - cleanup path
            os.killpg(os.getpgid(proc.pid), 9)
    marker.unlink(missing_ok=True)
    import time
    time.sleep(0.6)
    assert not marker.exists(), "grandchild survived the process-group kill"


# ---- Case-insensitive filesystems -------------------------------------------

def _sheet(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "sample_id": ids,
        "condition": ["treated", "untreated"] * (len(ids) // 2) + ["treated"] * (len(ids) % 2),
        "layout": ["single"] * len(ids),
        "fastq_1": [""] * len(ids),
    })


def test_case_only_duplicate_sample_ids_are_rejected() -> None:
    # Sample1 and sample1 are distinct dict keys but one file on NTFS/APFS, so the
    # two samples would overwrite each other's intermediates and the run would
    # report whichever wrote last — a silent wrong answer, not a crash.
    messages = validate_metadata(_sheet(["Sample1", "sample1"]), allow_pending_sra=True)
    failures = [m for m in messages if m["status"] == "FAIL"]
    assert any("capitalisation" in m["message"] for m in failures), messages


def test_distinct_sample_ids_still_pass() -> None:
    messages = validate_metadata(_sheet(["S1", "S2"]), allow_pending_sra=True)
    assert not any("capitalisation" in m["message"] for m in messages)


def test_exact_duplicates_still_reported_as_duplicates() -> None:
    messages = validate_metadata(_sheet(["S1", "S1"]), allow_pending_sra=True)
    assert any("Duplicate sample_id" in m["message"] for m in messages)


# ---- Apple Silicon split environment ----------------------------------------

def test_native_path_prefix_is_empty_when_nothing_is_installed(monkeypatch, tmp_path) -> None:
    # Only existing directories are returned, so a host without the environment --
    # every Windows box, and any Linux box using an activated shell -- is unaffected.
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "nope"))
    assert snakemake_runner.native_path_prefix() == []


def test_shim_precedes_the_tools_prefix_on_apple_silicon(monkeypatch, tmp_path) -> None:
    # rseqc pulls a bare r-base into the tools prefix, so that prefix ships an Rscript
    # with no Bioconductor. The shim must win, or every library(DESeq2) fails.
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path))
    monkeypatch.setattr(snakemake_runner.sys, "platform", "darwin")
    monkeypatch.setattr(snakemake_runner.platform, "machine", lambda: "arm64")
    (tmp_path / "shims").mkdir()
    (tmp_path / "envs" / "bulkseq" / "bin").mkdir(parents=True)

    prefix = snakemake_runner.native_path_prefix()
    assert prefix[0].endswith("shims"), prefix
    assert prefix[1].endswith(str(Path("bulkseq") / "bin")), prefix


def test_shim_is_not_used_off_apple_silicon(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path))
    monkeypatch.setattr(snakemake_runner.sys, "platform", "linux")
    monkeypatch.setattr(snakemake_runner.platform, "machine", lambda: "x86_64")
    (tmp_path / "shims").mkdir()
    (tmp_path / "envs" / "bulkseq" / "bin").mkdir(parents=True)

    prefix = snakemake_runner.native_path_prefix()
    assert not any(p.endswith("shims") for p in prefix), prefix


def test_macos_arm64_specs_exist_and_omit_the_unavailable_packages() -> None:
    # These two packages have no osx-arm64 build; including either would drag the
    # whole Bioconductor graph back a release and invalidate the benchmarks.
    import yaml

    envs = Path(__file__).resolve().parents[1] / "workflow" / "envs"
    tools = yaml.safe_load((envs / "bulkseq_macos_arm64_tools.yaml").read_text(encoding="utf-8"))
    r_env = yaml.safe_load((envs / "bulkseq_macos_arm64_r.yaml").read_text(encoding="utf-8"))

    def names(spec):
        return {str(d).split("=")[0].split(">")[0] for d in spec["dependencies"]}

    assert "sortmerna" not in names(tools), "no osx-arm64 build; installed from upstream release"
    assert "fastq-screen" not in names(tools), "blocked by perl-gd, which has no osx-arm64 build"
    assert "bioconductor-gsva" not in names(r_env)
    assert "bioconductor-affy" not in names(r_env)
    # The core analysis path must still be complete.
    for required in ("star", "hisat2", "salmon", "samtools", "subread", "fastp"):
        assert required in names(tools), required
    for required in ("r-base", "bioconductor-deseq2", "bioconductor-limma", "bioconductor-edger"):
        assert required in names(r_env), required


# ---- Per-user paths follow platform convention ------------------------------

def test_error_log_path_is_not_dumped_in_the_home_directory(monkeypatch) -> None:
    from app import main

    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    path = main.error_log_path()
    home = Path.home()
    # It must live under a conventional per-user data location, never directly
    # under $HOME (which is what the old bare Path.home() fallback produced).
    assert path.parent.parent != home, f"{path} lands straight in the home directory"
    assert str(path).endswith(str(Path("logs") / "error.log"))
