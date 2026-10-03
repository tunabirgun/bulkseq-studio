#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/custom_new"
cd "$project"
snakemake --snakefile workflow/Snakefile --cores 1 \
    results/gsva/gsva_scores.csv --resources mem_mb=8000
