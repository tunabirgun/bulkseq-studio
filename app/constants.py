from __future__ import annotations

APP_NAME = "BulkSeq Studio"
APP_VERSION = "0.32.1"
WORKFLOW_VERSION = "0.32.1"
# Named mutex held by a running application; packaging/installer.iss AppMutex must match.
APP_MUTEX_NAME = "BulkSeqStudioRunning"

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
    "library_name",
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
    # Free-text label the ENA/SRA importer writes; part of the schema, never a covariate.
    "sample_title",
    "title",
]

# Descriptive / provenance columns that are never candidate covariates: the sample identifier,
# read-file paths, ingest provenance, and the free-text labels (library_name, sample_title, title) that
# describe a sample rather than group it. The covariate-structure screen and the design helper
# both exclude these. workflow/scripts/check_covariate_structure.py keeps a literal copy because
# it runs inside the pipeline environment without `app` importable; the two are pinned together
# by tests/test_metadata_schema.py.
DESCRIPTIVE_METADATA_COLUMNS = frozenset({
    "sample_id",
    "library_name",
    "sample_title",
    "title",
    "fastq_1",
    "fastq_2",
    "gsm_accession",
    "original_accession",
    "original_filename",
    "detected_pair_id",
})

# Header of a freshly scaffolded sample sheet: the required schema with the optional library
# name after sample_id, plus the optional columns a new project starts with. Written by
# app/core/project.py and mirrored by scripts/capture_gui_matrix.py.
SCAFFOLD_METADATA_COLUMNS = (
    REQUIRED_METADATA_COLUMNS[:1]
    + ["library_name"]
    + REQUIRED_METADATA_COLUMNS[1:]
    + ["fastq_2", "replicate", "batch"]
)
