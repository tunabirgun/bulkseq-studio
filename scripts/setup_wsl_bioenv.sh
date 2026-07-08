#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-bulkseq}"
PROFILE="${2:-core}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$PROFILE" = "full" ]; then
  ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_full.yaml"
else
  ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_core.yaml"
fi
LOG_DIR="$REPO_DIR/scripts/logs"
LOG_FILE="$LOG_DIR/wsl_bioenv_install.log"
MAMBA_ROOT="$HOME/micromamba"
MICROMAMBA="$HOME/.local/bin/micromamba"
MM_URL="https://micro.mamba.pm/api/micromamba/linux-64/latest"

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
      echo "Another BulkSeq setup is already running; waiting for it to finish…"
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

# Extract bin/micromamba from the .tar.bz2 at $1 into $2, using only the python3
# standard library (urllib + tarfile/bz2). No curl, bzip2, apt or sudo needed.
bootstrap_with_python3() {
  python3 - "$1" "$2" <<'PY'
import io, os, stat, sys, tarfile, urllib.request
url, dest = sys.argv[1], sys.argv[2]
data = urllib.request.urlopen(url, timeout=180).read()
with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
    extracted = tf.extractfile(tf.getmember("bin/micromamba"))
    if extracted is None:
        raise SystemExit("bin/micromamba not found in archive")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as out:
        out.write(extracted.read())
os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print("micromamba written to", dest)
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

  # Preferred: python3 standard library. No system packages, no sudo.
  if command -v python3 >/dev/null 2>&1; then
    echo "Downloading micromamba with python3 (no system packages needed)..."
    if bootstrap_with_python3 "$MM_URL" "$MICROMAMBA"; then
      installed=1
    else
      echo "python3 bootstrap failed; trying curl/wget."
    fi
  fi

  # Fallback: curl or wget piped through tar (tar -j needs bzip2).
  if [ "$installed" -eq 0 ] && command -v bzip2 >/dev/null 2>&1; then
    if command -v curl >/dev/null 2>&1; then
      echo "Downloading micromamba with curl..."
      if curl -L "$MM_URL" | tar -xj -C "$HOME/.local/bin" --strip-components=1 bin/micromamba; then
        chmod +x "$MICROMAMBA"
        installed=1
      fi
    elif command -v wget >/dev/null 2>&1; then
      echo "Downloading micromamba with wget..."
      if wget -qO- "$MM_URL" | tar -xj -C "$HOME/.local/bin" --strip-components=1 bin/micromamba; then
        chmod +x "$MICROMAMBA"
        installed=1
      fi
    fi
  fi

  # Last resort: install python3 via apt, but only if sudo needs no password.
  # This installer has no terminal to type a sudo password into, so an
  # interactive sudo would hang; we skip it and print instructions instead.
  if [ "$installed" -eq 0 ] && sudo -n true 2>/dev/null; then
    echo "Installing python3 via passwordless sudo apt..."
    sudo apt-get update
    sudo apt-get install -y python3 ca-certificates
    if command -v python3 >/dev/null 2>&1 && bootstrap_with_python3 "$MM_URL" "$MICROMAMBA"; then
      installed=1
    fi
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

# Clean rebuild: an in-place `env update` across versions can leave the R/Bioconductor
# stack ABI-inconsistent (R base moves but packages built against the old R do not),
# which makes the first R step crash on library load. When BULKSEQ_REBUILD=1, remove the
# existing environment and create it fresh so the whole stack is internally consistent.
REBUILD="${BULKSEQ_REBUILD:-0}"

# Create the env if absent, otherwise update it in place from the profile yaml. On a
# rebuild, remove any existing env first so the create path always runs.
run_env_step() {
  if [ "$REBUILD" = "1" ] && "$MICROMAMBA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "Rebuild requested: removing existing environment '$ENV_NAME' for a clean install…"
    "$MICROMAMBA" env remove --yes -n "$ENV_NAME" || rm -rf "$MAMBA_ROOT/envs/$ENV_NAME"
    REBUILD=0  # only remove once, even if the create is retried after a cache clean
  fi
  if "$MICROMAMBA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "Updating existing micromamba environment: $ENV_NAME"
    "$MICROMAMBA" env update --yes -n "$ENV_NAME" -f "$ENV_FILE"
  else
    echo "Creating micromamba environment: $ENV_NAME"
    "$MICROMAMBA" create --yes -n "$ENV_NAME" -f "$ENV_FILE"
  fi
}

# Drop only the index/shard cache (not downloaded package tarballs) to recover
# from a truncated JSON shard left by an interrupted or concurrent fetch — the
# state that makes every run die with "parse error ... empty input".
clean_index_cache() {
  echo "Cleaning the micromamba index cache to recover from a corrupted shard…"
  "$MICROMAMBA" clean --index-cache --yes 2>/dev/null || true
  rm -rf "$MAMBA_ROOT/pkgs/cache" 2>/dev/null || true
}

# First failure is expected to be the corrupt cache; clean it and retry once.
# The retry runs under set -e, so a second failure aborts with a non-zero exit.
if ! run_env_step; then
  echo "Environment step failed; cleaning the index cache and retrying once."
  clean_index_cache
  run_env_step
fi

echo ""
echo "Stage 3/3: Configuring shell activation helper"
SHELL_RC="$HOME/.bashrc"
if ! grep -q "micromamba shell hook" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# BulkSeq Studio micromamba setup"
    echo 'export MAMBA_ROOT_PREFIX="$HOME/micromamba"'
    echo 'eval "$($HOME/.local/bin/micromamba shell hook --shell bash)"'
  } >> "$SHELL_RC"
else
  echo "micromamba shell hook is already present in $SHELL_RC"
fi

echo ""
echo "Setup complete."
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "Verification:"
for tool in snakemake aria2c fastqc multiqc fastp STAR hisat2 salmon gffread featureCounts samtools \
            trim_galore trimmomatic sortmerna ribodetector_cpu fastq_screen read_distribution.py genePredToBed; do
  printf "  %-14s" "$tool"
  if timeout 10 "$MICROMAMBA" run -n "$ENV_NAME" bash -lc "command -v $tool" >/tmp/bulkseq_tool_check.txt 2>/tmp/bulkseq_tool_check.err; then
    cat /tmp/bulkseq_tool_check.txt
  else
    echo "not found or timed out"
  fi
done
if [ "$PROFILE" = "full" ]; then
  printf "  %-14s" "Rscript"
  if timeout 10 "$MICROMAMBA" run -n "$ENV_NAME" bash -lc "command -v Rscript" >/tmp/bulkseq_tool_check.txt 2>/tmp/bulkseq_tool_check.err; then
    cat /tmp/bulkseq_tool_check.txt
  else
    echo "not found or timed out"
  fi
fi
echo "Open a new WSL shell and run:"
echo "  micromamba activate $ENV_NAME"
echo "  snakemake --version"
