from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.config_models import WorkflowConfig, default_config
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


def test_deseq2_shrinkage_method_validation():
    import pytest
    from pydantic import ValidationError

    from app.core.config_models import Deseq2Config

    for ok in ("apeglm", "ashr", "normal"):
        assert Deseq2Config(shrinkage_method=ok).shrinkage_method == ok
    with pytest.raises(ValidationError):
        Deseq2Config(shrinkage_method="bogus")


def test_organellar_genes_default_and_round_trip():
    # Default keeps organellar genes; the choice survives a dump -> reload round-trip.
    assert WorkflowConfig().organellar_genes == "keep"
    for mode in ("keep", "discard", "separate"):
        wf = WorkflowConfig(organellar_genes=mode)
        assert WorkflowConfig(**wf.model_dump()).organellar_genes == mode
