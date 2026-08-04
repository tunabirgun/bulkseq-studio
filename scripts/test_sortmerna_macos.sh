#!/usr/bin/env bash
#
# SortMeRNA macOS / Apple Silicon compatibility test for BulkSeq Studio.
#
#   bash scripts/test_sortmerna_macos.sh
#
# WHY THIS EXISTS
#   sortmerna 4.3.7 -- the version pinned in workflow/envs/ -- has NO native arm64
#   build: its bundled SSW aligner includes <emmintrin.h> (x86 SSE2). Upstream
#   releases from 4.4.0 onward DO ship native arm64 Mach-O binaries. Before the pin
#   can move, we need to know whether the newer binary is a drop-in for the exact
#   command lines in workflow/rules/rrna.smk.
#
# WHAT IT CHECKS
#   1. The downloaded binary is genuinely native arm64 (not x86_64 under Rosetta).
#   2. Every CLI flag rrna.smk passes is still accepted.
#   3. The index step (--index 1) works.
#   4. Single-end filtering produces out/other.fq.gz and out/aligned.log.
#   5. Paired-end filtering produces out/other_fwd.fq.gz, out/other_rev.fq.gz,
#      out/aligned.log -- the exact names the rules `mv`.
#   6. Reads that match the reference are removed and non-matching reads survive,
#      so the filter is actually filtering and not just exiting 0.
#
#   Test data is generated locally and deterministically. Nothing but the SortMeRNA
#   release is downloaded, and nothing outside the work directory is touched.
#
# Override the versions to try:
#   SORTMERNA_VERSIONS="4.4.0 5.0.1" bash scripts/test_sortmerna_macos.sh

set -uo pipefail

VERSIONS="${SORTMERNA_VERSIONS:-4.4.0}"
WORK="${SORTMERNA_TEST_DIR:-$(pwd)/sortmerna_macos_test}"
PASS=0; FAIL=0; RESULTS=""

say()  { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS+1)); RESULTS="${RESULTS}
  PASS  $*"; printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); RESULTS="${RESULTS}
  FAIL  $*"; printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
hdr()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- preconditions
hdr "Environment"
say "  uname:  $(uname -s) $(uname -m)"
say "  macOS:  $(sw_vers -productVersion 2>/dev/null || echo 'n/a')"
say "  workdir: $WORK"

# The CLI/output contract is the same codebase on every platform, so this also runs
# on Linux. That is deliberate: it lets the flag and output-filename compatibility be
# settled off a Mac, leaving the Mac run to confirm only the arm64-native part.
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) ASSET_OS="Darwin"; EXPECT_ARM64=1 ;;
  Linux)  ASSET_OS="Linux";  EXPECT_ARM64=0
          say ""
          say "NOTE: running on Linux. This checks the CLI flags and output filenames,"
          say "      which are platform-independent. The native-arm64 check is skipped;"
          say "      re-run on Apple Silicon to confirm that part." ;;
  *) say ""; say "Unsupported platform: $OS"; exit 2 ;;
esac
if [ "$OS" = "Darwin" ] && [ "$ARCH" != "arm64" ]; then
  say ""
  say "NOTE: this Mac reports '$ARCH', not 'arm64'. The test still runs, but the"
  say "native-arm64 check is only meaningful on Apple Silicon."
fi

mkdir -p "$WORK" || { say "Cannot create $WORK"; exit 2; }
cd "$WORK" || exit 2

# ------------------------------------------------------------- test data (local)
# Deterministic sequences; the "rRNA" reference and the reads derived from it are
# generated here so the test needs no external database download.
gen_seq() {  # $1 = length, $2 = seed
  awk -v n="$1" -v s="$2" 'BEGIN{srand(s); b="ACGT";
    for(i=0;i<n;i++) printf substr(b, int(rand()*4)+1, 1); print ""}'
}

hdr "Generating local test data"
REF_SEQ="$(gen_seq 1200 11)"
printf '>test_rRNA_contig\n%s\n' "$REF_SEQ" > ref.fasta

# 25 reads that ARE substrings of the reference (should be classified as rRNA),
# and 25 unrelated reads (should survive into `other`).
: > r1.fastq
: > r2.fastq
i=0
while [ $i -lt 25 ]; do
  off=$(( i * 40 + 1 ))
  sub="$(printf '%s' "$REF_SEQ" | cut -c${off}-$((off+99)))"
  qual="$(printf 'I%.0s' $(seq 1 ${#sub}))"
  printf '@rrna_%d/1\n%s\n+\n%s\n' "$i" "$sub" "$qual" >> r1.fastq
  printf '@rrna_%d/2\n%s\n+\n%s\n' "$i" "$sub" "$qual" >> r2.fastq
  i=$((i+1))
done
j=0
while [ $j -lt 25 ]; do
  sub="$(gen_seq 100 $((900+j)))"
  qual="$(printf 'I%.0s' $(seq 1 100))"
  printf '@other_%d/1\n%s\n+\n%s\n' "$j" "$sub" "$qual" >> r1.fastq
  printf '@other_%d/2\n%s\n+\n%s\n' "$j" "$sub" "$qual" >> r2.fastq
  j=$((j+1))
done
gzip -kf r1.fastq r2.fastq
say "  ref.fasta (1200 bp), r1/r2.fastq.gz (50 reads each: 25 rRNA-like, 25 not)"

# ------------------------------------------------------------------- per version
for VER in $VERSIONS; do
  hdr "SortMeRNA $VER"
  TARBALL="sortmerna-${VER}-${ASSET_OS}.tar.gz"
  URL="https://github.com/sortmerna/sortmerna/releases/download/v${VER}/${TARBALL}"
  VDIR="$WORK/v$VER"
  rm -rf "$VDIR"; mkdir -p "$VDIR"

  if [ ! -f "$TARBALL" ]; then
    say "  downloading $URL"
    curl -fsSL -o "$TARBALL" "$URL" || { bad "$VER: download failed ($URL)"; continue; }
  fi
  tar xzf "$TARBALL" -C "$VDIR" 2>/dev/null || { bad "$VER: tar extract failed"; continue; }

  BIN="$(find "$VDIR" -type f -name sortmerna -perm -u+x 2>/dev/null | head -1)"
  [ -n "$BIN" ] || BIN="$(find "$VDIR" -type f -name sortmerna 2>/dev/null | head -1)"
  if [ -z "$BIN" ]; then bad "$VER: no sortmerna binary inside the tarball"; continue; fi
  chmod +x "$BIN" 2>/dev/null

  # -- 1. native architecture -------------------------------------------------
  ARCHS="$(lipo -archs "$BIN" 2>/dev/null || file -b "$BIN")"
  say "  binary:  $BIN"
  say "  arch:    $ARCHS"
  if [ "$EXPECT_ARM64" -eq 1 ]; then
    case "$ARCHS" in
      *arm64*) ok "$VER: native arm64 binary" ;;
      *)       bad "$VER: NOT arm64 (reports: $ARCHS) -- would need Rosetta" ;;
    esac
  else
    say "        (arm64 check skipped on $OS)"
  fi

  # Gatekeeper quarantine on a downloaded binary
  xattr -d com.apple.quarantine "$BIN" 2>/dev/null
  if ! "$BIN" --version >/dev/null 2>&1; then
    bad "$VER: binary will not execute (Gatekeeper, or wrong arch)"
    "$BIN" --version 2>&1 | head -3 | sed 's/^/        /'
    continue
  fi
  ok "$VER: binary executes; $("$BIN" --version 2>&1 | head -1)"

  # -- 2. index step, exactly as rules/rrna.smk:108 ---------------------------
  IDX="$VDIR/idx"; mkdir -p "$IDX"
  if "$BIN" --ref "$WORK/ref.fasta" --idx-dir "$IDX" --index 1 \
        -m 4096 --threads 2 > "$VDIR/index.log" 2>&1; then
    ok "$VER: index step (--ref --idx-dir --index 1 -m --threads)"
  else
    bad "$VER: index step failed -- see $VDIR/index.log"
    tail -12 "$VDIR/index.log" | sed 's/^/        /'
    continue
  fi

  # -- 3. single-end, exactly as rules/rrna.smk:129-134 -----------------------
  SEWD="$VDIR/se"; rm -rf "$SEWD"; mkdir -p "$SEWD/out"
  if "$BIN" --ref "$WORK/ref.fasta" --idx-dir "$IDX" --workdir "$SEWD" \
        --aligned "$SEWD/out/aligned" --other "$SEWD/out/other" \
        --reads "$WORK/r1.fastq.gz" \
        --fastx -m 4096 --threads 2 > "$VDIR/se.log" 2>&1; then
    ok "$VER: single-end run accepted every flag"
  else
    bad "$VER: single-end run failed -- see $VDIR/se.log"
    tail -12 "$VDIR/se.log" | sed 's/^/        /'
  fi
  [ -f "$SEWD/out/other.fq.gz" ] \
    && ok "$VER: SE produced out/other.fq.gz (the name rrna.smk moves)" \
    || bad "$VER: SE missing out/other.fq.gz -- found: $(ls "$SEWD/out" 2>/dev/null | tr '\n' ' ')"
  [ -f "$SEWD/out/aligned.log" ] \
    && ok "$VER: SE produced out/aligned.log" \
    || bad "$VER: SE missing out/aligned.log -- found: $(ls "$SEWD/out" 2>/dev/null | tr '\n' ' ')"

  if [ -f "$SEWD/out/other.fq.gz" ]; then
    KEPT=$(( $(gzip -dc "$SEWD/out/other.fq.gz" | wc -l) / 4 ))
    say "        reads surviving the filter: $KEPT of 50 (expect ~25)"
    if [ "$KEPT" -gt 0 ] && [ "$KEPT" -lt 50 ]; then
      ok "$VER: filter actually removed rRNA-like reads and kept the rest"
    else
      bad "$VER: suspicious survivor count $KEPT/50 -- filter may be a no-op"
    fi
  fi

  # -- 4. paired-end, exactly as rules/rrna.smk:158-164 -----------------------
  for PAIRED in --paired_in --paired_out; do
    PEWD="$VDIR/pe${PAIRED}"; rm -rf "$PEWD"; mkdir -p "$PEWD/out"
    if "$BIN" --ref "$WORK/ref.fasta" --idx-dir "$IDX" --workdir "$PEWD" \
          --aligned "$PEWD/out/aligned" --other "$PEWD/out/other" \
          --reads "$WORK/r1.fastq.gz" --reads "$WORK/r2.fastq.gz" \
          --fastx "$PAIRED" --out2 -m 4096 --threads 2 \
          > "$VDIR/pe${PAIRED}.log" 2>&1; then
      ok "$VER: paired-end ($PAIRED --out2) accepted every flag"
    else
      bad "$VER: paired-end ($PAIRED) failed -- see $VDIR/pe${PAIRED}.log"
      tail -12 "$VDIR/pe${PAIRED}.log" | sed 's/^/        /'
      continue
    fi
    for f in other_fwd.fq.gz other_rev.fq.gz aligned.log; do
      [ -f "$PEWD/out/$f" ] \
        && ok "$VER: PE $PAIRED produced out/$f" \
        || bad "$VER: PE $PAIRED missing out/$f -- found: $(ls "$PEWD/out" 2>/dev/null | tr '\n' ' ')"
    done
  done
done

# ------------------------------------------------------------------- conclusion
hdr "Summary"
printf '%s\n' "$RESULTS"
printf '\n  %d passed, %d failed\n\n' "$PASS" "$FAIL"
if [ "$FAIL" -eq 0 ]; then
  say "VERDICT: drop-in compatible. The rrna.smk command lines and output filenames"
  say "         are unchanged, so the pin can move to this version."
  say ""
  say "Send back: this whole output. Keep $WORK until then."
  exit 0
fi
say "VERDICT: NOT a clean drop-in. See the FAIL lines and the .log files under"
say "         $WORK. rules/rrna.smk needs adjusting, or try another version:"
say "           SORTMERNA_VERSIONS=\"5.0.1 6.0.2 7.0.0\" bash scripts/test_sortmerna_macos.sh"
exit 1
