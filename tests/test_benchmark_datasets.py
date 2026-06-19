from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.metadata import load_metadata, validate_metadata
from app.core.project import ProjectManager


def test_pasilla_benchmark_project_creation() -> None:
    catalog = load_benchmark_catalog()
    assert catalog[0]["id"] == "pasilla_paired_subset"
    root = create_benchmark_project("pasilla_paired_subset", Path("manual_test_benchmark") / uuid4().hex, "pasilla_test")
    cfg = ProjectManager().load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    assert cfg.input.type == "sra"
    assert cfg.input.layout == "paired"
    assert cfg.reference.organism_name == "Drosophila melanogaster"
    assert cfg.deseq2.contrasts[0].name == "cg8144_rnai_vs_untreated"
    assert samples.shape[0] == 4
    messages = validate_metadata(samples, allow_pending_sra=True)
    assert not any(m["status"] == "FAIL" for m in messages)
