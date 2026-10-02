from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from workflow.scripts import verify_locked_environment as inventory


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    lock = tmp_path / "lock.yaml"
    lock.write_text(
        "name: bulkseq\ndependencies:\n"
        "  - pandoc==3.10=ha770c72_0\n"
        "  - salmon==1.10.3=haf24da9_3\n"
        "  - pip:\n    - mappy==2.30\n",
        encoding="utf-8",
    )
    prefix = tmp_path / "env"
    metadata = prefix / "conda-meta"
    metadata.mkdir(parents=True)
    (prefix / "lib/site-packages").mkdir(parents=True)
    for name, version, build in (("pandoc", "3.10", "ha770c72_0"),
                                 ("salmon", "1.10.3", "haf24da9_3")):
        (metadata / f"{name}.json").write_text(
            json.dumps({"name": name, "version": version, "build": build}),
            encoding="utf-8",
        )
    return lock, prefix


def _distribution(prefix: Path, version: str):
    return SimpleNamespace(version=version,
                           locate_file=lambda path: prefix / "lib/site-packages" / path)


def test_real_lock_requires_exact_versions_and_retains_every_conda_entry(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "workflow/envs/bulkseq.lock.yaml"
    conda, pip = inventory.read_lock(source)
    items = yaml.safe_load(source.read_text(encoding="utf-8"))["dependencies"]
    assert len(conda) == sum(isinstance(item, str) for item in items)
    assert len(pip) == sum(len(item["pip"]) for item in items if isinstance(item, dict))
    bad = tmp_path / "fuzzy.yaml"
    bad.write_text("name: bulkseq\ndependencies:\n  - pandoc=3.10=ha770c72_0\n",
                   encoding="utf-8")
    with pytest.raises(ValueError, match="pandoc"):
        inventory.read_lock(bad)


def test_inventory_cli_passes_then_refuses_patch_version_drift(tmp_path: Path,
                                                               monkeypatch, capsys) -> None:
    lock, prefix = _fixture(tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify_locked_environment.py", str(lock), str(prefix)])
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(inventory.importlib.metadata, "distribution",
                        lambda name: _distribution(prefix, {"mappy": "2.30"}[name]))
    assert inventory.main() == 0
    assert "LOCK_INVENTORY_PASS:2:1:0" in capsys.readouterr().out
    record = prefix / "conda-meta/pandoc.json"
    record.write_text(json.dumps({"name": "pandoc", "version": "3.10.2",
                                  "build": "ha770c72_0"}), encoding="utf-8")
    assert inventory.main() == 1
    output = capsys.readouterr()
    assert "pandoc" in output.err and "3.10.2" in output.err
    assert "LOCK_INVENTORY_PASS" not in output.out


@pytest.mark.parametrize("defect", ["missing", "duplicate", "malformed"])
def test_inventory_rejects_incomplete_or_unreadable_conda_metadata(tmp_path: Path,
                                                                   defect: str) -> None:
    lock, prefix = _fixture(tmp_path)
    metadata = prefix / "conda-meta"
    if defect == "missing":
        (metadata / "salmon.json").unlink()
    elif defect == "duplicate":
        (metadata / "duplicate.json").write_text(
            json.dumps({"name": "pandoc", "version": "3.10", "build": "ha770c72_0"}),
            encoding="utf-8",
        )
    else:
        (metadata / "salmon.json").write_text("{", encoding="utf-8")
    problems, extras = inventory.check_inventory(
        lock, prefix, lambda name: _distribution(prefix, {"mappy": "2.30"}[name]))
    assert not extras
    assert any(defect in problem or (defect == "malformed" and "unreadable" in problem)
               for problem in problems)


def test_additional_conda_package_is_reported_and_preserved(tmp_path: Path,
                                                            monkeypatch, capsys) -> None:
    lock, prefix = _fixture(tmp_path)
    extra = prefix / "conda-meta/extra.json"
    extra.write_text(json.dumps({"name": "extra", "version": "1", "build": "0"}),
                     encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify_locked_environment.py", str(lock), str(prefix)])
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(inventory.importlib.metadata, "distribution",
                        lambda name: _distribution(prefix, {"mappy": "2.30"}[name]))
    assert inventory.main() == 0
    assert capsys.readouterr().out.splitlines() == ["LOCK_INVENTORY_PASS:2:1:1",
                                                   "LOCK_INVENTORY_WARNING: retained additional Conda packages: extra"]
    assert extra.is_file()


def test_inventory_rejects_missing_or_wrong_pip_pin(tmp_path: Path) -> None:
    lock, prefix = _fixture(tmp_path)
    assert any("mappy" in problem for problem in inventory.check_inventory(
        lock, prefix, lambda name: _distribution(prefix, "2.31"))[0])

    def missing(name: str):
        raise LookupError(name)

    assert any("mappy" in problem for problem in inventory.check_inventory(
        lock, prefix, missing)[0])


def test_outside_only_pip_metadata_cannot_satisfy_selected_environment(tmp_path: Path) -> None:
    import venv

    lock = tmp_path / "lock.yaml"
    lock.write_text("name: probe\ndependencies:\n  - fixture==1=0\n"
                    "  - pip:\n    - release-probe-only==1.0\n", encoding="utf-8")
    prefix = tmp_path / "env"
    venv.EnvBuilder(with_pip=False).create(prefix)
    metadata = prefix / "conda-meta"
    metadata.mkdir()
    (metadata / "fixture.json").write_text(
        json.dumps({"name": "fixture", "version": "1", "build": "0"}), encoding="utf-8")
    outside = tmp_path / "outside"
    dist_info = outside / "release_probe_only-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: release-probe-only\nVersion: 1.0\n",
        encoding="utf-8",
    )
    python = prefix / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(outside), str(Path(yaml.__file__).parent.parent)))
    helper = Path(inventory.__file__)
    result = subprocess.run([str(python), str(helper), str(lock), str(prefix)],
                            env=environment, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode != 0
    assert "metadata outside selected environment" in result.stderr
    assert "LOCK_INVENTORY_PASS" not in result.stdout
