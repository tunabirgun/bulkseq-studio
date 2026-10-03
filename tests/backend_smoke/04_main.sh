#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/main"
cd "$project"
snakemake --snakefile workflow/Snakefile --cores 1 \
    results/figures/volcano.png results/figures/volcano.svg \
    results/reports/results_report.html --resources mem_mb=8000
