# BulkSeq Studio — Remediation & Completion Plan

Derived from a 6-lane audit of the codebase against its build spec and the
`bulk_transcriptomics.pdf` protocol (the scientific backbone). Ground truth at
start: 19 tests pass, the PySide6 GUI constructs with 10 tabs, but the **entire
Snakemake pipeline is placeholder** (every rule touches a sentinel file or writes
fake data; the R scripts are one-line TODO stubs). WSL2 (62 GB cap, 24 threads,
755 GB free) has no bio tools yet. Decision: **full end-to-end** — run the
pasilla subset (paired, 2×37 bp, verified via ENA) through STAR → featureCounts →
DESeq2 → figures for real.

## Key technical decisions
- **WSL execution model:** run via `micromamba run -n bulkseq snakemake …`; drop
  `--use-conda` (no rule has a `conda:` directive). Readiness probes use the same
  `micromamba run` path so "ready" matches what the runner can actually invoke.
- **Strandedness:** infer from STAR `ReadsPerGene.out.tab` (unstranded/fwd/rev
  columns) and feed the detected `-s` to featureCounts. Do **not** blindly default
  to `-s 2` — pasilla (Brooks 2011) is unstranded; protocol calls strandedness the
  #1 silent error. Expose the setting (with an "infer" option) in the GUI.
- **Provenance diff:** diff the live config against in-code schema defaults
  (`default_config()`), excluding the volatile `project` block, so the
  "Customized / Non-standard Parameters" list shows only meaningful changes.
- **Reference:** Ensembl release-111 `BDGP6.46` toplevel FASTA + matching GTF for
  Drosophila; STAR `--genomeSAindexNbases 11`, `--sjdbOverhang 36` (37 bp reads).
- **Run staging:** stage the real benchmark run under the WSL-native home (`~/`),
  not `/mnt/c`, for many-small-file I/O speed; the runner still supports `/mnt/c`.

## Phase 1 — Foundation correctness (app-side; verify via pytest + dry-run)
- Fix `snakemake_runner` to `micromamba run -n bulkseq`; drop `--use-conda`.
  Align `readiness` probes to the same mechanism. Add a test on the WSL string.
- Fix GUI config round-trip: `_load_project` must repopulate every workflow/
  resources/reference widget; `_start_snakemake` must save workflow settings.
- Fix provenance diff baseline (schema defaults, drop `project` block).
- Wire design-variable existence check (parse `design_formula`).
- Call `validate_working_directory` on project creation; WSL warning only when WSL
  execution is selected.
- Move `openpyxl` to main deps; fix `star_mem` no-op; replace `wmic` with stdlib.
- Conda envs: channel order conda-forge→bioconda + strict, pin protocol versions.
- Cleanup: delete dead UI re-export shims; unify `write_check`; tests → `tmp_path`.
- Add unit tests for provenance diff, timing parsing, sanity aggregation,
  input-pairing patterns, metadata-validation branches, config round-trip,
  WSL-path edge cases.

## Phase 2 — Real Snakemake pipeline (the core deliverable)
Rewrite the STAR→featureCounts→DESeq2→figures route as real rules with real
per-sample I/O (the current rule graph is fake):
- Reference download + `STAR --runMode genomeGenerate` (auto `genomeSAindexNbases`
  from genome length, `sjdbOverhang = readlen−1`).
- `fastp` per sample (paired: `--detect_adapter_for_pe -q 15 -u 40 -l 36`, thread
  cap 16); FastQC pre/post + MultiQC.
- `STAR` align (`--readFilesCommand zcat`, sorted BAM, `--quantMode GeneCounts`,
  trailing-`_` prefix) + `samtools index/flagstat`.
- Strandedness inference → `featureCounts -p --countReadPairs -t exon -g gene_id
  -s <inferred> -Q 10` → single matrix (+ `.summary`).
- `run_deseq2.R`: import matrix (drop 6 cols, integer), `colData` order guard,
  `DESeqDataSetFromMatrix(~condition)`, prefilter `rowSums(counts≥10) ≥ min group`,
  relevel ref, `DESeq`, `results(alpha=0.05)`, `lfcShrink(apeglm)`,
  `vst(blind=FALSE)`, write ordered CSV + RDS + `sessionInfo.txt`.
- `make_figures.R`: PCA (`ntop=500`), sample-distance heatmap (ward.D2), MA
  (resLFC), volcano (ggplot2+ggrepel+ggtext), top-DEG heatmap (z-scored, 30);
  export **PNG (dpi 300) + SVG**, no titles.
- `run_enrichment.R`: GO/KEGG ORA + seeded GSEA (best-effort; needs `org.Dm.eg.db`).
- Reports: real `run_summary` (with the customized-params diff), full
  `timing_summary` (wall-clock, cumulative, estimate-vs-observed verdict, per-step/
  phase), `software_versions` + `sessionInfo`, fixed sanity aggregation DAG.
- Single-end branching; alternative routes (HISAT2/Salmon/htseq/edgeR/limma) as
  loud-failing stubs keyed on config.

## Phase 3 — WSL2 bio environment install (heavy; long-running)
- Bootstrap WSL (curl/ca-certs), install micromamba, create `bulkseq` core env
  (snakemake, fastqc, multiqc, fastp, star, samtools, subread, sortmerna, bbmap),
  then the full R/DESeq2 stack. Record resolved versions (env export = lock).

## Phase 4 — Real end-to-end run (pasilla)
- Create the benchmark project under `~/`, download reference + FASTQs, run the
  real pipeline through DESeq2 + figures. Verify: real BAMs, real count matrix,
  non-degenerate DESeq2 result, PNG+SVG figures, complete reports/timing, sanity
  PASS on real data.

## Phase 5 — GUI feature completion (acceptance criteria)
DESeq2 contrast/reference-level builder; Reference Manager custom import/validate/
build-index + lock enforcement; Workflow Settings full params (fastp, SortMeRNA,
**strandedness**, enrichment, figure toggles); Resources per-rule + temp dir;
Metadata editor (column ops, paste, export, bulk-edit, assign-condition, restore);
Sanity phase-status list + REVIEW_REQUIRED approval gate; Run Monitor progress/
elapsed/ETA/stop/open-folder; Reports inline display + figure/MultiQC links; SRA
accession persistence; per-rule resource wiring (no oversubscription).

## Phase 6 — Catalog, calibration, docs, polish
Populate `reference_catalog.yaml` (real URLs, starting with the priority
organisms); runtime history calibration (`runtime_profiles.json`); reconcile
README; final test pass; provenance/version capture via WSL.

Each phase ends with a git commit and a verification step; heavier phases (3, 4)
check in before starting.
