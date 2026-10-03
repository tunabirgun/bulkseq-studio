#!/usr/bin/env bash
set -euo pipefail
checker="${1:?}"
flag="${2:?}"
reason="${3:?}"
root="${BULKSEQ_SMOKE_ROOT:?}"
mkdir -p "$root/negative"
log="$root/negative/$(basename "$checker" .py)-${flag#--}.log"
if python "tests/backend_smoke/$checker" "$flag" > "$log" 2>&1; then
    echo "Negative control unexpectedly exited zero: $checker $flag" >&2
    exit 1
fi
if ! grep -Fq -- "$reason" "$log"; then
    echo "Negative control failed for an unrelated reason: $checker $flag" >&2
    exit 1
fi
printf 'Negative control refused the injected defect: %s %s\n' "$checker" "$flag"
