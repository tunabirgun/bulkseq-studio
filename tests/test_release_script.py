"""Verified replacement for tests/test_packaging_spec.py::
test_release_script_creates_release_after_expected_missing_release_probe.

release.ps1 now asserts a clean tree and HEAD == @{u}, passes --target, and reads the
published release back, so the fixture needs a real git repo with an upstream and a fake gh
that answers `release view <tag> --json ...`. The two extra tests are the negative controls
for the new gates.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_FAKE_GH = (
    "@echo off\r\n"
    'echo %*>>"%FAKE_GH_LOG%"\r\n'
    'if "%1 %2"=="release view" (\r\n'
    '  if "%4"=="--json" (\r\n'
    "    powershell -NoProfile -Command \"$a=Get-ChildItem $env:RELEASE_OUTPUT -File | "
    "ForEach-Object { [pscustomobject]@{name=$_.Name; size=$(if ($env:RELEASE_BREAK -eq 'size' "
    "-and $_.Name -like '*.AppImage') {1} else {$_.Length})} } | Where-Object { -not "
    "($env:RELEASE_BREAK -eq 'drop' -and $_.name -like '*.zsync') }; [pscustomobject]"
    "@{assets=@($a); tagName='v0.28.0'; targetCommitish=$env:RELEASE_HEAD} "
    '| ConvertTo-Json -Depth 4"\r\n'
    "    exit /b 0\r\n"
    "  )\r\n"
    "  1>&2 echo release not found& exit /b 1\r\n"
    ")\r\n"
    'if "%1 %2"=="release create" exit /b 0\r\n'
    "exit /b 2\r\n"
)


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, f"git {args}: {done.stdout}{done.stderr}"
    return done.stdout.strip()


def _release_fixture(tmp_path: Path, *, dirty: bool = False, unpushed: bool = False):
    root = tmp_path / "release-root"
    (root / "scripts").mkdir(parents=True)
    (root / "app").mkdir()
    output = root / "installer_output"
    output.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "release.ps1", root / "scripts" / "release.ps1")
    (root / "app" / "constants.py").write_text('APP_VERSION = "0.28.0"\n', encoding="utf-8")
    (root / ".gitignore").write_text("installer_output/\n", encoding="utf-8")
    for name in ("BulkSeqStudio-Setup-0.28.0.exe", "BulkSeqStudio-Portable-0.28.0.zip",
                 "BulkSeqStudio-0.28.0-x86_64.AppImage",
                 "BulkSeqStudio-0.28.0-x86_64.AppImage.zsync",
                 "BulkSeqStudio-Portable-0.28.0-linux-x86_64.tar.gz"):
        (output / name).write_bytes(name.encode("ascii"))

    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("init", cwd=root)
    _git("config", "user.email", "release@example.com", cwd=root)
    _git("config", "user.name", "Tuna Birgun", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "release fixture", cwd=root)
    _git("remote", "add", "origin", str(remote), cwd=root)
    _git("push", "-u", "origin", _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root), cwd=root)
    if unpushed:
        (root / "extra.txt").write_text("x", encoding="utf-8")
        _git("add", "-A", cwd=root)
        _git("commit", "-m", "unpushed", cwd=root)
    if dirty:
        (root / "app" / "constants.py").write_text('APP_VERSION = "0.28.0"\n#\n', encoding="utf-8")
    return root, output


def _run_release(tmp_path: Path, root: Path, output: Path, break_mode: str = ""):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "gh.cmd").write_text(_FAKE_GH, encoding="ascii")
    log = tmp_path / "gh.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_LOG"] = str(log)
    env["RELEASE_OUTPUT"] = str(output)
    env["RELEASE_HEAD"] = _git("rev-parse", "HEAD", cwd=root)
    env["RELEASE_BREAK"] = break_mode
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts" / "release.ps1")],
        cwd=root, env=env, capture_output=True, text=True, timeout=120)
    return completed, (log.read_text(encoding="utf-8") if log.exists() else "")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_script_creates_release_after_expected_missing_release_probe(tmp_path: Path) -> None:
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "release view v0.28.0" in calls
    assert "release create v0.28.0" in calls
    # The tag must name the verified commit, and the release must be read back afterwards.
    assert f'--target {_git("rev-parse", "HEAD", cwd=root)}' in calls
    assert "--json assets,tagName,targetCommitish" in calls
    assert "Verified 6 assets" in completed.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
@pytest.mark.parametrize("state,message", [("dirty", "not clean"), ("unpushed", "upstream")])
def test_release_refuses_a_tree_that_does_not_match_the_remote(tmp_path: Path, state, message) -> None:
    root, output = _release_fixture(tmp_path, **{state: True})
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode != 0
    assert message in completed.stdout + completed.stderr
    assert not calls, "gh must not be called at all when the tree cannot be verified"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
@pytest.mark.parametrize("break_mode,message", [("drop", "missing asset"),
                                                ("size", "bytes on the release")])
def test_release_verification_fails_on_a_bad_published_asset(tmp_path: Path, break_mode, message) -> None:
    root, output = _release_fixture(tmp_path)
    completed, _ = _run_release(tmp_path, root, output, break_mode)
    assert completed.returncode != 0
    assert message in completed.stdout + completed.stderr


def test_checksum_manifest_uses_lf_so_sha256sum_c_can_verify_it(tmp_path: Path) -> None:
    """`sha256sum -c` reads a trailing CR as part of the file name and verifies nothing."""
    release = (REPO_ROOT / "scripts" / "release.ps1").read_text(encoding="utf-8")
    assert "Set-Content -LiteralPath $checksumManifest" not in release
    assert "[System.IO.File]::WriteAllText($checksumManifest" in release
