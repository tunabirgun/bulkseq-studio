import os
from pathlib import Path

import pytest
import yaml

import app.core.project as project_module
from app.constants import WORKFLOW_VERSION
from app.core.project import ProjectManager


def test_sync_noop_when_current(tmp_path: Path) -> None:
    mgr = ProjectManager()
    root = mgr.create_project("resync_current", tmp_path)
    # A freshly scaffolded project already records the current workflow version.
    assert mgr.workflow_version_of(root) == WORKFLOW_VERSION
    assert mgr.sync_workflow_if_outdated(root) is None


def test_sync_recopies_when_outdated(tmp_path: Path) -> None:
    mgr = ProjectManager()
    root = mgr.create_project("resync_old", tmp_path)
    script = root / "workflow" / "scripts" / "make_enrichment_figures.R"
    # Simulate a project scaffolded by an older app with a valid recorded tree.
    (root / "workflow" / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "workflow_digest": mgr.workflow_tree_digest(root / "workflow"),
                        "copied_at": "2000-01-01T00:00:00"}),
        encoding="utf-8",
    )
    synced = mgr.sync_workflow_if_outdated(root)
    assert synced == WORKFLOW_VERSION
    # Bundled script restored (carries the scoped directional fallback) and version updated.
    text = script.read_text(encoding="utf-8")
    assert "Up-regulated ORA selected" in text
    assert mgr.workflow_version_of(root) == WORKFLOW_VERSION
    assert len(list(root.glob(".workflow-backup-*"))) == 1
    # A second call is now a no-op.
    assert mgr.sync_workflow_if_outdated(root) is None


def test_sync_recopies_when_metadata_missing(tmp_path: Path) -> None:
    mgr = ProjectManager()
    root = mgr.create_project("resync_missing", tmp_path)
    (root / "workflow" / "workflow_metadata.yaml").unlink()
    assert mgr.workflow_version_of(root) is None
    assert mgr.sync_workflow_if_outdated(root) == WORKFLOW_VERSION
    assert mgr.workflow_version_of(root) == WORKFLOW_VERSION


def test_sync_upgrades_a_valid_legacy_digest_after_the_bundle_changes(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "bundled-workflow"
    script = source / "scripts" / "rule.py"
    script.parent.mkdir(parents=True)
    (source / "Snakefile").write_text("rule all:\n", encoding="utf-8")
    script.write_text("print('v1')\n", encoding="utf-8")
    monkeypatch.setattr(project_module, "workflow_root", lambda: source)
    manager = ProjectManager()
    root = tmp_path / "legacy-project"
    root.mkdir()
    manager.copy_workflow_metadata(root)
    workflow = root / "workflow"
    (workflow / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": WORKFLOW_VERSION,
                        "workflow_digest": manager.workflow_tree_digest(workflow)}),
        encoding="utf-8",
    )
    script.write_text("print('v2')\n", encoding="utf-8")

    assert manager.sync_workflow_if_outdated(root) == WORKFLOW_VERSION
    backups = list(root.glob(".workflow-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "scripts" / "rule.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert (workflow / "scripts" / "rule.py").read_text(encoding="utf-8") == "print('v2')\n"


def test_legacy_windows_and_posix_digests_are_portably_accepted(tmp_path: Path) -> None:
    import hashlib
    from pathlib import PurePosixPath, PureWindowsPath

    manager = ProjectManager()
    workflow = tmp_path / "workflow"
    for relative, text in (("a/x.py", "x\n"), ("a.py", "a\n"), ("Snakefile", "rule all:\n"),
                           ("__init__.py", "init\n"), ("mixed/Case.py", "case\n")):
        path = workflow / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    digests = manager._legacy_workflow_tree_digests(workflow)
    paths = [path for path in workflow.rglob("*") if path.is_file()]

    def historical_digest(path_type) -> str:
        digest = hashlib.sha256()
        ordered = sorted((path_type(*path.relative_to(workflow).parts), path) for path in paths)
        for _, path in ordered:
            digest.update(path.relative_to(workflow).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    assert digests == {historical_digest(PurePosixPath), historical_digest(PureWindowsPath)}
    for digest in digests:
        (workflow / "workflow_metadata.yaml").write_text(
            yaml.safe_dump({"workflow_version": WORKFLOW_VERSION, "workflow_digest": digest}),
            encoding="utf-8",
        )
        assert manager.verify_workflow_integrity(tmp_path) == manager.workflow_tree_digest(workflow)


def test_version_tuple_parsing() -> None:
    assert ProjectManager._version_tuple("0.8.10") > ProjectManager._version_tuple("0.8.9")
    assert ProjectManager._version_tuple("0.8.4") == (0, 8, 4)
    assert ProjectManager._version_tuple("garbage") == (0,)


def test_workflow_copy_and_digest_ignore_volatile_python_caches(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "bundled-workflow"
    script = source / "scripts" / "analysis.py"
    cache = source / "scripts" / "__pycache__" / "analysis.cpython-312.pyc"
    script.parent.mkdir(parents=True)
    cache.parent.mkdir(parents=True)
    (source / "Snakefile").write_text("rule all:\n", encoding="utf-8")
    script.write_text("print('stable')\n", encoding="utf-8")
    cache.write_bytes(b"first volatile cache")
    monkeypatch.setattr(project_module, "workflow_root", lambda: source)

    manager = ProjectManager()
    first_digest = manager._bundled_workflow_digest()
    cache.write_bytes(b"different volatile cache")
    assert manager._bundled_workflow_digest() == first_digest

    project_root = tmp_path / "project"
    project_root.mkdir()
    manager.copy_workflow_metadata(project_root)
    assert (project_root / "workflow" / "scripts" / "analysis.py").is_file()
    assert not (project_root / "workflow" / "scripts" / "__pycache__").exists()

    script.write_text("print('scientific change')\n", encoding="utf-8")
    assert manager._bundled_workflow_digest() != first_digest


def test_sync_refuses_current_metadata_when_project_workflow_was_edited(tmp_path: Path) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_drift", tmp_path)
    script = root / "workflow" / "scripts" / "make_enrichment_figures.R"
    original = script.read_bytes()
    script.write_bytes(original + b"\n# local edit\n")

    with pytest.raises(ValueError, match="changed since it was copied"):
        manager.sync_workflow_if_outdated(root)

    assert script.read_bytes().endswith(b"# local edit\n")
    assert not list(root.glob(".workflow-backup-*"))


def test_sync_refuses_digestless_edited_workflow(tmp_path: Path) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_legacy", tmp_path)
    workflow = root / "workflow"
    (workflow / "workflow_metadata.yaml").unlink()
    (workflow / "scripts" / "make_enrichment_figures.R").write_text("# legacy\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no valid recorded digest"):
        manager.sync_workflow_if_outdated(root)
    assert (workflow / "scripts" / "make_enrichment_figures.R").read_text(encoding="utf-8") == "# legacy\n"


def test_sync_copy_failure_leaves_project_workflow_unchanged(tmp_path: Path, monkeypatch) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_partial_copy", tmp_path)
    workflow = root / "workflow"
    original = (workflow / "scripts" / "make_enrichment_figures.R").read_bytes()
    (workflow / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "workflow_digest": manager.workflow_tree_digest(workflow)}),
        encoding="utf-8",
    )

    before = {path.name for path in root.iterdir()}

    def partial_copy(source, target, **kwargs):
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("copy interrupted")

    monkeypatch.setattr(project_module.shutil, "copytree", partial_copy)
    with pytest.raises(ValueError, match="stage"):
        manager.sync_workflow_if_outdated(root)

    assert (workflow / "scripts" / "make_enrichment_figures.R").read_bytes() == original
    assert not (workflow / "partial.txt").exists()
    assert {path.name for path in root.iterdir()} == before


def test_sync_stage_reservation_failure_leaves_project_workflow_unchanged(tmp_path: Path, monkeypatch) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_stage_reservation", tmp_path)
    workflow = root / "workflow"
    original = (workflow / "scripts" / "make_enrichment_figures.R").read_bytes()
    (workflow / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "workflow_digest": manager.workflow_tree_digest(workflow)}),
        encoding="utf-8",
    )

    def fail_reservation(*args, **kwargs):
        raise OSError("stage directory unavailable")

    monkeypatch.setattr(project_module.tempfile, "mkdtemp", fail_reservation)
    with pytest.raises(ValueError, match="Could not stage"):
        manager.sync_workflow_if_outdated(root)

    assert (workflow / "scripts" / "make_enrichment_figures.R").read_bytes() == original
    assert not list(root.glob(".workflow-backup-*"))


def test_sync_rejects_and_cleans_a_stage_that_exceeds_the_path_budget(tmp_path: Path, monkeypatch) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_stage_budget", tmp_path)
    workflow = root / "workflow"
    original = (workflow / "scripts" / "make_enrichment_figures.R").read_bytes()
    (workflow / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "workflow_digest": manager.workflow_tree_digest(workflow)}),
        encoding="utf-8",
    )
    before = {path.name for path in root.iterdir()}
    reserved: list[Path] = []
    real_mkdtemp = project_module.tempfile.mkdtemp

    def reserve_long_stage(*args, **kwargs):
        stage = Path(real_mkdtemp(prefix="stage-budget-", dir=kwargs["dir"]))
        reserved.append(stage)
        return stage

    monkeypatch.setattr(project_module.tempfile, "mkdtemp", reserve_long_stage)
    with pytest.raises(ValueError, match="short workflow stage"):
        manager.sync_workflow_if_outdated(root)

    assert len(reserved) == 1
    assert len(reserved[0].name) > len("workflow")
    assert not reserved[0].exists()
    assert (workflow / "scripts" / "make_enrichment_figures.R").read_bytes() == original
    assert {path.name for path in root.iterdir()} == before
    assert not list(root.glob(".workflow-backup-*"))


def test_sync_refuses_incomplete_bundle_without_touching_project_workflow(tmp_path: Path, monkeypatch) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_missing_entrypoint", tmp_path)
    workflow = root / "workflow"
    original = (workflow / "scripts" / "make_enrichment_figures.R").read_bytes()
    incomplete = tmp_path / "incomplete-bundle"
    (incomplete / "scripts").mkdir(parents=True)
    (incomplete / "scripts" / "rule.py").write_text("print('not runnable')\n", encoding="utf-8")
    monkeypatch.setattr(project_module, "workflow_root", lambda: incomplete)

    with pytest.raises(ValueError, match="non-empty Snakefile"):
        manager.sync_workflow_if_outdated(root)

    assert (workflow / "scripts" / "make_enrichment_figures.R").read_bytes() == original


def test_sync_promotion_failure_restores_project_workflow(tmp_path: Path, monkeypatch) -> None:
    manager = ProjectManager()
    root = manager.create_project("resync_promotion", tmp_path)
    workflow = root / "workflow"
    original = (workflow / "scripts" / "make_enrichment_figures.R").read_bytes()
    (workflow / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "workflow_digest": manager.workflow_tree_digest(workflow)}),
        encoding="utf-8",
    )
    real_replace = project_module.os.replace
    promotion_failed = False

    def fail_stage_promotion(source, target):
        nonlocal promotion_failed
        if (not promotion_failed and Path(source).parent == workflow.parent
                and Path(source) != workflow and Path(target) == workflow):
            promotion_failed = True
            raise OSError("promotion interrupted")
        return real_replace(source, target)

    monkeypatch.setattr(project_module.os, "replace", fail_stage_promotion)
    with pytest.raises(ValueError, match="restored"):
        manager.sync_workflow_if_outdated(root)

    assert (workflow / "scripts" / "make_enrichment_figures.R").read_bytes() == original
    assert not list(root.glob(".workflow-backup-*"))


def test_workflow_stage_stays_within_the_windows_path_budget(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "bundled-workflow"
    longest_relative = Path("scripts") / "make_custom_enrichment_figure.R"
    for relative, content in (
        (Path("Snakefile"), "rule all:\n"),
        (longest_relative, "print('custom')\n"),
        (Path("scripts") / "run_featurecounts_per_sample.py", "print('counts')\n"),
        (Path("scripts") / "run_meta_per_study_enrichment.R", "print('meta')\n"),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(project_module, "workflow_root", lambda: source)

    legacy_stage = ".workflow-stage-" + "a" * 32
    root_length = 260 - len(os.fspath(Path(legacy_stage) / longest_relative)) - 1
    component_length = root_length - len(os.fspath(tmp_path)) - 1
    assert 0 < component_length <= 255
    root = tmp_path / ("p" * component_length)
    root.mkdir()
    assert len(os.fspath(root / legacy_stage / longest_relative)) == 260

    stages: list[Path] = []
    real_mkdtemp = project_module.tempfile.mkdtemp

    def reserve_stage(*args, **kwargs):
        stage = Path(real_mkdtemp(*args, **kwargs))
        stages.append(stage)
        return stage

    monkeypatch.setattr(project_module.tempfile, "mkdtemp", reserve_stage)
    manager = ProjectManager()
    manager.copy_workflow_metadata(root)

    assert len(stages) == 1
    assert stages[0].parent == root
    assert len(stages[0].name) <= len("workflow")
    assert not stages[0].exists()
    assert (root / "workflow" / longest_relative).read_text(encoding="utf-8") == "print('custom')\n"
    assert manager.workflow_tree_digest(root / "workflow") == manager.workflow_tree_digest(source)
