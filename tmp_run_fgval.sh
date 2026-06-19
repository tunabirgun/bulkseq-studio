#!/usr/bin/env bash
P="$HOME/fgval"
rm -rf "$P"
cp -r /mnt/c/Users/tunabirgun/fgval_ws/fgval "$P"
cd "$P" || exit 2
export MAMBA_ROOT_PREFIX="$HOME/micromamba"
"$HOME/.local/bin/micromamba" run -n bulkseq snakemake --cores 12 --resources mem_mb=32000 \
  -p --rerun-incomplete --keep-going --configfile config/config.yaml > run.log 2>&1
echo "SMK_EXIT=$?" >> run.log
tail -n 3 run.log
