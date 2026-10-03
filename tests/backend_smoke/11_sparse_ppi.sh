#!/usr/bin/env bash
set -euo pipefail
project="${BULKSEQ_SMOKE_ROOT:?}/projects/ppi"
cache="$project/results/networks/string_cache"
for name in 4932.protein.aliases.v12.0.txt.gz 4932.protein.info.v12.0.txt.gz 4932.protein.links.v12.0.txt.gz; do
    test -s "$cache/$name"
done
test ! -e "$project/results/networks/string_ppi_edges.csv"
cd "$project"
ulimit -v 2097152
timeout --signal=TERM --kill-after=10s 600s snakemake \
    --snakefile workflow/Snakefile --cores 1 --allowed-rules network_string \
    results/networks/string_ppi_edges.csv --resources mem_mb=2048
