# BulkSeq Studio

BulkSeq Studio is a Windows-native PySide6 project manager for reproducible, reference-based bulk RNA-seq analysis. It generates plain Snakemake projects that can be run from the GUI or from a terminal.

The scaffold follows the attached protocol, "Bulk Transcriptomics: From Raw SRA Reads to Differential Expression, Functional Enrichment, and Figures": FASTQ/SRA input, FastQC/MultiQC, fastp, optional rRNA removal, STAR alignment, featureCounts, DESeq2, enrichment, figures, sanity checks, provenance, and timing reports.

## Current Prototype

Implemented:

- PySide6 GUI shell with project, input, metadata, reference, workflow, resource, runtime, sanity, run monitor, and report tabs.
- Project folder creation with manifest/config/sample files.
- FASTQ pairing detection and editable metadata table.
- Metadata/config/reference/resource validation helpers.
- Reference catalog and custom reference manifest/checksum scaffolding.
- Snakemake command builder and non-blocking runner.
- Modular Snakemake workflow scaffold with benchmark directives.
- Report generators for run summary, timing summary, sanity checks, and software versions.
- Tests for metadata detection/validation, config generation, reference validation, resources, and runtime estimation.
- Curated pasilla paired-end subset benchmark project template under `examples/benchmarks/pasilla_paired_subset`.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Bioinformatics execution is expected under WSL2/Linux or an existing conda/mamba environment.

On startup, BulkSeq Studio runs a readiness check. It can install missing Python GUI/core packages from `requirements.txt`, open an Administrator PowerShell setup for WSL2, and install a WSL bioinformatics environment.

Use **Install/Repair Core WSL Env** first. It installs micromamba plus Snakemake, SRA tools, FastQC, MultiQC, fastp, STAR, HISAT2, Salmon, featureCounts/subread, samtools, SortMeRNA, BBMap, pandas, and PyYAML. Use **Install Full R/DESeq2 Stack** afterward to add R/Bioconductor packages.

The app does not run as Administrator during normal use. Only the WSL enable/install action asks for elevation because Windows requires it.

If WSL setup fails or closes unexpectedly, inspect:

```text
scripts\logs\wsl_setup.log
```

If the WSL bioinformatics package setup is unclear, inspect:

```text
scripts\logs\wsl_bioenv_install.log
```

Manual WSL/conda setup sketch:

```bash
conda env create -f workflow/envs/rnaseq.yaml
conda env create -f workflow/envs/r_deseq2.yaml
conda activate bulkseq-rnaseq
conda install -c bioconda -c conda-forge snakemake
```

## Launch

```powershell
python -m app.main
```

Or from PowerShell in the repository:

```powershell
.\launch_bulkseq_studio.ps1
```

For double-click launching on Windows, use:

```text
Launch BulkSeq Studio.bat
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

Inside the created project, benchmark FASTQs can be downloaded from the ENA URLs recorded in `config/samples.tsv`:

```bash
snakemake --snakefile workflow/Snakefile download_ena_fastqs --cores 2 --configfile config/config.yaml
```

## Snakemake From Terminal

After creating a project in the GUI:

```bash
cd /path/to/project
snakemake --cores 8 --resources mem_mb=24000 --use-conda --configfile config/config.yaml
```

## TODO

- Replace placeholder reference URLs in `app/data/reference_catalog.yaml`.
- Add deeper Snakemake rules for HISAT2, Salmon/tximport, SortMeRNA, BBMap repair, htseq-count, goseq, and UMAP.
- Expand R scripts from robust placeholders into full DESeq2/enrichment/figure pipelines.
- Add WSL path translation tests for edge cases.
- Add persisted app-level reference installation database.
