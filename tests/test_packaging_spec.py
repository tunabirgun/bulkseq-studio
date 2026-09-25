from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest


def test_python_distribution_includes_the_application_and_shared_workflow_helper() -> None:
    repo = Path(__file__).resolve().parents[1]
    with (repo / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)

    assert metadata["build-system"] == {
        "requires": ["setuptools==84.0.0", "wheel==0.48.0"],
        "build-backend": "setuptools.build_meta",
    }
    assert metadata["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "app",
        "app.*",
        "workflow",
        "workflow.scripts",
    ]
    assert metadata["tool"]["setuptools"]["packages"]["find"]["namespaces"] is True
    package_data = metadata["tool"]["setuptools"]["package-data"]["app"]
    assert "data/*.yaml" in package_data
    assert "assets/**/*" in package_data


def test_release_version_declarations_are_synchronised() -> None:
    repo = Path(__file__).resolve().parents[1]
    expected = "0.32.1"
    constants = (repo / "app" / "constants.py").read_text(encoding="utf-8")
    assert f'APP_VERSION = "{expected}"' in constants
    assert f'WORKFLOW_VERSION = "{expected}"' in constants
    with (repo / "pyproject.toml").open("rb") as handle:
        assert tomllib.load(handle)["project"]["version"] == expected
    installer = (repo / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert f'#define MyAppVersion "{expected}"' in installer


def test_matrix_ci_installs_the_declared_no_isolation_build_requirements() -> None:
    repo = Path(__file__).resolve().parents[1]
    workflow = (repo / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "import tomllib" in workflow
    assert 'tomllib.load(handle)["build-system"]["requires"]' in workflow
    assert '"pip", "install", *build_requires' in workflow


def test_built_wheel_exposes_the_shared_count_validator_to_the_gui(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(repo / name, source / name)
    shutil.copytree(repo / "app", source / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    scripts = source / "workflow" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(
        repo / "workflow" / "scripts" / "count_matrix_validation.py",
        scripts / "count_matrix_validation.py",
    )

    wheel_dir = tmp_path / "wheel"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(source),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(wheel_dir.glob("*.whl"))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.ui import main_window; "
                "from workflow.scripts import count_matrix_validation; "
                "print(main_window.__file__); print(count_matrix_validation.__file__)"
            ),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(installed)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert str(installed) in probe.stdout


def test_pyinstaller_spec_resolves_repository_root() -> None:
    repo = Path(__file__).resolve().parents[1]
    spec_path = repo / "packaging" / "BulkSeqStudio.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    root_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ROOT" for target in node.targets)
    )
    expression = ast.Expression(root_assignment.value)
    resolved = Path(
        eval(  # noqa: S307 - expression comes from the repository-owned spec
            compile(expression, str(spec_path), "eval"),
            {"os": os, "SPECPATH": str(spec_path.parent)},
        )
    ).resolve()

    assert resolved == repo
    for required in (
        "app/main.py",
        "app/data",
        "app/assets",
        "workflow",
        "scripts",
        "examples",
    ):
        assert (resolved / required).exists(), required

    spec = spec_path.read_text(encoding="utf-8")
    assert "APP_VERSION = re.search" in spec
    assert 'if os.name == "nt"' in spec
    assert "VERSION_INFO = None" in spec
    assert "version=VERSION_INFO" in spec
    assert 'versioninfo.StringStruct("ProductName", "BulkSeq Studio")' in spec
    assert 'versioninfo.StringStruct("ProductVersion", APP_VERSION)' in spec
    for test_only in ('"pytest"', '"_pytest"', '"py"', '"pygments"'):
        assert test_only in spec


def test_windows_package_build_is_gated_by_the_frozen_webengine_probe() -> None:
    repo = Path(__file__).resolve().parents[1]
    script = (repo / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    installer = (repo / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert '$env:BULKSEQ_SELFTEST = "1"' in script
    assert '$env:BULKSEQ_SELFTEST_OUT = $selftestOut' in script
    assert "$selftest.ExitCode -ne 0" in script
    assert "-not $selftestResult.pass" in script
    assert "-not $selftestResult.webengine" in script
    assert "$selftestResult.nodes -ne 3" in script
    assert "if WizardSilent then" in installer
    assert "choice := IDYES" in installer


def test_linux_package_build_requires_all_three_verified_artifacts() -> None:
    repo = Path(__file__).resolve().parents[1]
    appimage_script = (repo / "packaging" / "build_appimage.sh").read_text(encoding="utf-8")
    workflow = (repo / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    release = (repo / "scripts" / "release.ps1").read_text(encoding="utf-8")

    assert 'zsync metadata was not produced' in appimage_script
    assert 'BulkSeqStudio-Portable-${VERSION}-linux-x86_64.tar.gz' in appimage_script
    assert 'tar -tzf "$PORTABLE"' in appimage_script

    assert "BulkSeqStudio-${VERSION}-x86_64.AppImage" in workflow
    assert "bulkseq-appimage-selftest.json" in workflow
    assert "result.get(\"webengine\") is True" in workflow
    assert "result.get(\"nodes\") == 3" in workflow
    assert "a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0" in workflow
    assert "digest.hexdigest() in header.lower()" in workflow

    assert "BulkSeqStudio-$version-x86_64.AppImage" in release
    assert 'BulkSeqStudio-Portable-$version-linux-x86_64.tar.gz' in release
    assert "$packageAssets = @($installer, $portable, $appImage, $zsync, $linuxPortable)" in release
    assert "SHA256SUMS.txt" in release
    assert "function Get-Sha256Hex" in release
    assert "[System.Security.Cryptography.SHA256]::Create()" in release
    assert "[System.IO.File]::OpenRead($path)" in release
    assert "$stream.Dispose()" in release
    assert "$algorithm.Dispose()" in release
    assert "$hash = Get-Sha256Hex $f" in release
    assert "$actual = Get-Sha256Hex $f" in release
    assert "$recorded.Count -ne $packageAssets.Count" in release


def test_installer_refuses_to_run_while_the_application_holds_its_mutex() -> None:
    import re
    import sys

    from app.constants import APP_MUTEX_NAME

    repo = Path(__file__).resolve().parents[1]
    installer = (repo / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert f"AppMutex={APP_MUTEX_NAME}" in installer
    assert "CloseApplications=force" in installer
    # Pre-mutex builds are guarded by the window title before InitializeSetup removes anything.
    assert "FindWindowByWindowName('{#MyAppName}')" in installer
    assert installer.index("FindWindowByWindowName") < installer.index("instLoc := InstalledLocation()")
    if not sys.platform.startswith("win"):
        return
    import ctypes

    from app.main import hold_instance_mutex

    handle = hold_instance_mutex()
    assert handle
    kernel32 = ctypes.windll.kernel32
    second = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    assert kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS: Setup will see the running app
    kernel32.CloseHandle(second)
    kernel32.CloseHandle(handle)
