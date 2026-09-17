#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-bulkseq}"
PROFILE="${2:-core}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Host platform. The same script serves WSL2 on Windows and native Linux, and the
# micromamba build to download keys off the detected architecture.
# ---------------------------------------------------------------------------
HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"
# micromamba is pinned to an exact version and verified against a per-platform SHA-256.
# `/latest` was a moving target: every machine could get a different bootstrap, and a
# corrupted or substituted download was installed unverified. Bump the version and BOTH
# digests together (download the pinned URL and hash it; the digests are of the .tar.bz2).
MM_VERSION="2.9.0"
case "${HOST_OS}/${HOST_ARCH}" in
  Linux/x86_64)
    MM_PLATFORM="linux-64"
    MM_SHA256="8761c382127e6363bd9e0a2451aa3ef90d071a79133f736e2f759a3bf13040dd" ;;
  Linux/aarch64|Linux/arm64)
    MM_PLATFORM="linux-aarch64"
    MM_SHA256="e705ffeed90ce0659eb546e4b1e1028c9eaf0bc9cc854867b19ac5ce0ba5852f" ;;
  *)
    echo "Unsupported platform: ${HOST_OS} ${HOST_ARCH}." >&2
    echo "BulkSeq Studio runs the pipeline on Linux (x86_64 or aarch64), natively or" >&2
    echo "inside WSL2 on Windows." >&2
    exit 1 ;;
esac
FIXED_LOCK_ENV_FILE=""
case "$PROFILE" in
full)
  # Install the full R/Bioconductor + CLI stack from the pinned LOCK, not the floating
  # bulkseq_full.yaml. A fresh solve of the float spec can silently drop a transitive
  # dependency (e.g. GO.db) and leave clusterProfiler unable to load; the lock pins every
  # package and build so the env reproduces exactly. bulkseq_full.yaml stays as a fallback
  # for what the lock cannot satisfy: a build garbage-collected from the channels, or a host
  # that is not linux-64 (the lock is a linux-64 snapshot).
  FIXED_LOCK_ENV_FILE="$REPO_DIR/workflow/envs/bulkseq.lock.yaml"
  ENV_FILE="$FIXED_LOCK_ENV_FILE"
  FALLBACK_ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_full.yaml"
  if [ "$MM_PLATFORM" != "linux-64" ]; then
    # The lock is a linux-64 snapshot pinned to exact builds, so it cannot solve on
    # any other subdir. Go straight to the float spec rather than burning a long
    # solve on a guaranteed failure and reporting it as an error.
    echo "Note: ${MM_PLATFORM} host -- installing from the float spec, not the linux-64 lock."
    ENV_FILE="$FALLBACK_ENV_FILE"
    FALLBACK_ENV_FILE=""
  fi
  ;;
core)
  ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_core.yaml"
  FALLBACK_ENV_FILE=""
  ;;
*)
  echo "Unsupported environment profile: $PROFILE (expected 'core' or 'full')." >&2
  exit 2
  ;;
esac
# GUI launchers pass a common per-user location. A direct shell invocation still uses
# the XDG data convention, keeping the source checkout and a mounted AppImage read-only.
LOG_DIR="${BULKSEQ_SETUP_LOG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/BulkSeq Studio/logs}"
LOG_FILE="$LOG_DIR/wsl_bioenv_install.log"
MAMBA_ROOT="$HOME/micromamba"
MICROMAMBA="$HOME/.local/bin/micromamba"
MM_URL="https://micro.mamba.pm/api/micromamba/${MM_PLATFORM}/${MM_VERSION}"

verify_sha256() {  # verify_sha256 <file> <expected-hex>
  local actual=""
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$1" | cut -d' ' -f1)"
  elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$1" | cut -d' ' -f1)"
  else
    echo "No sha256sum/shasum available to verify the micromamba download." >&2
    return 1
  fi
  if [ "$actual" != "$2" ]; then
    echo "micromamba checksum mismatch: expected $2, got $actual" >&2
    return 1
  fi
}

# `timeout` comes from GNU coreutils and is not guaranteed: a minimal container image
# or a BusyBox userland may not have it. The tool verification below wraps each probe in
# an `if`, so `set -e` never fires -- a missing `timeout` would silently make EVERY tool
# report "not found or timed out" on a correctly installed system. Fall back to a
# portable background-and-kill wait.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
else
  TIMEOUT_BIN=""
fi

run_limited() {  # run_limited <seconds> <command...>; exit 124 on timeout, like timeout(1)
  local secs="$1"; shift
  if [ -n "$TIMEOUT_BIN" ]; then
    "$TIMEOUT_BIN" "$secs" "$@"
    return $?
  fi
  "$@" &
  local pid=$! waited=0 rc=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$secs" ]; then
      kill -TERM "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" || rc=$?
  return $rc
}

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "BulkSeq Studio WSL bioinformatics setup"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Repository: $REPO_DIR"
echo "Profile: $PROFILE"
echo "Environment file: $ENV_FILE"
echo "Log file: $LOG_FILE"

# ---------------------------------------------------------------------------
# Serialize concurrent setup invocations. Two setups resolving at the same time
# both write the shared shard cache without holding micromamba's transaction
# lock (that lock only guards the link phase), and an interrupted fetch leaves a
# truncated/empty JSON shard that breaks every later run with parse_error.101.
# A single atomic mkdir lock makes a second invocation wait for the first.
# mkdir is used rather than flock so the lock is portable to macOS (no flock).
# ---------------------------------------------------------------------------
mkdir -p "$MAMBA_ROOT"
LOCK_DIR="$MAMBA_ROOT/.bulkseq_setup.lock"
release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
acquire_lock() {
  local waited=0 announced=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    # Stale-lock recovery: take over if the recorded owner process is gone.
    if [ -f "$LOCK_DIR/pid" ]; then
      local owner
      owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
      if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
        echo "Removing stale setup lock from dead process $owner."
        rm -rf "$LOCK_DIR" 2>/dev/null || true
        continue
      fi
    fi
    if [ "$announced" -eq 0 ]; then
      echo "Another BulkSeq setup is already running; waiting for it to finish..."
      announced=1
    fi
    sleep 3
    waited=$((waited + 3))
    if [ "$waited" -ge 1800 ]; then
      echo "Timed out after 30 min waiting for the other setup to finish. Exiting."
      exit 4
    fi
  done
  echo $$ > "$LOCK_DIR/pid"
  trap release_lock EXIT
}
acquire_lock

mkdir -p "$HOME/.local/bin"

# Extract bin/micromamba from the .tar.bz2 at $1 into $2 after verifying its SHA-256
# against $3, using only the python3 standard library (urllib + hashlib + tarfile/bz2).
# No curl, bzip2, apt or sudo needed. The binary is written to a temp path, made
# executable and moved into place with os.replace, so an interrupted or mismatched
# download never leaves a partial micromamba on PATH.
bootstrap_with_python3() {
  python3 - "$1" "$2" "$3" <<'PY'
import hashlib, http.client, io, os, stat, sys, tarfile, urllib.error, urllib.request
url, dest, expected = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = urllib.request.urlopen(url, timeout=180).read()
except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
    raise SystemExit(f"download failed: {getattr(exc, 'reason', exc)}")
digest = hashlib.sha256(data).hexdigest()
if digest != expected:
    raise SystemExit(f"micromamba checksum mismatch: expected {expected}, got {digest}")
with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
    extracted = tf.extractfile(tf.getmember("bin/micromamba"))
    if extracted is None:
        raise SystemExit("bin/micromamba not found in archive")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".partial"
    with open(tmp, "wb") as out:
        out.write(extracted.read())
os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
os.replace(tmp, dest)
print("micromamba", expected[:12], "verified and written to", dest)
PY
}

# ---------------------------------------------------------------------------
# Stage 1/3: Install micromamba into the user account (no sudo required).
# micromamba is a single static binary that manages every later tool itself, so
# the bootstrap only has to download and unpack one .tar.bz2. python3 does that
# from its standard library and ships on a default Ubuntu WSL, so the normal
# path never calls apt or asks for a sudo password. curl/wget+bzip2, then
# passwordless apt, are kept as fallbacks for minimal distributions.
# ---------------------------------------------------------------------------
echo ""
echo "Stage 1/3: Installing micromamba (user-level, no sudo)"
if [ -x "$MICROMAMBA" ]; then
  echo "micromamba already installed at $MICROMAMBA"
else
  installed=0
  fetch_failed=""

  echo "micromamba $MM_VERSION ($MM_PLATFORM), sha256 $MM_SHA256"

  # Preferred: python3 standard library. No system packages, no sudo.
  if command -v python3 >/dev/null 2>&1; then
    echo "Downloading micromamba with python3 (no system packages needed)..."
    if py_out="$(bootstrap_with_python3 "$MM_URL" "$MICROMAMBA" "$MM_SHA256" 2>&1)"; then
      echo "$py_out"
      installed=1
    else
      echo "$py_out"
      fetch_failed="python3: $(printf '%s\n' "$py_out" | tail -n 1)"
      echo "python3 bootstrap failed; trying curl/wget."
    fi
  fi

  # Fallback: curl or wget into a temp file, verified before it is unpacked (tar -j needs
  # bzip2). Piping the download straight into tar would install unverified bytes.
  if [ "$installed" -eq 0 ] && command -v bzip2 >/dev/null 2>&1; then
    mm_archive="$(mktemp)"
    mm_fetched=0
    if command -v curl >/dev/null 2>&1; then
      echo "Downloading micromamba with curl..."
      if curl_err="$(curl -fSsL -o "$mm_archive" "$MM_URL" 2>&1)"; then mm_fetched=1; else fetch_failed="curl: ${curl_err:-download failed}"; fi
    elif command -v wget >/dev/null 2>&1; then
      echo "Downloading micromamba with wget..."
      if wget_err="$(wget -qO "$mm_archive" "$MM_URL" 2>&1)"; then mm_fetched=1; else fetch_failed="wget: ${wget_err:-download failed}"; fi
    fi
    if [ "$mm_fetched" -eq 1 ] && verify_sha256 "$mm_archive" "$MM_SHA256"; then
      mm_dir="$(mktemp -d)"
      if tar -xjf "$mm_archive" -C "$mm_dir" bin/micromamba; then
        chmod +x "$mm_dir/bin/micromamba"
        mv -f "$mm_dir/bin/micromamba" "$MICROMAMBA"
        installed=1
      fi
      rm -rf "$mm_dir"
    fi
    rm -f "$mm_archive"
  fi

  # Last resort: install python3 via apt, but only if sudo needs no password.
  # This installer has no terminal to type a sudo password into, so an
  # interactive sudo would hang; we skip it and print instructions instead.
  if [ "$installed" -eq 0 ] && sudo -n true 2>/dev/null; then
    echo "Installing python3 via passwordless sudo apt..."
    sudo apt-get update
    sudo apt-get install -y python3 ca-certificates
    if command -v python3 >/dev/null 2>&1; then
      if py_out="$(bootstrap_with_python3 "$MM_URL" "$MICROMAMBA" "$MM_SHA256" 2>&1)"; then
        echo "$py_out"
        installed=1
      else
        echo "$py_out"
        fetch_failed="python3: $(printf '%s\n' "$py_out" | tail -n 1)"
      fi
    fi
  fi

  if [ "$installed" -eq 0 ] && [ -n "$fetch_failed" ]; then
    echo ""
    echo "ACTION REQUIRED: micromamba could not be downloaded from"
    echo "    $MM_URL"
    echo "The download failed with: $fetch_failed"
    echo "Check the network connection and any proxy settings (http_proxy/https_proxy),"
    echo "then click \"Install / repair core environment\" again."
    echo ""
    exit 3
  fi

  if [ "$installed" -eq 0 ]; then
    echo ""
    echo "ACTION REQUIRED: micromamba could not be installed automatically."
    echo "This WSL distribution has no python3 and no curl/wget+bzip2, and sudo needs"
    echo "a password that this installer cannot type. Open a WSL terminal yourself and"
    echo "run the following, then click \"Install / repair core environment\" again:"
    echo ""
    echo "    sudo apt-get update && sudo apt-get install -y python3 ca-certificates"
    echo ""
    exit 3
  fi
fi

export MAMBA_ROOT_PREFIX="$MAMBA_ROOT"

echo ""
echo "Stage 2/3: Creating/updating the BulkSeq micromamba environment"

# The full profile installs from the pinned lock so the R/Bioconductor stack reproduces
# exactly (a re-solve of the float spec can drop a transitive dep like GO.db). An in-place
# `env update` can still leave a package installed-but-unloadable (a build GC, an r-base ABI
# drift); Stage 2b below verifies the stack actually loads. It never destroys that environment
# implicitly: BULKSEQ_REBUILD=1 is the explicit authorization for a clean rebuild up front.
REBUILD="${BULKSEQ_REBUILD:-0}"
case "$REBUILD" in
  0|1) ;;
  *) echo "BULKSEQ_REBUILD must be 0 or 1." >&2; exit 2 ;;
esac

env_exists() { "$MICROMAMBA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; }

remove_env() {
  echo "Removing existing environment '$ENV_NAME' for a clean install..."
  "$MICROMAMBA" env remove --yes -n "$ENV_NAME" || rm -rf "$MAMBA_ROOT/envs/$ENV_NAME"
}

# Create the env from $1 if absent, otherwise update it in place. Returns micromamba's exit code.
create_or_update() {
  local env_file="$1"
  if env_exists; then
    echo "Updating environment '$ENV_NAME' from $(basename "$env_file")"
    "$MICROMAMBA" env update --yes -n "$ENV_NAME" -f "$env_file"
  else
    echo "Creating environment '$ENV_NAME' from $(basename "$env_file")"
    "$MICROMAMBA" create --yes -n "$ENV_NAME" -f "$env_file"
  fi
}

# Drop only the index/shard cache (not downloaded package tarballs) to recover
# from a truncated JSON shard left by an interrupted or concurrent fetch -- the
# state that makes every run die with "parse error ... empty input".
clean_index_cache() {
  echo "Cleaning the micromamba index cache to recover from a corrupted shard..."
  "$MICROMAMBA" clean --index-cache --yes 2>/dev/null || true
  rm -rf "$MAMBA_ROOT/pkgs/cache" 2>/dev/null || true
}

# One create/update from $1, with a single cache-clean retry (the first failure is usually a
# truncated shard). Returns non-zero only if both attempts fail.
attempt_install() {
  local env_file="$1"
  if create_or_update "$env_file"; then return 0; fi
  echo "Environment step failed; cleaning the index cache and retrying once."
  clean_index_cache
  create_or_update "$env_file"
}

# Full profile only: does the R/Bioconductor stack actually LOAD? Reads a stdout marker,
# NOT the exit code -- `micromamba run` can mask a non-zero status. A dropped GO.db or an
# r-base ABI drift leaves these installed-but-unloadable, which is what kills enrichment
# mid-run. Core/empty profile -> trivially "loads".
R_STACK_PROBE='q<-c("DESeq2","edgeR","limma","GSVA","clusterProfiler","GO.db","DOSE","enrichplot","fgsea","STRINGdb","apeglm","ashr","GEOquery","affy","AnnotationDbi","KEGGREST","Biobase","S4Vectors","SummarizedExperiment","metaRNASeq","metafor","HTSFilter","tximport","gprofiler2","ggplot2","ggrepel","ggnewscale","ggridges","gtable","pheatmap","igraph","jsonlite","matrixStats","scales","svglite","systemfonts","RColorBrewer","msigdbr","org.At.tair.db","org.Bt.eg.db","org.Ce.eg.db","org.Dm.eg.db","org.Dr.eg.db","org.Gg.eg.db","org.Hs.eg.db","org.Mm.eg.db","org.Rn.eg.db","org.Sc.sgd.db","org.Ss.eg.db"); ok<-function(p) isTRUE(tryCatch(suppressWarnings(suppressMessages(requireNamespace(p,quietly=TRUE))),error=function(e)FALSE)); bad<-q[!vapply(q,ok,logical(1))]; cat(if(length(bad)) paste0("R_STACK_BAD:",paste(bad,collapse=",")) else "R_STACK_OK")'
r_stack_loads() {
  [ "$PROFILE" = "full" ] || return 0
  local out
  out="$("$MICROMAMBA" run -n "$ENV_NAME" Rscript --vanilla -e "$R_STACK_PROBE" 2>/dev/null || true)"
  if echo "$out" | grep -q "R_STACK_OK"; then
    return 0
  fi
  echo "${out:-R_STACK_BAD:probe produced no output}" >&2
  return 1
}

# Stage 2a: install/repair from the lock (or core.yaml). Fall back to the floating spec only
# for what the lock cannot satisfy (a GC'd build or a non-linux-64 host).
if [ "$REBUILD" = "1" ] && env_exists; then
  remove_env
fi
INSTALLED_ENV_FILE="$ENV_FILE"
if attempt_install "$ENV_FILE"; then
  :
elif [ -n "$FALLBACK_ENV_FILE" ] && [ "$FALLBACK_ENV_FILE" != "$ENV_FILE" ]; then
  echo "Locked install failed (a pinned build may be unavailable, or this host is not linux-64);"
  echo "falling back to the floating spec $(basename "$FALLBACK_ENV_FILE")."
  attempt_install "$FALLBACK_ENV_FILE" || { echo "Environment setup failed." >&2; exit 1; }
  INSTALLED_ENV_FILE="$FALLBACK_ENV_FILE"
else
  echo "Environment setup failed." >&2
  exit 1
fi

# Stage 2b (full profile)
# A normal env update trusts conda metadata, so it does not restore a package file that
# is missing or damaged. Verification below may request one exact-spec reinstall. The retry is
# bounded, preserves the environment, and uses the spec that actually installed (lock, fallback,
# or core); it never deletes the environment or silently changes pins.
repair_attempted=0
repair_installed_spec_once() {
  if [ "$repair_attempted" -ne 0 ]; then
    echo "Environment repair was already attempted once; refusing another automatic retry." >&2
    return 1
  fi
  repair_attempted=1
  echo "Verification found a damaged or unloadable component."
  echo "Repairing '$ENV_NAME' once from $(basename "$INSTALLED_ENV_FILE") without removing it."
  echo "Cached conda packages are reused, but package post-link steps may download data again."
  "$MICROMAMBA" install --yes --force-reinstall -n "$ENV_NAME" -f "$INSTALLED_ENV_FILE"
}

echo ""
echo "Configuring shell activation helper"
# Writing only to ~/.bashrc leaves the hook unreachable for anyone whose login shell is
# zsh. Write to every rc that applies: always bash (the app invokes `bash -lc` for its own
# probes), plus zsh when that is the login shell or a ~/.zshrc already exists. Each gets
# its own --shell argument.
_install_shell_hook() {  # $1 = rc file, $2 = micromamba shell name
  local rc="$1" shell_name="$2"
  if grep -q "micromamba shell hook" "$rc" 2>/dev/null; then
    echo "micromamba shell hook is already present in $rc"
    return 0
  fi
  {
    echo ""
    echo "# BulkSeq Studio micromamba setup"
    echo 'export MAMBA_ROOT_PREFIX="$HOME/micromamba"'
    echo "eval \"\$(\$HOME/.local/bin/micromamba shell hook --shell ${shell_name})\""
  } >> "$rc"
  echo "Added the micromamba shell hook to $rc"
}

_install_shell_hook "$HOME/.bashrc" bash
case "${SHELL:-}" in
  */zsh) _install_shell_hook "$HOME/.zshrc" zsh ;;
  *) [ -f "$HOME/.zshrc" ] && _install_shell_hook "$HOME/.zshrc" zsh || true ;;
esac

echo ""
echo "Stage 3/3: Verifying the $PROFILE environment"
echo "Verification:"
CORE_PROBE_TOOLS=(
  snakemake aria2c fastqc multiqc fastp STAR hisat2 hisat2-build salmon gffread
  featureCounts samtools trim_galore trimmomatic sortmerna fastq_screen bowtie2 perl
  read_distribution.py geneBody_coverage.py gtfToGenePred genePredToBed
)
FULL_ONLY_PROBE_TOOLS=(ribodetector_cpu Rscript)
PROBE_TOOLS=("${CORE_PROBE_TOOLS[@]}")
if [ "$PROFILE" = "full" ]; then
  PROBE_TOOLS+=("${FULL_ONLY_PROBE_TOOLS[@]}")
fi

probe_tool() {
  local tool="$1" path="" out="" rc=0 expected_marker=""
  local -a probe_args=()
  path="$("$MICROMAMBA" run -n "$ENV_NAME" bash -c 'command -v "$1"' _ "$tool" 2>/dev/null | tail -n 1 || true)"
  printf "  %-22s" "$tool"
  if [ -z "$path" ]; then
    echo "not found"
    verification_failed=1
    return
  fi

  # Every probe executes the installed program. command -v supplies the auditable path for the
  # setup log, but path presence alone is never accepted as readiness evidence.
  case "$tool" in
    featureCounts)       probe_args=(-v) ;;
    trimmomatic)         probe_args=(-version) ;;
    read_distribution.py|geneBody_coverage.py|ribodetector_cpu) probe_args=(--help) ;;
    perl)                probe_args=(-v) ;;
    Rscript)             probe_args=(--version) ;;
    gtfToGenePred|genePredToBed)
      probe_args=()
      expected_marker="$tool"
      ;;
    *)                   probe_args=(--version) ;;
  esac

  if out="$(run_limited 20 "$MICROMAMBA" run -n "$ENV_NAME" "$tool" "${probe_args[@]}" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  # UCSC conversion tools print an identifying usage banner and return 255 when executed without
  # files. Accept that documented execution signature; a missing binary or loader failure does not
  # contain the exact tool banner and therefore fails closed.
  if [ "$rc" -ne 0 ] && { [ -z "$expected_marker" ] || ! grep -Fq "$expected_marker" <<< "$out"; }; then
    echo "execution failed (exit $rc)"
    [ -n "$out" ] && printf "    %s\n" "$(printf '%s\n' "$out" | head -n 1)"
    verification_failed=1
    return
  fi
  echo "$path"
  if [ -n "$out" ]; then
    printf "    version: %s\n" "$(printf '%s\n' "$out" | head -n 1)"
  fi
  return 0
}

verify_environment() {
  verification_failed=0
  local tool python_path python_versions
  for tool in "${PROBE_TOOLS[@]}"; do
    probe_tool "$tool"
  done

  printf "  %-22s" "Python imports"
  python_path="$("$MICROMAMBA" run -n "$ENV_NAME" bash -c 'command -v python' 2>/dev/null | tail -n 1 || true)"
  if python_versions="$(run_limited 20 "$MICROMAMBA" run -n "$ENV_NAME" python -c \
      'import numpy, pandas, yaml; print("numpy=" + numpy.__version__ + "; pandas=" + pandas.__version__ + "; PyYAML=" + yaml.__version__)' 2>&1)"; then
    echo "$python_path"
    printf "    versions: %s\n" "$python_versions"
  else
    echo "numpy/pandas/PyYAML import failed"
    [ -n "$python_versions" ] && printf "    %s\n" "$(printf '%s\n' "$python_versions" | head -n 1)"
    verification_failed=1
  fi

  if [ "$PROFILE" = "full" ]; then
    printf "  %-22s" "R package stack"
    if r_stack_loads; then
      echo "loaded"
    else
      echo "load failed"
      verification_failed=1
    fi
  fi
  [ "$verification_failed" -eq 0 ]
}

if ! verify_environment; then
  echo "Initial $PROFILE environment verification failed; attempting one in-place repair." >&2
  if repair_installed_spec_once; then
    echo ""
    echo "Re-running verification after repair:"
    if ! verify_environment; then
      echo "ERROR: $PROFILE environment verification still fails after one exact-spec repair." >&2
      echo "The existing environment was retained for diagnosis; see the probes above." >&2
      exit 1
    fi
  else
    echo "ERROR: $PROFILE environment repair failed; the existing environment was retained." >&2
    exit 1
  fi
fi

# Record which profile this environment was installed with. app/core/readiness.py reads the
# marker so a correct core-only environment is not reported broken for the full-only tools
# (Rscript, ribodetector_cpu) it was never meant to contain. Written only after verification
# passed, so the marker never claims a profile that did not install.
ENV_PREFIX="$MAMBA_ROOT/envs/$ENV_NAME"
if [ -d "$ENV_PREFIX" ]; then
  printf '%s\n' "$PROFILE" > "$ENV_PREFIX/.bulkseq_profile"
  echo "Recorded environment profile '$PROFILE' in $ENV_PREFIX/.bulkseq_profile"

  # Record which spec file was actually installed. environment_lock_md5 in run_summary.json
  # hashes workflow/envs/bulkseq.lock.yaml regardless of what installed, so a fallback-solved
  # environment (float spec, no exact build pins) reports the same hash as a lock-installed
  # one. This marker names the file that was actually installed, so make_run_summary.py can
  # tell the two apart.
  spec_basename="$(basename "$INSTALLED_ENV_FILE")"
  if command -v sha256sum >/dev/null 2>&1; then
    spec_sha256="$(sha256sum "$INSTALLED_ENV_FILE" | cut -d' ' -f1)"
  else
    spec_sha256="$(shasum -a 256 "$INSTALLED_ENV_FILE" | cut -d' ' -f1)"
  fi
  spec_source="fallback"
  if [ "$PROFILE" = "core" ]; then
    spec_source="core"
  elif [ "$INSTALLED_ENV_FILE" = "$FIXED_LOCK_ENV_FILE" ]; then
    spec_source="lock"
  fi
  printf '%s\n%s\n%s\n' "$spec_basename" "$spec_sha256" "$spec_source" > "$ENV_PREFIX/.bulkseq_spec"
  echo "Recorded installed spec '$spec_basename' (source: $spec_source) in $ENV_PREFIX/.bulkseq_spec"
fi

echo ""
echo "Setup complete."
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "Open a new WSL shell and run:"
echo "  micromamba activate $ENV_NAME"
echo "  snakemake --version"
