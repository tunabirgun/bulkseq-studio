#!/usr/bin/env bash
#
# Reclaim disk from a FINISHED run by removing the regenerable bulk.
#
#   bash scripts/reclaim_run_space.sh <project-dir>            # report only, deletes nothing
#   bash scripts/reclaim_run_space.sh <project-dir> --delete   # actually remove
#
# A completed run leaves several GB of intermediates that can be recreated from the sample
# sheet and the reference: downloaded FASTQ, trimmed and rRNA-filtered reads, BAM files, and
# the aligner index. The results that carry the science — counts, DESeq2 tables, enrichment,
# figures, the report, the sanity checks and the provenance record — are kept.
#
# It REFUSES to delete anything unless the run's key outputs are present and non-empty. A
# half-finished run's intermediates are the only way to resume it, so deleting them because
# a command was run in the wrong directory would cost hours.
#
# Before deleting, it records what it removed and the checksums of the results it kept, into
# reclaimed-space.txt inside the project. Provenance survives the cleanup.

set -uo pipefail

PROJECT="${1:-}"
MODE="${2:-report}"

if [ -z "$PROJECT" ] || [ ! -d "$PROJECT" ]; then
  echo "usage: $0 <project-dir> [--delete]" >&2
  exit 2
fi
if [ ! -f "$PROJECT/config/config.yaml" ]; then
  echo "Not a BulkSeq Studio project (no config/config.yaml): $PROJECT" >&2
  exit 2
fi

# Outputs that must exist before anything is removed. If the run did not get this far, its
# intermediates are still needed.
REQUIRED=(
  "results/counts/counts.txt"
  "results/deseq2/deseq2_results.csv"
)
# Regenerable, in rough order of size.
DISPOSABLE=(
  "references"                 # aligner index: rebuilt from the reference FASTA/GTF
  "data/raw"                   # downloaded FASTQ: re-fetched from the accessions
  "results/trimmed"
  "results/rrna_filtered"
  "results/aligned"            # BAM files
  ".snakemake/conda"
)

echo "project: $PROJECT"
echo
echo "== completeness check =="
missing=0
for rel in "${REQUIRED[@]}"; do
  if [ -s "$PROJECT/$rel" ]; then
    echo "  present  $rel"
  else
    echo "  MISSING  $rel"
    missing=$((missing + 1))
  fi
done
if [ "$missing" -gt 0 ]; then
  echo
  echo "Refusing to delete: this run did not produce its key outputs, so the intermediates" >&2
  echo "are still the only way to resume it." >&2
  exit 1
fi

echo
echo "== reclaimable =="
total_kb=0
present=()
for rel in "${DISPOSABLE[@]}"; do
  path="$PROJECT/$rel"
  [ -e "$path" ] || continue
  kb=$(du -sk "$path" 2>/dev/null | cut -f1)
  [ -n "$kb" ] || continue
  total_kb=$((total_kb + kb))
  present+=("$rel")
  printf '  %-24s %s\n' "$rel" "$(du -sh "$path" 2>/dev/null | cut -f1)"
done

if [ "${#present[@]}" -eq 0 ]; then
  echo "  (nothing to reclaim)"
  exit 0
fi
printf '\n  total: %s\n' "$(echo "$total_kb" | awk '{printf "%.1f GB", $1/1048576}')"

if [ "$MODE" != "--delete" ]; then
  echo
  echo "Report only. Re-run with --delete to remove them."
  exit 0
fi

RECORD="$PROJECT/reclaimed-space.txt"
{
  echo "Intermediates removed to reclaim disk"
  echo "date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "reclaimed: $(echo "$total_kb" | awk '{printf "%.1f GB", $1/1048576}')"
  echo
  echo "removed (regenerable from the sample sheet and reference):"
  for rel in "${present[@]}"; do echo "  $rel"; done
  echo
  echo "checksums of the retained results at the time of removal:"
  for f in results/counts/counts.txt results/deseq2/deseq2_results.csv \
           results/deseq2/upregulated_genes.csv results/deseq2/downregulated_genes.csv; do
    [ -f "$PROJECT/$f" ] && echo "  $(sha256sum "$PROJECT/$f" | cut -c1-16)  $f"
  done
} > "$RECORD"

echo
echo "== removing =="
for rel in "${present[@]}"; do
  rm -rf "${PROJECT:?}/$rel" && echo "  removed  $rel"
done
echo
echo "recorded in $(basename "$RECORD")"
df -h "$PROJECT" | tail -1
