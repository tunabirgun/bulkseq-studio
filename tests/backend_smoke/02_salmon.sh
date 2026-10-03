#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/salmon"
test -s "$project/config/config.yaml"
test ! -e "$project/results/counts/counts.txt"
cd "$project"
snakemake --snakefile workflow/Snakefile --cores 1 \
    results/counts/counts.txt --resources mem_mb=8000
