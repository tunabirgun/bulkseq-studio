#!/usr/bin/env bash
# Drop transcript records whose name (first header token) is not unique, keeping the
# first occurrence, and keep the tx2gene table in sync. gffread can emit non-unique
# "unassigned_transcript_N" auto-names for unnamed organellar/tRNA records (its counter
# is not global), which salmon's indexer rejects ("two references with the same name").
# No-op for transcriptomes that are already unique (Ensembl, most assemblies).
set -euo pipefail
raw_fa=$1; raw_t2g=$2; out_fa=$3; out_t2g=$4
awk '/^>/{n=$1; sub(/^>/,"",n); keep=!seen[n]++} keep' "$raw_fa" > "$out_fa"
awk -F'\t' '!seen[$1]++' "$raw_t2g" > "$out_t2g"
