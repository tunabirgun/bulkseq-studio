# BulkSeq Studio

[![Release](https://img.shields.io/github/v/release/tunabirgun/bulkseq-studio?label=release&color=0b7285)](https://github.com/tunabirgun/bulkseq-studio/releases) [![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/) [![Snakemake](https://img.shields.io/badge/snakemake-9.23.1-039475)](https://snakemake.readthedocs.io/) [![License](https://img.shields.io/badge/license-MIT-6c757d)](LICENSE) [![Tests](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/tests.yml) [![Environment](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/environment.yml/badge.svg)](https://github.com/tunabirgun/bulkseq-studio/actions/workflows/environment.yml)

BulkSeq Studio is a desktop application for Windows and Linux for reproducible bulk RNA-seq and microarray analysis. Its PySide6 interface drives a transparent Snakemake workflow from raw reads or processed inputs through differential expression, enrichment, protein-interaction networks, figures, reports, and route-aware provenance.

> **Release status — 26 September 2026.** Version 0.33.0 is the current public release. It changes enrichment output: KEGG enrichment now runs for 17 catalogue organisms whose identity check it previously failed, and organisms without a curated annotation package receive annotation-transfer enrichment; count matrices and differential-expression tables are unchanged (see the [changelog](CHANGELOG.md)). All nine bundled datasets, two of them new non-model organisms, were run through the installed 0.33.0 interface and passed: every run completed without a FAIL, and the seven datasets carried over from 0.32.1 reproduced their 0.32.1 results byte for byte (see [Bundled datasets](#bundled-datasets)). Use only the checksummed packages published on GitHub Releases.

[Read public v0.33.0 handbook](https://tunabirgun.github.io/bulkseq-studio/) · [Download public v0.33.0](https://github.com/tunabirgun/bulkseq-studio/releases/latest) · [Report an issue](https://github.com/tunabirgun/bulkseq-studio/issues) · [Changelog](CHANGELOG.md)

A release is published only after the Tests and Build packages workflows succeed for its commit, and its packages are the ones that workflow built; verify every download against the release's `SHA256SUMS.txt`. The Windows installer and portable build are not code-signed, so Windows SmartScreen can warn about an unrecognised publisher before the first launch. The Linux AppImage needs glibc 2.38 or newer.

![BulkSeq Studio in light mode: the four stage groups down the left with Project and data selected, and the Project page open](docs/assets/images/four-stage-navigator.png)

## What it covers

| Area | Supported routes |
| --- | --- |
| Inputs | Local single- or paired-end FASTQ; SRA/ENA accessions; RNA-seq GEO series; raw count matrices; processed microarray matrices; imported differential-expression tables |
| Read processing | FastQC/MultiQC; fastp, Trim Galore, or Trimmomatic; optional SortMeRNA or RiboDetector; optional FastQ Screen and RSeQC |
| Quantification | STAR, HISAT2, or Salmon; featureCounts, STAR gene counts, or Salmon/tximport |
| Differential expression | DESeq2 by default; optional limma-voom and edgeR quasi-likelihood; limma for microarrays; optional multi-study meta-analysis |
| Interpretation | Directional GO/KEGG and custom-gene-set enrichment; annotation-transfer enrichment for organisms without a curated annotation package, from STRING's orthology-transferred GO, Reactome and InterPro sets or imported eggNOG-mapper and KofamScan output; GSVA, STRING networks, publication figures, sortable reports with native keyboard-operated column-header buttons and figure viewers, and Cytoscape exports |
| Reproducibility | Pinned workflow environment, content-fingerprinted pre-run validation, default-versus-used parameter records, active-route tool and reference provenance, R session details, and a `bulkseq run` command line that executes the same command the interface would |

The interface groups twelve pages into four stages: **Project and data**, **Analysis setup**, **Validate and run**, and **Explore results**.

The accession, differential-expression, output, figure-preview and PPI edge-filter controls carry specific Qt accessible names and descriptions. Composite workflow labels are programmatically associated with their fields, and the accession editor follows normal Tab navigation. Report tables retain column and row headers, and their native sort buttons work with mouse, Enter and Space. The current-view edge filter’s 0–100 slider maps to displayed confidence 0.00–1.00 and does not rebuild the network. These checks exercise Qt's accessibility interface and keyboard focus; they do not replace a native screen-reader session.

## Documentation

The handbook at **[tunabirgun.github.io/bulkseq-studio](https://tunabirgun.github.io/bulkseq-studio/)** is the complete reference. It is organised as a tutorial, how-to guides, reference and explanation, and each page states the release it documents.

Backend environment setup writes its persistent log in the current user's BulkSeq Studio application-data directory, separate from the installed or portable application files. Open **Show details / log** and choose **Load setup log** when a setup attempt needs review. After a tool, R or import verification failure, setup can make one in-place repair using the same installed specification, then rechecks it. Post-link steps can redownload required files; an unresolved verification failure remains failed. Run provenance records `lock` only when the exact linux-64 lock was installed; a floating-spec install records `fallback`, including a direct native Linux ARM install, with the actual specification filename and SHA-256. The published AppImage remains x86-64; this marker correction does not establish an ARM package or end-to-end ARM run.

- **Tutorial** — [your first analysis](https://tunabirgun.github.io/bulkseq-studio/tutorial.html): one complete run of a bundled four-sample study, with a recorded result to check your own against
- **How-to guides** — [install and first run](https://tunabirgun.github.io/bulkseq-studio/guide.html), the [walkthrough](https://tunabirgun.github.io/bulkseq-studio/walkthrough.html) for each of the five starting points, [input routes](https://tunabirgun.github.io/bulkseq-studio/inputs.html), [experimental design](https://tunabirgun.github.io/bulkseq-studio/design.html), and [command line and HPC profiles](https://tunabirgun.github.io/bulkseq-studio/cli.html)
- **Reference** — [pre-run checks](https://tunabirgun.github.io/bulkseq-studio/checks.html), [exit codes and statuses](https://tunabirgun.github.io/bulkseq-studio/codes.html), [outputs and provenance](https://tunabirgun.github.io/bulkseq-studio/outputs.html), and [troubleshooting, version notices and citation](https://tunabirgun.github.io/bulkseq-studio/faq.html)
- **Explanation** — [what the workflow checks for you](https://tunabirgun.github.io/bulkseq-studio/safeguards.html), [choosing a differential-expression engine](https://tunabirgun.github.io/bulkseq-studio/engines.html), [what the numbers mean](https://tunabirgun.github.io/bulkseq-studio/interpreting.html), and [reproducibility and provenance](https://tunabirgun.github.io/bulkseq-studio/provenance.html)

## Scientific safeguards

A successful run means the computation completed. The study design, quality checks and interpretation still require review. The application is built so that the record of what it did survives the run.

- **Validation is revalidated.** Pre-run checks store content fingerprints for the configuration, sample sheet, local inputs, reference locks and index files; starting or resuming revalidates them, so a replaced or edited input cannot inherit an earlier pass.
- **Strandedness is per sample.** Each sample's library type is inferred from its own data and applied with its own featureCounts orientation, and a project mixing library types is named rather than forced onto one code.
- **Unmodelled structure is screened.** For every sample-sheet column outside the design formula that varies between samples without being unique to each one, the workflow tests that column against the run's principal components and reports the result. Columns that name a sample rather than describe it — the identifier, file paths, ingest accessions and the free-text `sample_title`, `title` and `library_name` labels — are left out. The screen is advisory and engine-relative: two engines can reach different verdicts on the same samples.
- **Alternative-engine contrasts preserve condition identity.** edgeR, limma-voom and microarray limma form the configured numerator-minus-denominator comparison by factor position, so distinct condition levels containing spaces, hyphens or dots remain distinct even when they share an R-safe spelling. Internal model names also prevent a sample-sheet column named `grp` from replacing the configured contrast factor and keep group and covariate coefficients unique. The fitted model remains the documented additive group-means design; interaction terms remain unsupported by these engines.
- **Imported results are not relabelled.** An imported differential-expression table is validated in full, bound to its hash and schema, and its report never claims a model the application did not fit.
- **Ranked enrichment is independent of the ORA family.** GSEA retains an independently filtered row when its adjusted p-value is missing but its route-specific rank and raw p-value are finite. The ORA tested universe and foregrounds stay unchanged. An additional adjusted-p-missing row without a finite raw p-value, including a DESeq2 Cook's-distance outlier, remains excluded; legacy adjusted-p-finite rows retain their supported eligibility. Custom GSEA continues to rank on log2 fold change. On fallback KEGG routes, source aliases are reduced once after the final GeneID bridge, which can change an already-ranked alias group as well as admit newly eligible rows.
- **Count matrices are validated before conversion.** Missing, nonnumeric, nonfinite and negative cells are refused before a project copy or count output is written. Any non-integer value requires the explicit RSEM/tximport estimated-count declaration and is then rounded with the documented round-half-to-even rule. When at least half the sample columns have totals within one per cent of a million, an advisory message at import says the data may be normalized; it is not a check status, and it does not reclassify valid integer counts as TPM.
- **Ambiguous microarray probes are excluded.** GEO platform annotation is parsed across every listed candidate and every row for a probe. Only probes resolving to one distinct gene enter the established MaxMean collapse; ambiguous, unknown and missing mappings remain in the probe-map evidence and are counted by check 12. Local gene-level matrices retain their supplied identifiers directly.
- **Meta-enrichment foregrounds match the per-study tables.** Cross-study enrichment applies the configured meta-analysis FDR and absolute log2-fold-change thresholds to each study, including exact threshold boundaries; zero effect remains neutral when the threshold is zero. It then restricts the published up/down selections to the shared tested universe and the same ambiguity-aware accepted identifier mappings used by the main enrichment route. Unresolved one-to-many mappings are excluded consistently from the universe and every foreground, retained in `results/meta/meta_enrichment_mapping.tsv`, and reported as review-required by check 18 even if enrichment later skips.
- **Wilcoxon sensitivity remains diagnostic.** The matrix-based rank-sum check reports a `WARNING` status (check 14) for small groups because exact p-values are discrete and power is limited; it is a rank-concordance check rather than a thresholded DEG call. The warning wording does not alter the Wilcoxon statistics, adjusted p-values or differential-expression calls.
- **Provenance names what ran.** The run summary records the app and workflow version that actually executed, the execution-tree digest separately from the bundled-workflow identity, the installed environment specification and the active route's tools and references. Workflow synchronization stops if a recorded project copy has local edits.
- **Project destinations are protected.** New projects must be immediate children of the selected working directory. An occupied directory requires explicit overwrite; an existing file is refused.
- **Command-line checks use the selected sample sheet.** `bulkseq project info`, `samples show`, and `check` read `input.samples`, including custom relative and absolute paths. `check` requires local FASTQ files on the FASTQ route and allows pending reads only for SRA and processed-input routes.

Results can differ between releases when a scientific output changes. Every such change is marked in [CHANGELOG.md](CHANGELOG.md) with a re-run notice, and the site's [version notices](https://tunabirgun.github.io/bulkseq-studio/faq.html#version-notices) list them by release.

## Exit codes and check statuses

Every check reports one of four statuses, and the overall status is the most severe finding:

| Status | Meaning | Effect |
| --- | --- | --- |
| `PASS` | Nothing to report, or the check does not apply to the input route | None |
| `WARNING` | An advisory finding that may limit interpretation | Review it |
| `REVIEW_REQUIRED` | A finding that needs your judgement before you rely on the result | The interface asks you to acknowledge it |
| `FAIL` | A condition that prevents a correct analysis | The interface disables Start; `bulkseq check` exits 4 |

The interface adds a fifth status, `STALE`, when the saved input validation no longer matches the current inputs, and keeps Start disabled until you validate again. Three kinds of check can stop a workflow that is already running: a `FAIL` in check 00, project setup; anything but `PASS` in check 05, reference validation, on the FASTQ and SRA routes; and a failed microarray import, which records `FAIL` in checks 11 and 12.

The `bulkseq` command returns:

| Exit | Meaning |
| --- | --- |
| 0 | Success; for `check`, also when the worst finding is `WARNING` or `REVIEW_REQUIRED` |
| 1 | An unexpected error, printed with a traceback; please report it |
| 2 | A usage error |
| 3 | Not a project, an unreadable or invalid configuration, or a workflow copy that could not be refreshed |
| 4 | `check` found a `FAIL`, or the sample sheet is missing or unreadable |
| 5 | The workflow run failed |
| 130 | Interrupted with Ctrl-C |

`bulkseq run` does not evaluate the checks before it starts, so a script that must not run past a `FAIL` should call `bulkseq check` first and stop on exit 4. Environment setup codes, run-monitor states and public-data lookup messages are listed on [Exit codes and statuses](https://tunabirgun.github.io/bulkseq-studio/codes.html).

## Bundled datasets

**Create Benchmark Project** scaffolds one of nine bundled public datasets with its reference, sample sheet and comparison already set. All nine were run under 0.33.0 from the installed Windows package, through the interface alone, and all nine passed: every run completed without a FAIL. For the seven datasets carried over from 0.32.1, the results are byte-identical to their 0.32.1 runs: the count matrix and differential-expression table of each read-based dataset, and the limma table of each microarray dataset. The two added datasets are recorded for the first time. Significant genes are counted at an adjusted p-value below 0.05 with no fold-change filter.

| Dataset | Route | Genes tested | Significant | 0.33.0 result |
|---|---|---|---|---|
| Pasilla paired-end subset (*D. melanogaster*) | Reads, STAR, featureCounts, DESeq2 | 7,532 | 467 | Byte-identical to 0.32.1 and 0.31.0 |
| Yeast rpd3Δ Ume6Δ2-508 subset (*S. cerevisiae*) | Reads, STAR, featureCounts, DESeq2 | 5,967 | 97 | Byte-identical to 0.32.1 |
| Rice CY1000 salt-stress subset (*O. sativa* Japonica) | Reads, STAR, featureCounts, DESeq2 | 23,935 | 12,171 | Byte-identical to 0.32.1 |
| Arabidopsis hub2-3 vs Col-0, ATH1 microarray | GEO series matrix, limma | 21,323 | 1,401 | Byte-identical to 0.32.1 |
| Yeast cbc2Δ vs wild type, YG-S98 microarray | GEO series matrix, limma | 5,683 | 486 | Byte-identical to 0.32.1 |
| *F. graminearum* PH-1 spores vs mycelium | Reads, STAR, featureCounts, DESeq2 | 9,028 | 5,734 | Byte-identical to 0.32.1; KEGG now 9 ORA and 31 GSEA pathways |
| *F. graminearum* Z-3639 heat shock | Reads, STAR, featureCounts, DESeq2 | 8,280 | 5,836 | Byte-identical to 0.32.1; KEGG now 0 ORA and 11 GSEA pathways |
| *M. oryzae* ΔMocreA vs wild type (GSE153084) | Reads, STAR, featureCounts, DESeq2 | 9,777 | 4,778 | First recorded value |
| Sorghum sulfur deficiency vs control (GSE184725) | Single-end reads, STAR, featureCounts, DESeq2 | 20,591 | 302 | First recorded value |

Every run ends with an overall WARNING or REVIEW_REQUIRED rather than PASS, from advisory checks such as the small-sample Wilcoxon diagnostic and the static network layout. On both *Fusarium* datasets, check 10 is REVIEW_REQUIRED because g:Profiler recognises none of their RefSeq gene identifiers; their GO enrichment comes from annotation transfer instead (check 25), KEGG now resolves for them, and their differential-expression results are unaffected. The organisms without a curated annotation package, *Fusarium*, *M. oryzae*, sorghum and rice, receive annotation-transfer enrichment; rice maps 61.0% of its genes with an adjusted p-value to STRING proteins, so its check 25 reads WARNING.

## Citation

Cite the exact BulkSeq Studio release that produced your analysis, as recorded in the run summary, together with the repository address, https://github.com/tunabirgun/bulkseq-studio.

## License

BulkSeq Studio is released under the [MIT License](LICENSE).
