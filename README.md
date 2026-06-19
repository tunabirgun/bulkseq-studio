# BulkSeq Studio

BulkSeq Studio is a Windows-native PySide6 project manager for reproducible, reference-based bulk RNA-seq analysis. It generates plain Snakemake projects that can be run from the GUI or from a terminal.

The scaffold follows the attached protocol, "Bulk Transcriptomics: From Raw SRA Reads to Differential Expression, Functional Enrichment, and Figures": FASTQ/SRA input, FastQC/MultiQC, fastp, optional rRNA removal, STAR alignment, featureCounts, DESeq2, enrichment, figures, sanity checks, provenance, and timing reports.

## Current State

Implemented:

- PySide6 GUI shell with project, input, metadata, reference, workflow, resource, runtime, sanity, run monitor, and report tabs.
- Project folder creation with manifest/config/sample files (and a bundled `default_config.yaml` for the provenance diff).
- FASTQ pairing detection and editable metadata table.
- Metadata/config/reference/resource validation helpers; design-formula variable checks.
- Reference catalog and custom reference manifest/checksum scaffolding.
- Snakemake command builder (runs via `micromamba run -n bulkseq` under WSL) and non-blocking runner.
- **A real, runnable paired-end pipeline**: ENA FASTQ download, FastQC/MultiQC, fastp, STAR index (genome-size-aware) and alignment, strandedness inference, featureCounts, DESeq2 (shrinkage, VST), and PCA/MA/volcano/sample-distance/top-DEG figures (PNG + SVG). Validated end-to-end on the pasilla subset.
- Report generators for run summary (with a default-vs-used parameter diff), timing summary, sanity checks, software versions, and R `sessionInfo`.
- Tests for metadata detection/validation, config generation/round-trip, reference validation, resources, runtime estimation, provenance diff, WSL path translation, and the WSL command builder.
- Curated pasilla paired-end subset benchmark project template under `examples/benchmarks/pasilla_paired_subset`.

The STAR -> featureCounts -> DESeq2 -> figures route is fully implemented. HISAT2, Salmon/tximport, SortMeRNA, BBMap, htseq-count, edgeR/limma-voom, and single-end handling remain scaffolding/TODOs.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Bioinformatics execution is expected under WSL2/Linux or an existing conda/mamba environment.

On startup, BulkSeq Studio runs a readiness check. It can install missing Python GUI/core packages from `requirements.txt`, open an Administrator PowerShell setup for WSL2, and install a WSL bioinformatics environment.

Use **Install/Repair Core WSL Env** first. It installs micromamba plus Snakemake, SRA download tooling, FastQC, MultiQC, fastp, STAR, featureCounts/subread, samtools, pandas, and PyYAML — the tools the default STAR → featureCounts → DESeq2 route uses. Use **Install Full R/DESeq2 Stack** afterward to add the R/Bioconductor packages (DESeq2, apeglm, clusterProfiler, and dependencies). Alternative aligners/quantifiers (HISAT2, Salmon) are defined in `workflow/envs/` and pulled per-rule only if selected; they are not part of the core install.

The app does not run as Administrator during normal use. Only the WSL enable/install action asks for elevation because Windows requires it.

If WSL setup fails or closes unexpectedly, inspect:

```text
scripts\logs\wsl_setup.log
```

If the WSL bioinformatics package setup is unclear, inspect:

```text
scripts\logs\wsl_bioenv_install.log
```

Manual WSL/micromamba setup sketch (what the installer does):

```bash
# core CLI tools (STAR route)
micromamba create -y -n bulkseq -f workflow/envs/bulkseq_core.yaml
# add the R/Bioconductor stack for DESeq2, enrichment, figures
micromamba env update -y -n bulkseq -f workflow/envs/bulkseq_full.yaml
```

The solved environment is recorded in `workflow/envs/bulkseq.lock.yaml`.

## Launch

End users install via the packaged installer (`installer_output\BulkSeqStudio-Setup-0.1.0.exe`,
built with `scripts\build_release.ps1` — see `BUILD.md`) and launch from the Start Menu.

For development, run from source:

```powershell
python -m app.main
```

If installed as a package, the GUI and benchmark helper are also available as:

```powershell
bulkseq-studio
bulkseq-benchmark list
```

## Test

```powershell
pytest
```

## Benchmark Dataset

The first bundled validation benchmark is `pasilla_paired_subset`, a four-sample paired-end subset of the published Drosophila pasilla RNAi RNA-seq experiment. The GUI Project tab includes a "Create Benchmark Project" button that writes `samples.tsv`, `sra_accessions.txt`, benchmark provenance, and a STAR + featureCounts + DESeq2 config.

The selected SRR runs are `SRR031714`, `SRR031716`, `SRR031724`, and `SRR031726`.

CLI usage:

```powershell
python -m app.benchmark_cli list
python -m app.benchmark_cli create --benchmark pasilla_paired_subset --workdir C:\BulkSeqBenchmarks --name pasilla_paired_subset --validate
```

The pipeline downloads the benchmark FASTQs (from the ENA URLs in `config/samples.tsv`) and the Ensembl reference automatically as part of the run. For best I/O performance, stage the project under the WSL home filesystem (`~/`) rather than `/mnt/c` before running.

## Snakemake From Terminal

After creating a project in the GUI:

```bash
cd /path/to/project
snakemake --cores 8 --resources mem_mb=24000 --use-conda --configfile config/config.yaml
```

## TODO

- Replace placeholder reference URLs in `app/data/reference_catalog.yaml` (the pasilla benchmark already uses real Ensembl URLs via `benchmark_datasets.yaml`).
- Add Snakemake rules for HISAT2, Salmon/tximport, SortMeRNA, BBMap repair, htseq-count, goseq, and UMAP; add single-end branching.
- Add organism-specific enrichment config (OrgDb/keytype/KEGG) in the GUI for non-Drosophila projects.
- Build out the Reference Manager (custom import/validate/build-index + lock enforcement), per-rule resource editing, metadata column/paste/export ops, and the sanity phase-gate approval UI.
- Add runtime history calibration (`runtime_profiles.json`).
- Add persisted app-level reference installation database.

See `PLAN.md` for the full remediation roadmap and phase status.
