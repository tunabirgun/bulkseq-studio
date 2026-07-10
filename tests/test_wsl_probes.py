from __future__ import annotations

import subprocess

import app.core.paths as paths
from app.core.paths import wsl_has_working_distro, wsl_home


def _completed(returncode: int, stdout) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["wsl"], returncode=returncode, stdout=stdout, stderr="")


def test_wsl_home_rejects_error_text_on_nonzero_exit(monkeypatch) -> None:
    # A distro that will not start makes wsl.exe print its mount error to stdout and exit non-zero.
    # That text must never be returned as $HOME (the bug that pasted it into the working directory).
    err = "'...ext4.vhdx' could not be attached to WSL2: ERROR_PATH_NOT_FOUND"
    monkeypatch.setattr(paths.subprocess, "run", lambda *a, **k: _completed(1, err))
    assert wsl_home("Ubuntu") is None


def test_wsl_home_rejects_non_posix_path_even_on_zero_exit(monkeypatch) -> None:
    monkeypatch.setattr(paths.subprocess, "run", lambda *a, **k: _completed(0, "some error text\n"))
    assert wsl_home("Ubuntu") is None


def test_wsl_home_accepts_absolute_posix_home(monkeypatch) -> None:
    monkeypatch.setattr(paths.subprocess, "run", lambda *a, **k: _completed(0, "/home/user\n"))
    assert wsl_home("Ubuntu") == "/home/user"


def test_working_distro_false_without_wsl_exe(monkeypatch) -> None:
    monkeypatch.setattr(paths.shutil, "which", lambda _: None)
    assert wsl_has_working_distro() is False


def test_working_distro_false_on_failed_launch(monkeypatch) -> None:
    # A registered-but-broken distro: wsl.exe present, but the launch fails with no marker.
    monkeypatch.setattr(paths.shutil, "which", lambda _: "C:/Windows/System32/wsl.exe")
    monkeypatch.setattr(paths.subprocess, "run", lambda *a, **k: _completed(1, b"\xff\xfeUTF16 error"))
    assert wsl_has_working_distro() is False


def test_working_distro_true_on_marker(monkeypatch) -> None:
    monkeypatch.setattr(paths.shutil, "which", lambda _: "C:/Windows/System32/wsl.exe")
    monkeypatch.setattr(paths.subprocess, "run", lambda *a, **k: _completed(0, b"BULKSEQ_WSL_OK\n"))
    assert wsl_has_working_distro() is True
