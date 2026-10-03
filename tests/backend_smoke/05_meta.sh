#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/meta"
cd "$project"
snakemake --snakefile workflow/Snakefile --cores 1 \
    results/reports/meta_analysis_report.html --resources mem_mb=8000
