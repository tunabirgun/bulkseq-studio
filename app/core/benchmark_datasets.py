from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from app.core.metadata import save_metadata
from app.core.paths import data_path
from app.core.project import ProjectManager


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
    cfg.input.layout = "paired"
    cfg.reference.mode = "preset"
    cfg.reference.organism_name = str(benchmark["organism_name"])
    cfg.reference.genome_size_category = str(benchmark.get("genome_size_category", "custom"))
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
    cfg.deseq2.design_formula = "~ condition"
    cfg.deseq2.reference_level = {"condition": "untreated"}
    cfg.deseq2.contrasts[0].name = "cg8144_rnai_vs_untreated"
    cfg.deseq2.contrasts[0].factor = "condition"
    cfg.deseq2.contrasts[0].numerator = "cg8144_rnai"
    cfg.deseq2.contrasts[0].denominator = "untreated"
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
                "geo_accession": sample["geo_accession"],
                "experiment_accession": sample["experiment_accession"],
                "read_count": sample["read_count"],
                "base_count": sample["base_count"],
                "fastq_1_url": sample["fastq_1_url"],
                "fastq_2_url": sample["fastq_2_url"],
            }
        )
    return pd.DataFrame(rows)
