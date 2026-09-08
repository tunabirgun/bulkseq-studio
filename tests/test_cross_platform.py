"""Cross-platform correctness guards for Windows and Linux.

Two defects that are live on the supported platforms: a Stop that leaks tool
processes on Linux, and sample ids that collide on a case-insensitive filesystem
such as NTFS.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.core import snakemake_runner
from app.core.metadata import validate_metadata


# ---- Stop must reach the whole process tree ---------------------------------

class _FakeProcess:
    """Stands in for a launched Snakemake relay: never actually running."""

    def __init__(self) -> None:
        self.pid = 424242
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _runner(monkeypatch, tmp_path, **command_kwargs):
    captured: dict = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", fake_popen)
    cmd = snakemake_runner.SnakemakeCommand(["snakemake", "-n"], "snakemake -n", **command_kwargs)
    return snakemake_runner.SnakemakeRunner(tmp_path, cmd), captured


def test_native_launch_creates_its_own_process_group(monkeypatch, tmp_path) -> None:
    # Windows gets CREATE_NEW_PROCESS_GROUP; POSIX needs setsid() via start_new_session, or
    # os.killpg has no group to signal and Snakemake's children (STAR, featureCounts, Rscript)
    # survive Stop. Asserted on the arguments actually handed to Popen.
    runner, captured = _runner(monkeypatch, tmp_path)
    runner.start()
    if sys.platform.startswith("win"):
        assert captured["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert captured["start_new_session"] is False
    else:
        assert captured["start_new_session"] is True
    # The pipeline speaks UTF-8; a strict cp1252 decode of one curly quote used to kill the
    # reader thread and leave the UI at "Running" forever.
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX process groups")
def test_stop_signals_the_group_not_just_the_child(monkeypatch, tmp_path) -> None:
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(snakemake_runner.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(snakemake_runner.os, "killpg",
                        lambda pgid, sig: signalled.append((pgid, sig)))
    runner, _ = _runner(monkeypatch, tmp_path)
    process = runner.start()
    runner.stop()
    assert (process.pid, signal.SIGTERM) in signalled, "the tool processes can leak"
    assert not process.terminated, "a bare terminate() reaches only the relay handle"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="taskkill is the Windows tree kill")
def test_windows_stop_walks_the_process_tree(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(snakemake_runner, "_run_quiet", lambda cmd, **kw: calls.append(cmd))
    runner, _ = _runner(monkeypatch, tmp_path)
    process = runner.start()
    runner.stop()
    assert ["taskkill", "/F", "/T", "/PID", str(process.pid)] in calls


def test_wsl_stop_kills_the_tagged_tree_inside_the_vm(monkeypatch, tmp_path) -> None:
    # Killing the Windows wsl.exe relay leaves snakemake/STAR running inside the VM; the
    # run tag is the only handle on them.
    calls: list[list[str]] = []
    monkeypatch.setattr(snakemake_runner, "_run_quiet", lambda cmd, **kw: calls.append(cmd))
    tag = "BULKSEQ_RUN_TAG_0123456789abcdef"
    runner, _ = _runner(monkeypatch, tmp_path, use_wsl=True, distro="Ubuntu-24.04", run_tag=tag)
    runner.start()
    runner.stop()
    assert calls and calls[0][:4] == ["wsl", "-d", "Ubuntu-24.04", "--exec"]
    assert f"{tag}=1" in calls[0][-1]


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
    # Sample1 and sample1 are distinct dict keys but one file on NTFS, so the two
    # samples would overwrite each other's intermediates and the run would report
    # whichever wrote last: a silent wrong answer, not a crash.
    messages = validate_metadata(_sheet(["Sample1", "sample1"]), allow_pending_sra=True)
    failures = [m for m in messages if m["status"] == "FAIL"]
    assert any("capitalisation" in m["message"] for m in failures), messages


def test_distinct_sample_ids_still_pass() -> None:
    messages = validate_metadata(_sheet(["S1", "S2"]), allow_pending_sra=True)
    assert not any("capitalisation" in m["message"] for m in messages)


def test_exact_duplicates_still_reported_as_duplicates() -> None:
    messages = validate_metadata(_sheet(["S1", "S1"]), allow_pending_sra=True)
    assert any("Duplicate sample_id" in m["message"] for m in messages)


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
