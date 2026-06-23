from pathlib import Path

import yaml

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
    # Bundled script restored (carries the 0.8.3 fallback caption) and version updated.
    text = script.read_text(encoding="utf-8")
    assert "Up-regulated genes only" in text
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
