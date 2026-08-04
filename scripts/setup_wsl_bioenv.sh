#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-bulkseq}"
PROFILE="${2:-core}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Host platform. The same script serves WSL2/Linux and native macOS, so the
# micromamba build, the environment spec and the shell rc all key off this.
# ---------------------------------------------------------------------------
HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"
case "${HOST_OS}/${HOST_ARCH}" in
  Linux/x86_64)          MM_PLATFORM="linux-64" ;;
  Linux/aarch64|Linux/arm64) MM_PLATFORM="linux-aarch64" ;;
  Darwin/x86_64)         MM_PLATFORM="osx-64" ;;
  Darwin/arm64)          MM_PLATFORM="osx-arm64" ;;
  *)
    echo "Unsupported platform: ${HOST_OS} ${HOST_ARCH}." >&2
    echo "BulkSeq Studio supports Linux (x86_64/aarch64) and macOS (Intel/Apple Silicon)." >&2
    exit 1 ;;
esac
if [ "$PROFILE" = "full" ]; then
  # Install the full R/Bioconductor + CLI stack from the pinned LOCK, not the floating
  # bulkseq_full.yaml. A fresh solve of the float spec can silently drop a transitive
  # dependency (e.g. GO.db) and leave clusterProfiler unable to load; the lock pins every
  # package and build so the env reproduces exactly. bulkseq_full.yaml stays as a fallback
  # for what the lock cannot satisfy: a build garbage-collected from the channels, or a host
  # that is not linux-64 (the lock is a linux-64 snapshot).
  ENV_FILE="$REPO_DIR/workflow/envs/bulkseq.lock.yaml"
  FALLBACK_ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_full.yaml"
  if [ "$MM_PLATFORM" = "osx-arm64" ]; then
    # Apple Silicon needs TWO prefixes, not one. The alignment tools pin
    # libdeflate <1.23 while r-base 4.5.2 pins >=1.24; on linux-64/osx-64 bioconda's
    # repodata patch widens the tool bound and the conflict disappears, but that
    # patch coverage was never extended to osx-arm64. Verified on macOS 27 / M5 Pro:
    # a single-prefix solve fails on exactly that constraint, and these two specs
    # each resolve at the protocol-pinned versions.
    ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_macos_arm64_tools.yaml"
    R_ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_macos_arm64_r.yaml"
    R_ENV_NAME="${ENV_NAME}-r"
    FALLBACK_ENV_FILE=""
    SPLIT_R_PREFIX=1
    echo "Note: Apple Silicon — installing a split environment (${ENV_NAME} + ${R_ENV_NAME})."
  elif [ "$MM_PLATFORM" != "linux-64" ]; then
    # The lock is a linux-64 snapshot pinned to exact builds, so it cannot solve on
    # any other subdir. Go straight to the float spec rather than burning a long
    # solve on a guaranteed failure and reporting it as an error.
    echo "Note: ${MM_PLATFORM} host — installing from the float spec, not the linux-64 lock."
    ENV_FILE="$FALLBACK_ENV_FILE"
    FALLBACK_ENV_FILE=""
  fi
else
  ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_core.yaml"
  FALLBACK_ENV_FILE=""
  if [ "$MM_PLATFORM" = "osx-arm64" ]; then
    # Same split rationale as the full profile; the core profile has no R half, so
    # only the tools spec changes.
    ENV_FILE="$REPO_DIR/workflow/envs/bulkseq_macos_arm64_tools.yaml"
  fi
fi
: "${R_ENV_FILE:=}"
: "${R_ENV_NAME:=}"
: "${SPLIT_R_PREFIX:=0}"
LOG_DIR="$REPO_DIR/scripts/logs"
LOG_FILE="$LOG_DIR/wsl_bioenv_install.log"
MAMBA_ROOT="$HOME/micromamba"
MICROMAMBA="$HOME/.local/bin/micromamba"
MM_URL="https://micro.mamba.pm/api/micromamba/${MM_PLATFORM}/latest"

# `timeout` is GNU coreutils: present on Linux, absent from a stock macOS. The tool
# verification below wraps each probe in an `if`, so `set -e` never fires and a missing
# `timeout` would make EVERY tool report "not found or timed out" on a correctly
# installed Mac. Fall back to a portable background-and-kill wait.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then   # coreutils from Homebrew
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

# The full profile installs from the pinned lock so the R/Bioconductor stack reproduces
# exactly (a re-solve of the float spec can drop a transitive dep like GO.db). An in-place
# `env update` can still leave a package installed-but-unloadable (a build GC, an r-base ABI
# drift); Stage 2b below verifies the stack actually loads and does one clean rebuild if not.
# BULKSEQ_REBUILD=1 forces the clean rebuild up front.
REBUILD="${BULKSEQ_REBUILD:-0}"

env_exists() { "$MICROMAMBA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; }

remove_env() {
  echo "Removing existing environment '$ENV_NAME' for a clean install…"
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
# from a truncated JSON shard left by an interrupted or concurrent fetch — the
# state that makes every run die with "parse error ... empty input".
clean_index_cache() {
  echo "Cleaning the micromamba index cache to recover from a corrupted shard…"
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
# NOT the exit code — `micromamba run` can mask a non-zero status. A dropped GO.db or an
# r-base ABI drift leaves these installed-but-unloadable, which is what kills enrichment
# mid-run. Core/empty profile -> trivially "loads".
# GSVA and affy are deliberately absent from the osx-arm64 R spec (no arm64 build of
# bioconductor-gsva; affy's affyio dependency has none at r45 and pulling it would drag
# the whole Bioconductor graph back a release). Probing for them there would report a
# correct environment as broken, so the expected package list is built per platform
# rather than hardcoded.
R_OPTIONAL_PKGS='"GSVA","affy",'
[ "$MM_PLATFORM" = "osx-arm64" ] && R_OPTIONAL_PKGS=''
R_STACK_PROBE='q<-c("DESeq2","edgeR","limma",'"$R_OPTIONAL_PKGS"'"clusterProfiler","GO.db","DOSE","enrichplot","fgsea","STRINGdb","GEOquery","metaRNASeq","metafor","HTSFilter","tximport","gprofiler2","ggplot2","scales","svglite","RColorBrewer","msigdbr"); ok<-function(p) isTRUE(tryCatch(suppressWarnings(suppressMessages(requireNamespace(p,quietly=TRUE))),error=function(e)FALSE)); bad<-q[!vapply(q,ok,logical(1))]; cat(if(length(bad)) paste0("R_STACK_BAD:",paste(bad,collapse=",")) else "R_STACK_OK")'
r_stack_loads() {
  [ "$PROFILE" = "full" ] || return 0
  local out probe_env="$ENV_NAME"
  # On the split (Apple Silicon) layout the R stack lives in its own prefix.
  [ "$SPLIT_R_PREFIX" = "1" ] && probe_env="$R_ENV_NAME"
  out="$("$MICROMAMBA" run -n "$probe_env" Rscript --vanilla -e "$R_STACK_PROBE" 2>/dev/null || true)"
  echo "$out" | grep -q "R_STACK_OK"
}

# Stage 2a: install/repair from the lock (or core.yaml). Fall back to the floating spec only
# for what the lock cannot satisfy (a GC'd build or a non-linux-64 host).
if [ "$REBUILD" = "1" ] && env_exists; then
  remove_env
fi
if attempt_install "$ENV_FILE"; then
  :
elif [ -n "$FALLBACK_ENV_FILE" ] && [ "$FALLBACK_ENV_FILE" != "$ENV_FILE" ]; then
  echo "Locked install failed (a pinned build may be unavailable, or this host is not linux-64);"
  echo "falling back to the floating spec $(basename "$FALLBACK_ENV_FILE")."
  attempt_install "$FALLBACK_ENV_FILE" || { echo "Environment setup failed." >&2; exit 1; }
else
  echo "Environment setup failed." >&2
  exit 1
fi

# Stage 2a-pre (Apple Silicon, both profiles): SortMeRNA from the upstream release.
#
# There is no osx-arm64 conda build of sortmerna at ANY version -- 4.3.7 bundles an SSW
# aligner that includes <emmintrin.h> (x86 SSE2), so it cannot build for arm64. Upstream
# ships a native arm64 Mach-O from 4.4.0 onward, verified a drop-in for the exact command
# lines in rules/rrna.smk by scripts/test_sortmerna_macos.sh (28/28 on Linux, 15/15 on an
# M5 Pro). It is installed here into the shim directory rather than the env prefix so a
# later `micromamba install` into that prefix cannot clobber it.
#
# The checksum is pinned: this binary is outside the conda lock, so the SHA256 is what
# keeps the environment reproducible. Verify it before trusting a new version.
SORTMERNA_MACOS_VERSION="4.4.0"
SORTMERNA_MACOS_SHA256="82fe9b9954c86b041e5383b7ae0b30dc248ab121683a58fd0b116cd030e597ff"

_install_sortmerna_macos() {
  local bindir="$1"
  local ver="$SORTMERNA_MACOS_VERSION"
  local asset="sortmerna-${ver}-Darwin.tar.gz"
  local url="https://github.com/sortmerna/sortmerna/releases/download/v${ver}/${asset}"
  local workdir="$MAMBA_ROOT/.sortmerna-src"

  mkdir -p "$bindir" "$workdir"
  if [ -x "$bindir/sortmerna" ] && "$bindir/sortmerna" --version >/dev/null 2>&1; then
    echo "sortmerna already installed at $bindir/sortmerna"
    return 0
  fi

  echo "Installing SortMeRNA ${ver} (native arm64) from the upstream release"
  if ! curl -fsSL -o "$workdir/$asset" "$url"; then
    echo "WARNING: could not download $url" >&2
    echo "         rRNA filtering with SortMeRNA will be unavailable; RiboDetector still works." >&2
    return 0
  fi

  local actual
  actual="$(shasum -a 256 "$workdir/$asset" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$actual" ] || actual="$(sha256sum "$workdir/$asset" 2>/dev/null | cut -d' ' -f1)"
  if [ "$actual" != "$SORTMERNA_MACOS_SHA256" ]; then
    echo "ERROR: SortMeRNA checksum mismatch — refusing to install." >&2
    echo "  expected $SORTMERNA_MACOS_SHA256" >&2
    echo "  actual   ${actual:-<none>}" >&2
    rm -f "$workdir/$asset"
    return 1
  fi

  tar xzf "$workdir/$asset" -C "$workdir"
  local extracted="$workdir/sortmerna-${ver}-Darwin/bin/sortmerna"
  if [ ! -f "$extracted" ]; then
    echo "WARNING: unexpected archive layout; sortmerna not installed." >&2
    return 0
  fi
  install -m 0755 "$extracted" "$bindir/sortmerna"
  # A downloaded binary carries the quarantine attribute; without clearing it Gatekeeper
  # blocks execution and every rRNA rule fails with an opaque kill signal.
  xattr -d com.apple.quarantine "$bindir/sortmerna" 2>/dev/null || true
  rm -rf "$workdir"
  echo "sortmerna ${ver} -> $bindir/sortmerna"
}

if [ "$MM_PLATFORM" = "osx-arm64" ]; then
  echo ""
  echo "Stage 2a-pre: SortMeRNA (no arm64 conda build exists)"
  _install_sortmerna_macos "$MAMBA_ROOT/shims" || exit 1
fi

# Stage 2a-bis (Apple Silicon, full profile): the R/Bioconductor prefix.
#
# Snakemake invokes R rules as `Rscript --vanilla <file>`, resolved from PATH, so the
# split layout is bridged with a shim rather than by editing any .smk file. The shim
# MUST live in its own directory placed ahead of the tools prefix on PATH, never inside
# the tools prefix's bin/: rseqc depends on bare r-base, so the tools prefix ships its
# own Rscript at a different R version with no Bioconductor. If that one wins, every
# library(DESeq2) fails -- loudly, not silently, but it fails.
if [ "$SPLIT_R_PREFIX" = "1" ] && [ "$PROFILE" = "full" ]; then
  echo ""
  echo "Stage 2a-bis: R / Bioconductor prefix ($R_ENV_NAME)"
  if ! "$MICROMAMBA" create --yes -n "$R_ENV_NAME" --file "$R_ENV_FILE"; then
    echo "R environment setup failed." >&2
    exit 1
  fi
  SHIM_DIR="$MAMBA_ROOT/shims"
  mkdir -p "$SHIM_DIR"
  R_PREFIX="$MAMBA_ROOT/envs/$R_ENV_NAME"
  for r_tool in Rscript R; do
    cat > "$SHIM_DIR/$r_tool" <<SHIM
#!/usr/bin/env bash
# BulkSeq Studio shim: route R to the split Bioconductor prefix (Apple Silicon).
# Generated by scripts/setup_wsl_bioenv.sh -- edits here are overwritten on re-run.
exec "$R_PREFIX/bin/$r_tool" "\$@"
SHIM
    chmod +x "$SHIM_DIR/$r_tool"
  done
  echo "Rscript shim -> $R_PREFIX/bin/Rscript  (in $SHIM_DIR)"
  if [ ! -x "$R_PREFIX/bin/Rscript" ]; then
    echo "WARNING: $R_PREFIX/bin/Rscript is missing; the R prefix did not install correctly." >&2
  fi
fi

# Stage 2b (full profile): confirm the R stack loads. An in-place update can leave a package
# installed-but-unloadable that `env update` will not repair; escalate to ONE clean rebuild
# from the lock, which reproduces a self-consistent stack. No-op for a healthy or core env.
if ! r_stack_loads; then
  echo "The R/Bioconductor stack did not load after the update; doing a clean rebuild from the lock…"
  remove_env
  attempt_install "$ENV_FILE" || { echo "Clean rebuild failed." >&2; exit 1; }
  if ! r_stack_loads; then
    echo "ERROR: the R/Bioconductor stack still does not load after a clean rebuild." >&2
    echo "See the messages above for the failing packages; the environment may need manual attention." >&2
    exit 1
  fi
  echo "Clean rebuild succeeded; the R/Bioconductor stack now loads."
fi

echo ""
echo "Stage 3/3: Configuring shell activation helper"
# macOS has defaulted to zsh since Catalina, so writing only to ~/.bashrc leaves the
# hook unreachable from the user's actual login shell. Write to every rc that applies:
# always bash (the app invokes `bash -lc` for its own probes), plus zsh when that is
# the login shell or a ~/.zshrc already exists. Each gets its own --shell argument.
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
echo "Setup complete."
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "Verification:"
for tool in snakemake aria2c fastqc multiqc fastp STAR hisat2 salmon gffread featureCounts samtools \
            trim_galore trimmomatic sortmerna ribodetector_cpu fastq_screen read_distribution.py genePredToBed; do
  printf "  %-14s" "$tool"
  if run_limited 10 "$MICROMAMBA" run -n "$ENV_NAME" bash -lc "command -v $tool" >/tmp/bulkseq_tool_check.txt 2>/tmp/bulkseq_tool_check.err; then
    cat /tmp/bulkseq_tool_check.txt
  else
    echo "not found or timed out"
  fi
done
if [ "$PROFILE" = "full" ]; then
  printf "  %-14s" "Rscript"
  if run_limited 10 "$MICROMAMBA" run -n "$ENV_NAME" bash -lc "command -v Rscript" >/tmp/bulkseq_tool_check.txt 2>/tmp/bulkseq_tool_check.err; then
    cat /tmp/bulkseq_tool_check.txt
  else
    echo "not found or timed out"
  fi
fi
echo "Open a new WSL shell and run:"
echo "  micromamba activate $ENV_NAME"
echo "  snakemake --version"
