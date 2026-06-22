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


def test_yeast_benchmark_project_creation() -> None:
    catalog = load_benchmark_catalog()
    ids = [b["id"] for b in catalog]
    assert ids[0] == "pasilla_paired_subset"  # pasilla stays first (picker + test order)
    assert "sc_ume6_paired" in ids
    root = create_benchmark_project("sc_ume6_paired", Path("manual_test_benchmark") / uuid4().hex, "yeast_test")
    cfg = ProjectManager().load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    assert cfg.input.type == "sra"
    assert cfg.input.layout == "paired"
    assert cfg.reference.organism_name == "Saccharomyces cerevisiae"
    # Enrichment ids must resolve from the catalog by exact organism_name match;
    # if they don't, enrichment silently no-ops (the v0.8.0 trap). This is the
    # discriminating assertion.
    assert cfg.enrichment.kegg_organism == "sce"
    assert cfg.enrichment.orgdb == "org.Sc.sgd.db"
    assert cfg.ppi.taxon == 4932
    # The contrast levels must be real condition values or DESeq2 fails at runtime.
    c0 = cfg.deseq2.contrasts[0]
    assert c0.name == "ume6_delta_vs_WT"
    assert c0.numerator == "ume6_delta" and c0.denominator == "WT"
    assert {c0.numerator, c0.denominator} <= set(samples["condition"])
    assert cfg.deseq2.reference_level == {"condition": "WT"}
    assert cfg.deseq2.design_formula == "~ condition"
    assert samples.shape[0] == 4
    messages = validate_metadata(samples, allow_pending_sra=True)
    assert not any(m["status"] == "FAIL" for m in messages)
