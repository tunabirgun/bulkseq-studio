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

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "BulkSeq Studio WSL bioinformatics setup"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Repository: $REPO_DIR"
echo "Profile: $PROFILE"
echo "Environment file: $ENV_FILE"
echo "Log file: $LOG_FILE"

mkdir -p "$HOME/.local/bin"

echo ""
echo "Stage 1/4: Checking WSL prerequisite packages"
missing_prereqs=()
for cmd in curl bzip2; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing_prereqs+=("$cmd")
  fi
done

if [ "${#missing_prereqs[@]}" -gt 0 ]; then
  echo "Installing missing prerequisites via apt: ${missing_prereqs[*]}"
  echo "sudo may ask for your WSL password."
  sudo apt-get update
  sudo apt-get install -y curl bzip2 ca-certificates
else
  echo "WSL prerequisites are present."
fi

echo ""
echo "Stage 2/4: Checking micromamba"
if [ ! -x "$MICROMAMBA" ]; then
  echo "Installing micromamba..."
  curl -L https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C "$HOME/.local/bin" --strip-components=1 bin/micromamba
  chmod +x "$MICROMAMBA"
else
  echo "micromamba already installed at $MICROMAMBA"
fi

export MAMBA_ROOT_PREFIX="$MAMBA_ROOT"

echo ""
echo "Stage 3/4: Creating/updating the BulkSeq micromamba environment"
if "$MICROMAMBA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Updating existing micromamba environment: $ENV_NAME"
  "$MICROMAMBA" env update --yes -n "$ENV_NAME" -f "$ENV_FILE"
else
  echo "Creating micromamba environment: $ENV_NAME"
  "$MICROMAMBA" create --yes -n "$ENV_NAME" -f "$ENV_FILE"
fi

echo ""
echo "Stage 4/4: Configuring shell activation helper"
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
for tool in snakemake fastqc multiqc fastp STAR featureCounts samtools; do
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
