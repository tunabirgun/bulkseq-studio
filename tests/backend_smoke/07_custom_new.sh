#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/custom_new"
cd "$project"
snakemake --snakefile workflow/Snakefile --cores 1 \
    results/enrichment/custom_ora.csv results/enrichment/custom_gsea.csv \
    --resources mem_mb=8000
