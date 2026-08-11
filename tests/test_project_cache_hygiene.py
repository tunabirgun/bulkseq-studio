from pathlib import Path

import app.core.project as project_module
from app.core.project import ProjectManager


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
