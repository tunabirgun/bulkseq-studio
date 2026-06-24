from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from app.core.metadata import save_metadata
from app.core.paths import data_path
from app.core.project import ProjectManager
from app.core.reference_manager import catalog_entry_for_organism


def load_benchmark_catalog() -> list[dict[str, Any]]:
    with data_path("benchmark_datasets.yaml").open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload.get("benchmarks", [])


def get_benchmark(benchmark_id: str) -> dict[str, Any]:
    for benchmark in load_benchmark_catalog():
        if benchmark.get("id") == benchmark_id:
            return benchmark
    raise KeyError(f"Unknown benchmark dataset: {benchmark_id}")


def create_benchmark_project(benchmark_id: str, working_directory: Path, project_name: str | None = None) -> Path:
    benchmark = get_benchmark(benchmark_id)
    manager = ProjectManager()
    root = manager.create_project(project_name or str(benchmark["id"]), working_directory)
    samples = _samples_dataframe(benchmark)
    save_metadata(samples, root / "config" / "samples.auto_generated.tsv")
    save_metadata(samples, root / "config" / "samples.tsv")
    accessions = [str(row["original_accession"]) for row in benchmark["samples"]]
    (root / "config" / "sra_accessions.txt").write_text("\n".join(accessions) + "\n", encoding="utf-8")
    (root / "config" / "benchmark_manifest.yaml").write_text(yaml.safe_dump(benchmark, sort_keys=False), encoding="utf-8")

    cfg = manager.load_config(root)
    cfg.input.type = "sra"
    layouts = {str(sample.get("layout", "paired")) for sample in benchmark["samples"]}
    cfg.input.layout = layouts.pop() if len(layouts) == 1 else "mixed"  # type: ignore[assignment]
    cfg.reference.mode = "preset"
    cfg.reference.organism_name = str(benchmark["organism_name"])
    cfg.reference.genome_size_category = str(benchmark.get("genome_size_category", "custom"))
    # Seed the per-organism enrichment/PPI ids from the catalog so benchmark
    # projects match GUI presets (SRA mode, so keytype follows the catalog).
    entry = catalog_entry_for_organism(cfg.reference.organism_name)
    if entry is not None:
        cfg.enrichment.orgdb = entry.get("orgdb") or None
        cfg.enrichment.keytype = entry.get("enrichment_keytype") or None
        cfg.enrichment.kegg_organism = entry.get("kegg_organism") or None
        cfg.enrichment.gprofiler_organism = entry.get("gprofiler_organism") or None
        cfg.ppi.taxon = entry.get("string_taxon")
    ref = benchmark.get("reference", {})
    if ref:
        cfg.reference.source = ref.get("source")
        cfg.reference.release = str(ref.get("release")) if ref.get("release") else None
        cfg.reference.strain = ref.get("assembly")
        cfg.reference.annotation_format = ref.get("annotation_format", "gtf")
        cfg.reference.genome_fasta = "references/genome.fa"
        cfg.reference.annotation_file = "references/annotation.gtf"
        cfg.reference.genome_fasta_url = ref.get("genome_fasta_url")
        cfg.reference.annotation_gtf_url = ref.get("annotation_gtf_url")
    cfg.workflow.aligner = "STAR"
    cfg.workflow.quantifier = "featureCounts"
    # Differential-expression contrast from the benchmark entry (not hardcoded), so
    # each benchmark sets its own factor/levels. reference_level defaults to the
    # denominator (the control) when not given.
    contrast = benchmark.get("contrast", {})
    factor = str(contrast.get("factor", "condition"))
    numerator = str(contrast.get("numerator", ""))
    denominator = str(contrast.get("denominator", ""))
    reference_level = str(contrast.get("reference_level") or denominator)
    cfg.deseq2.design_formula = f"~ {factor}"
    if reference_level:
        cfg.deseq2.reference_level = {factor: reference_level}
    c0 = cfg.deseq2.contrasts[0]
    c0.factor = factor
    c0.numerator = numerator
    c0.denominator = denominator
    c0.name = str(contrast.get("name") or f"{numerator}_vs_{denominator}")
    manager.save_config(root, cfg)
    return root


def _samples_dataframe(benchmark: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sample in benchmark["samples"]:
        accession = str(sample["original_accession"])
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "original_accession": accession,
                "original_filename": f"{accession}.sra",
                "layout": sample["layout"],
                "fastq_1": f"data/raw/{accession}_1.fastq.gz",
                "fastq_2": f"data/raw/{accession}_2.fastq.gz",
                "detected_pair_id": accession,
                "condition": sample["condition"],
                "replicate": sample["replicate"],
                "batch": sample["batch"],
                "organism": benchmark["organism_name"],
                # GEO/experiment accessions and base_count are GEO/ENA-centric and
                # absent for some sources (e.g. DDBJ DRR runs), so they are optional.
                "geo_accession": sample.get("geo_accession", ""),
                "experiment_accession": sample.get("experiment_accession", ""),
                "read_count": sample.get("read_count", ""),
                "base_count": sample.get("base_count", ""),
                "fastq_1_url": sample["fastq_1_url"],
                "fastq_2_url": sample["fastq_2_url"],
            }
        )
    return pd.DataFrame(rows)
