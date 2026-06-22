from __future__ import annotations

APP_NAME = "BulkSeq Studio"
APP_VERSION = "0.8.0"
WORKFLOW_VERSION = "0.8.0"

# Below this STAR uniquely-mapped %, the Run Monitor warns and offers to stop the
# run; a wrong reference or contaminated reads otherwise waste hours of alignment.
MIN_UNIQUE_MAPPED_WARN_PCT = 50.0

SAFE_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"

# WSL bioinformatics environment (created by scripts/setup_wsl_bioenv.sh).
WSL_ENV_NAME = "bulkseq"
WSL_MICROMAMBA = "$HOME/.local/bin/micromamba"
WSL_MAMBA_ROOT = "$HOME/micromamba"

PROJECT_DIRS = [
    "config",
    "data/raw",
    "data/sra",
    "data/trimmed",
    "data/rrna_clean",
    "data/external_links",
    "references",
    "results/qc",
    "results/aligned",
    "results/counts",
    "results/microarray",
    "results/deseq2",
    "results/enrichment",
    "results/networks",
    "results/stats",
    "results/figures",
    "results/reports",
    "logs",
    "benchmarks",
    "checks",
    "tmp",
    "workflow",
]

REQUIRED_METADATA_COLUMNS = ["sample_id", "condition", "layout", "fastq_1"]
OPTIONAL_METADATA_COLUMNS = [
    "fastq_2",
    "gsm_accession",
    "platform",
    "original_accession",
    "original_filename",
    "detected_pair_id",
    "replicate",
    "batch",
    "strain",
    "genotype",
    "treatment",
    "timepoint",
    "tissue",
    "organism",
    "library_prep",
    "sequencing_run",
]
