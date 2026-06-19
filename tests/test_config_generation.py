from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.config_models import default_config
from app.core.project import ProjectManager


BASE = Path("manual_test_config")


def test_project_creation_writes_config():
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    root = ProjectManager().create_project("My Project", base)
    cfg = ProjectManager().load_config(root)
    assert cfg.project.name == "My_Project"
    assert (root / "config" / "samples.tsv").exists()
    assert (root / "workflow" / "Snakefile").exists()


def test_default_config_has_star_route():
    cfg = default_config("demo", BASE / "demo")
    assert cfg.workflow.aligner == "STAR"
    assert cfg.workflow.quantifier == "featureCounts"
