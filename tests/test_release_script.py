"""Behavioural tests for scripts/release.ps1 against a fake GitHub CLI.

release.ps1 asserts a clean tree and HEAD == @{u}, gates on the CI workflows, downloads the
packages from the verified Build packages run, passes --target, and reads the published
release back. The fixture therefore needs a real git repo with an upstream, a directory
standing in for the run's artifacts, and a fake gh that answers `run list`, `run download`
and `release view <tag> --json ...`. Every test other than the happy paths is a negative
control for one of those gates.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_FAKE_GH = (
    "@echo off\r\n"
    'echo %*>>"%FAKE_GH_LOG%"\r\n'
    'if "%1 %2 %3"=="run list --workflow" goto :envlist\r\n'
    'if "%1 %2"=="run list" goto :runlist\r\n'
    'if "%1 %2"=="run download" goto :download\r\n'
    'if "%1 %2"=="release view" goto :releaseview\r\n'
    'if "%1 %2"=="release create" exit /b 0\r\n'
    "exit /b 2\r\n"
    "\r\n"
    ":envlist\r\n"
    "powershell -NoProfile -Command \"$c=$(if ($env:RELEASE_BREAK -eq 'env-fail') {'failure'} "
    "else {'success'}); $d=$(if ($env:RELEASE_BREAK -eq 'env-old') "
    "{(Get-Date).ToUniversalTime().AddDays(-60)} else {(Get-Date).ToUniversalTime()}); "
    "ConvertTo-Json -Depth 4 -InputObject @([pscustomobject]"
    "@{conclusion=$c; status='completed'; createdAt=$d.ToString('o')})\"\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":runlist\r\n"
    "powershell -NoProfile -Command \"$runs=@(); "
    "if ($env:RELEASE_BREAK -ne 'build-missing') { "
    "$runs += [pscustomobject]@{workflowName='Build packages'; databaseId=4242; "
    "status=$(if ($env:RELEASE_BREAK -eq 'in-progress') {'in_progress'} else {'completed'}); "
    "conclusion='success'} "
    "}; "
    "if ($env:RELEASE_BREAK -ne 'test-missing') { "
    "$runs += [pscustomobject]@{workflowName='Tests'; databaseId=4243; "
    "status=$(if ($env:RELEASE_BREAK -eq 'test-in-progress') {'in_progress'} else {'completed'}); "
    "conclusion=$(if ($env:RELEASE_BREAK -eq 'test-fail') {'failure'} else {'success'})} "
    "}; "
    "@($runs) | ConvertTo-Json -Depth 4\"\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":download\r\n"
    'if "%RELEASE_BREAK%"=="download-fail" goto :downloadfail\r\n'
    "powershell -NoProfile -Command \"Copy-Item -Path (Join-Path (Join-Path "
    "$env:RELEASE_ARTIFACTS '%~5') '*') -Destination '%~7' -Force\"\r\n"
    "exit /b 0\r\n"
    ":downloadfail\r\n"
    "1>&2 echo artifact not found\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":releaseview\r\n"
    'if not "%4"=="--json" goto :releasemissing\r\n'
    "powershell -NoProfile -Command \"$a=Get-ChildItem $env:RELEASE_OUTPUT -File | "
    "ForEach-Object { [pscustomobject]@{name=$_.Name; size=$(if ($env:RELEASE_BREAK -eq 'size' "
    "-and $_.Name -like '*.AppImage') {1} else {$_.Length})} } | Where-Object { -not "
    "($env:RELEASE_BREAK -eq 'drop' -and $_.name -like '*.zsync') }; [pscustomobject]"
    "@{assets=@($a); tagName='v0.28.0'; targetCommitish=$env:RELEASE_HEAD} "
    '| ConvertTo-Json -Depth 4"\r\n'
    "exit /b 0\r\n"
    ":releasemissing\r\n"
    "1>&2 echo release not found\r\n"
    "exit /b 1\r\n"
)

# What the Build packages run attaches, per artifact name. `gh run download -n <name>`
# lands these files at the root of the target directory (verified against the real
# artifacts of the v0.30.1 build run), so the fake copies one directory's contents.
CI_ARTIFACTS = {
    "BulkSeqStudio-windows": (
        "BulkSeqStudio-Setup-0.28.0.exe",
        "BulkSeqStudio-Portable-0.28.0.zip",
    ),
    "BulkSeqStudio-linux": (
        "BulkSeqStudio-0.28.0-x86_64.AppImage",
        "BulkSeqStudio-0.28.0-x86_64.AppImage.zsync",
        "BulkSeqStudio-Portable-0.28.0-linux-x86_64.tar.gz",
    ),
}
DOWNLOADED_NAMES = tuple(name for names in CI_ARTIFACTS.values() for name in names)


def _artifact_payload(name: str) -> bytes:
    """Content that only the downloaded copy of a file can have."""
    return f"from-ci:{name}".encode("ascii")


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, f"git {args}: {done.stdout}{done.stderr}"
    return done.stdout.strip()


FIXTURE_CHANGELOG = """# Changelog

## 0.28.0 - 2026-01-01

> **Scientific output changes.** Illustrative notice for the fixture.

### Changed

- A described change.

## 0.27.0 - 2025-12-01

- An older entry that must not reach the release body.
"""


def _release_fixture(tmp_path: Path, *, dirty: bool = False, unpushed: bool = False,
                     stale: bool = False, changelog: str | None = FIXTURE_CHANGELOG):
    root = tmp_path / "release-root"
    (root / "scripts").mkdir(parents=True)
    (root / "app").mkdir()
    output = root / "installer_output"
    output.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "release.ps1", root / "scripts" / "release.ps1")
    (root / "app" / "constants.py").write_text('APP_VERSION = "0.28.0"\n', encoding="utf-8")
    (root / ".gitignore").write_text("installer_output/\n", encoding="utf-8")
    if changelog is not None:
        (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")

    artifacts = tmp_path / "ci-artifacts"
    for artifact, names in CI_ARTIFACTS.items():
        (artifacts / artifact).mkdir(parents=True)
        for name in names:
            (artifacts / artifact / name).write_bytes(_artifact_payload(name))
    if stale:
        for name in DOWNLOADED_NAMES:
            (output / name).write_bytes(b"stale local build")

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
    env["RELEASE_ARTIFACTS"] = str(tmp_path / "ci-artifacts")
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
    assert f"Verified {len(DOWNLOADED_NAMES) + 1} assets" in completed.stdout


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


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_succeeds_when_both_ci_workflows_pass(tmp_path: Path) -> None:
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Tests: success" in completed.stdout
    assert "Build packages: success" in completed.stdout
    assert "run list" in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_fails_when_tests_workflow_failed(tmp_path: Path) -> None:
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output, "test-fail")
    assert completed.returncode != 0
    assert "Tests" in completed.stdout + completed.stderr
    assert "failure" in completed.stdout + completed.stderr
    assert "release create" not in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_fails_when_build_packages_workflow_missing(tmp_path: Path) -> None:
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output, "build-missing")
    assert completed.returncode != 0
    assert "Build packages" in completed.stdout + completed.stderr
    assert "not found" in completed.stdout + completed.stderr
    assert "release create" not in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_fails_when_workflows_not_completed(tmp_path: Path) -> None:
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output, "in-progress")
    assert completed.returncode != 0
    assert "in_progress" in completed.stdout + completed.stderr
    assert "expected completed" in completed.stdout + completed.stderr
    assert "release create" not in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_publishes_the_artifacts_of_the_verified_build_run(tmp_path: Path) -> None:
    """The five packages must come from the Build packages run the gate just approved."""
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for artifact in CI_ARTIFACTS:
        assert f"run download 4242 -n {artifact}" in calls
    for name in DOWNLOADED_NAMES:
        assert (output / name).read_bytes() == _artifact_payload(name)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_stops_when_an_artifact_cannot_be_downloaded(tmp_path: Path) -> None:
    """A failed download must leave nothing behind: the prior local build is cleared first,
    so there is no same-named file left for a later step to mistake for the CI package."""
    root, output = _release_fixture(tmp_path, stale=True)
    completed, calls = _run_release(tmp_path, root, output, "download-fail")
    assert completed.returncode != 0
    assert "run download failed" in completed.stdout + completed.stderr
    assert "release create" not in calls
    assert not any((output / name).exists() for name in DOWNLOADED_NAMES)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_replaces_a_stale_local_build_with_the_downloaded_one(tmp_path: Path) -> None:
    """A local build of the same version has the same names and would pass every check."""
    root, output = _release_fixture(tmp_path, stale=True)
    completed, _ = _run_release(tmp_path, root, output)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for name in DOWNLOADED_NAMES:
        assert (output / name).read_bytes() == _artifact_payload(name)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
@pytest.mark.parametrize("break_mode,message", [("env-fail", "failure"), ("env-old", "60 days old")])
def test_release_reports_the_environment_workflow_without_gating_on_it(
    tmp_path: Path, break_mode, message
) -> None:
    """Environment is path-filtered and scheduled: it is reported, never a release gate."""
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output, break_mode)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'run list --workflow Environment' in calls
    warnings = completed.stdout + completed.stderr
    assert "Environment workflow last run" in warnings
    assert message in warnings
    assert "release create v0.28.0" in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_refuses_to_publish_without_a_changelog_entry(tmp_path: Path) -> None:
    """A release page with no description is worse than a failed publish: it looks finished."""
    root, output = _release_fixture(tmp_path, changelog=None)
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode != 0
    assert "CHANGELOG.md" in completed.stdout + completed.stderr
    assert "release create" not in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_release_refuses_a_changelog_with_no_entry_for_this_version(tmp_path: Path) -> None:
    root, output = _release_fixture(
        tmp_path, changelog="# Changelog\n\n## 0.27.0 - 2025-12-01\n\n- Only an older entry.\n")
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode != 0
    assert "0.28.0" in completed.stdout + completed.stderr
    assert "release create" not in calls


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell release probe is Windows-specific")
def test_the_release_body_is_this_versions_changelog_entry(tmp_path: Path) -> None:
    """The notes file must carry this entry and stop at the next version's heading."""
    root, output = _release_fixture(tmp_path)
    completed, calls = _run_release(tmp_path, root, output)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "--notes-file" in calls, "the release body must come from a file, not an inline string"

    notes = Path(tempfile.gettempdir()) / "bulkseq-release-notes-0.28.0.md"
    assert notes.exists(), f"{notes} was not written"
    body = notes.read_text(encoding="utf-8")
    assert "Scientific output changes" in body, "the scientific notice must reach the release page"
    assert "A described change." in body
    assert "0.27.0" not in body, "the body ran past this version's entry into an older one"
    assert "## 0.28.0" not in body, "the heading is the release title; it should not repeat in the body"
    assert "SHA256SUMS.txt" in body, "the body must tell a reader how to verify a download"
