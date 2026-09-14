# BulkSeq Studio

[![Release](https://img.shields.io/github/v/release/tunabirgun/bulkseq-studio?label=release&color=0b7285)](https://github.com/tunabirgun/bulkseq-studio/releases) [![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/) [![Snakemake](https://img.shields.io/badge/snakemake-9.23.1-039475)](https://snakemake.readthedocs.io/) [![License](https://img.shields.io/badge/license-MIT-6c757d)](LICENSE) [![Tests](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/tests.yml) [![Environment](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/environment.yml/badge.svg)](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/environment.yml)

BulkSeq Studio is a cross-platform desktop application for reproducible bulk RNA-seq and microarray analysis. Its PySide6 interface drives a transparent Snakemake workflow from raw reads or processed inputs through differential expression, enrichment, protein-interaction networks, figures, reports, and route-aware provenance.

> **Release status — 14 September 2026.** Version 0.31.0 is the current public release. Use only the checksummed packages published on GitHub Releases. The separately versioned B1–B20 validation archive remains deposited as version 0.26.6 on Zenodo.

[Read the documentation](https://tunabirgun.github.io/bulkseq-studio/) · [Download public v0.31.0](https://github.com/tunabirgun/bulkseq-studio/releases/latest) · [Report an issue](https://github.com/tunabirgun/bulkseq-studio/issues) · [Changelog](CHANGELOG.md)

![BulkSeq Studio in light mode: the four stage groups down the left with Project and data selected, and the Project page open](docs/assets/images/four-stage-navigator.png)

## What it covers

| Area | Supported routes |
| --- | --- |
| Inputs | Local single- or paired-end FASTQ; SRA/ENA accessions; RNA-seq GEO series; raw count matrices; processed microarray matrices; imported differential-expression tables |
| Read processing | FastQC/MultiQC; fastp, Trim Galore, or Trimmomatic; optional SortMeRNA or RiboDetector; optional FastQ Screen and RSeQC |
| Quantification | STAR, HISAT2, or Salmon; featureCounts, STAR gene counts, or Salmon/tximport |
| Differential expression | DESeq2 by default; optional limma-voom and edgeR quasi-likelihood; limma for microarrays; optional multi-study meta-analysis |
| Interpretation | Directional GO/KEGG and custom-gene-set enrichment, GSVA, STRING networks, publication figures, sortable reports, and Cytoscape exports |
| Reproducibility | Pinned workflow environment, content-fingerprinted pre-run validation, default-versus-used parameter records, active-route tool and reference provenance, R session details, and a `bulkseq run` command line that executes the same command the interface would |

The interface groups twelve pages into four stages: **Project and data**, **Analysis setup**, **Validate and run**, and **Explore results**.

## Documentation

The handbook at **[tunabirgun.github.io/bulkseq-studio](https://tunabirgun.github.io/bulkseq-studio/)** is the complete reference. It covers every route, setting, output and check in detail, and each page states the release it documents.

- [Install and first run](https://tunabirgun.github.io/bulkseq-studio/guide.html) — set up the application and the analysis environment
- [Interactive walkthrough](https://tunabirgun.github.io/bulkseq-studio/walkthrough.html) — the decisions in order, for each of the five starting points
- [Input routes](https://tunabirgun.github.io/bulkseq-studio/inputs.html) and [experimental design](https://tunabirgun.github.io/bulkseq-studio/design.html) — choose the right entry point and contrast
- [Pre-run checks](https://tunabirgun.github.io/bulkseq-studio/checks.html) — what each check tests and how to act on it
- [Outputs and provenance](https://tunabirgun.github.io/bulkseq-studio/outputs.html) — what a finished run writes and what records it
- [Command line and HPC profiles](https://tunabirgun.github.io/bulkseq-studio/cli.html) — scripted and scheduled runs
- [Troubleshooting, version notices and citation](https://tunabirgun.github.io/bulkseq-studio/faq.html) — including which release changed which output

## Scientific safeguards

A successful run means the computation completed. The study design, quality checks and interpretation still require review. The application is built so that the record of what it did survives the run.

- **Validation is revalidated.** Pre-run checks store content fingerprints for the configuration, sample sheet, local inputs, reference locks and index files; starting or resuming revalidates them, so a replaced or edited input cannot inherit an earlier pass.
- **Strandedness is per sample.** Each sample's library type is inferred from its own data and applied with its own featureCounts orientation, and a project mixing library types is named rather than forced onto one code.
- **Unmodelled structure is screened.** For every sample-sheet column outside the design formula, the workflow tests that column against the run's principal components and reports the result. The screen is advisory and engine-relative: two engines can reach different verdicts on the same samples.
- **Imported results are not relabelled.** An imported differential-expression table is validated in full, bound to its hash and schema, and its report never claims a model the application did not fit.
- **Provenance names what ran.** The run summary records the app and workflow version that actually executed, the workflow digest, the installed environment specification and the active route's tools and references.

Results can differ between releases when a scientific output changes. Every such change is marked in [CHANGELOG.md](CHANGELOG.md) with a re-run notice, and the site's [version notices](https://tunabirgun.github.io/bulkseq-studio/faq.html#version-notices) list them by release.

## Benchmark archive and citation

The checksummed version 0.26.6 benchmark archive is deposited at DOI [10.5281/zenodo.21833538](https://doi.org/10.5281/zenodo.21833538). The concept DOI [10.5281/zenodo.20955660](https://doi.org/10.5281/zenodo.20955660) resolves to the latest deposited version. Cite the exact software version that produced your analysis, not the archive version, when reporting an analysis.

```text
Birgün, Tuna (2026). BulkSeq Studio: validation benchmark archive. Version 0.26.6. Zenodo.
https://doi.org/10.5281/zenodo.21833538
```

## License

BulkSeq Studio is released under the [MIT License](LICENSE).
