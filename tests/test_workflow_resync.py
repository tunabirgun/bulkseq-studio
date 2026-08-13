from pathlib import Path

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
    # Simulate a project scaffolded by an older app: stale metadata + a stale script.
    (root / "workflow" / "workflow_metadata.yaml").write_text(
        yaml.safe_dump({"workflow_version": "0.0.1", "copied_at": "2000-01-01T00:00:00"}),
        encoding="utf-8",
    )
    script.write_text("# STALE PLACEHOLDER\n", encoding="utf-8")
    synced = mgr.sync_workflow_if_outdated(root)
    assert synced == WORKFLOW_VERSION
    # Bundled script restored (carries the scoped directional fallback) and version updated.
    text = script.read_text(encoding="utf-8")
    assert "Up-regulated ORA selected" in text
    assert mgr.workflow_version_of(root) == WORKFLOW_VERSION
    # A second call is now a no-op.
    assert mgr.sync_workflow_if_outdated(root) is None


def test_sync_recopies_when_metadata_missing(tmp_path: Path) -> None:
    mgr = ProjectManager()
    root = mgr.create_project("resync_missing", tmp_path)
    (root / "workflow" / "workflow_metadata.yaml").unlink()
    assert mgr.workflow_version_of(root) is None
    assert mgr.sync_workflow_if_outdated(root) == WORKFLOW_VERSION
    assert mgr.workflow_version_of(root) == WORKFLOW_VERSION


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
