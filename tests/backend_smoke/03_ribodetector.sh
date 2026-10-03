#!/usr/bin/env bash
set -euo pipefail
root="${BULKSEQ_SMOKE_ROOT:?}"
out="$root/ribodetector"
test ! -e "$out"
mkdir "$out"
ribodetector_cpu -t 1 -l 75 -i "$root/inputs/reads.fastq.gz" -e norrna \
    --chunk_size 1 -o "$out/non_rrna.fastq.gz" -r "$out/rrna.fastq.gz" \
    > "$out/classification.log" 2>&1
