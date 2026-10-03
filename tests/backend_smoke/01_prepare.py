from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import random
import shutil
import sys

import numpy as np
import pandas as pd

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
from app.core.project import ProjectManager  # noqa: E402
import app.core.project as project_module  # noqa: E402


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"]).resolve()
projects = root / "projects"
inputs = root / "inputs"
if projects.exists() or inputs.exists():
    raise SystemExit("Synthetic projects or inputs already exist")
projects.mkdir(parents=True)
inputs.mkdir()
project_module.workflow_root = lambda: repo / "workflow"

genome_rng = random.Random(3400)
genome = "".join(genome_rng.choice("ACGT") for _ in range(2500))
genes = (("G1", "T1", 101, 700), ("G2", "T2", 901, 1500),
         ("G3", "T3", 1701, 2300))
(inputs / "genome.fa").write_text(">chrSynthetic\n" + genome + "\n", encoding="ascii")
with (inputs / "annotation.gtf").open("w", encoding="ascii", newline="\n") as handle:
    for gene, transcript, start, end in genes:
        handle.write(f'chrSynthetic\tsmoke\texon\t{start}\t{end}\t.\t+\t.\t'
                     f'gene_id "{gene}"; transcript_id "{transcript}";\n')
positions = (150, 220, 350, 950, 1040, 1180, 1750, 1820, 1940)
with gzip.open(inputs / "reads.fastq.gz", "wt", encoding="ascii", newline="\n") as handle:
    for index, start in enumerate(positions * 3, 1):
        read = genome[start - 1:start + 74]
        handle.write(f"@synthetic_read_{index}\n{read}\n+\n{'I' * len(read)}\n")

samples = [f"S{index:02d}" for index in range(1, 9)]
groups = ["control", "control", "treated", "treated"] * 2
metadata = pd.DataFrame({"sample_id": samples, "condition": groups,
                         "layout": ["single"] * 8, "fastq_1": [""] * 8,
                         "dataset": ["study_A"] * 4 + ["study_B"] * 4})
count_rng = np.random.default_rng(3401)
counts = pd.DataFrame({"gene_id": [f"gene{i:04d}" for i in range(1, 81)]})
condition = np.array([False, False, True, True] * 2)
scale = np.array([0.9, 1.1, 0.95, 1.05, 1.15, 0.85, 1.0, 1.0])
for sample_index, sample in enumerate(samples):
    means = np.array([80 + (gene * 11) % 140 for gene in range(1, 81)], dtype=float)
    means[:8] *= 4.0 if condition[sample_index] else 1.0
    means[8:16] *= 1.0 if condition[sample_index] else 4.0
    means *= scale[sample_index]
    dispersions = np.array([0.08 + ((gene * 7) % 12) / 100 for gene in range(1, 81)])
    size = 1.0 / dispersions
    counts[sample] = count_rng.negative_binomial(size, size / (size + means)).astype(int)
if counts.shape != (80, 9) or (counts[samples] < 0).any().any():
    raise SystemExit("Synthetic count matrix is invalid")
if not (counts.loc[0, samples[2:4]].mean() > counts.loc[0, samples[:2]].mean() and
        counts.loc[8, samples[:2]].mean() > counts.loc[8, samples[2:4]].mean()):
    raise SystemExit("Planted effect directions are absent")
counts.to_csv(inputs / "counts.tsv", sep="\t", index=False)
gmt = ("signal_A\tsynthetic set A\t" + "\t".join(counts["gene_id"].iloc[:20]) + "\n"
       "signal_B\tsynthetic set B\t" + "\t".join(counts["gene_id"].iloc[30:55]) + "\n")
(inputs / "custom.gmt").write_text(gmt, encoding="ascii")

manager = ProjectManager()
for name in ("main", "meta", "custom_old", "custom_new"):
    project = manager.create_project(name, projects)
    (project / "inputs").mkdir(exist_ok=True)
    shutil.copy2(inputs / "counts.tsv", project / "inputs/counts.tsv")
    metadata.to_csv(project / "config/samples.tsv", sep="\t", index=False)
    config = manager.load_config(project)
    config.input.type = "count_matrix"
    config.input.count_matrix = "inputs/counts.tsv"
    config.workflow.enrichment = False
    config.workflow.figures = name == "main"
    config.workflow.trimming = False
    config.workflow.meta_analysis = name == "meta"
    config.ppi.enabled = False
    config.deseq2.design_formula = "~ condition"
    config.deseq2.contrasts[0].factor = "condition"
    config.deseq2.contrasts[0].numerator = "treated"
    config.deseq2.contrasts[0].denominator = "control"
    config.resources.total_threads = 1
    config.resources.total_memory_gb = 8
    if name.startswith("custom_"):
        shutil.copy2(inputs / "custom.gmt", project / "inputs/custom.gmt")
        config.gene_sets.custom_gene_sets = "inputs/custom.gmt"
        config.workflow.gsva = True
    manager.save_config(project, config)

salmon = manager.create_project("salmon", projects)
(salmon / "inputs").mkdir(exist_ok=True)
for name in ("genome.fa", "annotation.gtf", "reads.fastq.gz"):
    shutil.copy2(inputs / name, salmon / "inputs" / name)
pd.DataFrame({"sample_id": ["S01"], "condition": ["control"],
              "layout": ["single"], "fastq_1": ["inputs/reads.fastq.gz"]}).to_csv(
                  salmon / "config/samples.tsv", sep="\t", index=False)
config = manager.load_config(salmon)
config.input.type = "fastq"
config.input.layout = "single"
config.workflow.aligner = "Salmon"
config.workflow.quantifier = "Salmon_tximport"
config.workflow.trimming = False
config.workflow.fastqc_pre_trim = False
config.workflow.fastqc_post_trim = False
config.workflow.enrichment = False
config.workflow.figures = False
config.ppi.enabled = False
config.reference.mode = "custom"
config.reference.organism_name = "Synthetic organism"
config.reference.genome_fasta = "inputs/genome.fa"
config.reference.annotation_file = "inputs/annotation.gtf"
config.reference.annotation_format = "gtf"
config.deseq2.design_formula = "~ 1"
config.deseq2.reference_level = {}
config.deseq2.contrasts = []
config.resources.total_threads = 1
config.resources.total_memory_gb = 8
manager.save_config(salmon, config)

ppi = manager.create_project("ppi", projects)
config = manager.load_config(ppi)
config.input.type = "count_matrix"
config.input.count_matrix = "inputs/counts.tsv"
config.reference.organism_name = "Saccharomyces cerevisiae"
config.workflow.enrichment = False
config.workflow.figures = False
config.workflow.meta_analysis = False
config.ppi.enabled = True
config.ppi.taxon = 4932
config.ppi.score_threshold = 400
config.ppi.string_version = "12.0"
config.ppi.max_seed_genes = 3
config.resources.total_threads = 1
config.resources.total_memory_gb = 8
manager.save_config(ppi, config)
(ppi / "inputs").mkdir(exist_ok=True)
ppi_counts = counts.iloc[[0, 1, 8]].copy()
ppi_counts["gene_id"] = ["CDC28", "CLB2", "SIC1"]
ppi_counts.to_csv(ppi / "inputs/counts.tsv", sep="\t", index=False)
metadata.to_csv(ppi / "config/samples.tsv", sep="\t", index=False)
de = ppi / "results/deseq2"
de.mkdir(parents=True, exist_ok=True)
rows = pd.DataFrame({"gene_id": ["CDC28", "CLB2", "SIC1"],
                     "symbol": ["CDC28", "CLB2", "SIC1"],
                     "log2FoldChange": [2.0, 1.8, -1.4],
                     "padj": [0.001, 0.002, 0.004],
                     "pvalue": [0.0005, 0.001, 0.002],
                     "baseMean": [100, 110, 130]})
rows.to_csv(de / "deseq2_results.csv", index=False)
rows.loc[rows["log2FoldChange"] > 0].to_csv(de / "upregulated_genes.csv", index=False)
rows.loc[rows["log2FoldChange"] < 0].to_csv(de / "downregulated_genes.csv", index=False)

(root / "fixture.json").write_text(json.dumps({
    "genome_bases": len(genome), "reads": len(positions) * 3,
    "count_genes": len(counts), "samples": len(samples),
    "genome_seed": 3400, "count_seed": 3401,
    "public_string_taxon": 4932,
    "public_string_seeds": list(rows["symbol"]),
}, indent=2) + "\n", encoding="utf-8")
print(f"Prepared {len(counts)} synthetic genes, {len(samples)} samples and {len(positions) * 3} reads")
