from __future__ import annotations

import ast
import os
from pathlib import Path
import tomllib


def test_python_distribution_discovers_only_the_application_package() -> None:
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
    ]
    package_data = metadata["tool"]["setuptools"]["package-data"]["app"]
    assert "data/*.yaml" in package_data
    assert "assets/**/*" in package_data


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
    assert "Get-FileHash -LiteralPath $f -Algorithm SHA256" in release
    assert "$recorded.Count -ne $packageAssets.Count" in release
