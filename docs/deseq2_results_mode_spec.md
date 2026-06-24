# Implementation spec: `input.type = "deseq2_results"` upload mode

Final, code-accurate spec. Reviewer corrections override the designers throughout.
Verified against the repo at version 0.8.4 (constants.py). Target release: 0.9.0.

## 0. The defining constraint

Downstream rules split by what they read from disk:

- **CSV-only consumers (work unchanged):** `enrichment` (`run_enrichment.R`), `set_overlap`
  (`run_set_overlap.R`), `network_string` (`build_string_network.R` reads up/down at
  97/98, results at 131), the interactive PPI (`ppi_graph.py`, degrades `meanExpr` to
  null when the normalized matrix is absent, node log2FC colour still works), and the
  `.rnk` half of `export_matrices`.
- **RDS consumers needing a real `dds`/`vsd` (NOT available from a results table):**
  `figures` (`make_figures.R` reads `dds`/`vsd` at 23, then `colData(dds)` at 115 and
  `plotPCA(vsd)` at 128 BEFORE any guard), `export_matrices` VST half
  (`export_downstream.R:19`), `sample_correlation`, `wilcoxon_sensitivity`,
  `genes_of_interest` (`make_goi.R:43,62,93`).

The mode ingests the uploaded table into the canonical artifacts and writes a **synthetic
RDS with no `dds`/`vsd`**. The `figures` and `export_matrices` rules are monolithic (one
`readRDS` produces both results-derived and counts-only outputs), so they degrade
**internally**; the per-rule counts-only outputs are dropped from `final_targets()`.

### Synthetic RDS contract (the interface everything keys on — pin this)

`ingest_deseq2_results.R` writes:

```r
saveRDS(list(res = R, resLFC = R, symbol_map = sm, assay_kind = "results_only"),
        snakemake@output[["rds"]])
```

where `R` is a data.frame with **`rownames(R)` set to `gene_id`** and columns
`baseMean, log2FoldChange, lfcSE, stat, pvalue, padj` (numeric; NA allowed except
`log2FoldChange`). `resLFC` aliases `res` (no shrinkage available). `sm` is a named
character vector `gene_id -> symbol` (NA -> gene_id). **No `dds`, no `vsd`.**
Consumers that rely on this: `make_figures.R` (volcano reads `rownames(vol)` at 199,
pvalue reads `res$pvalue` at 321, MA reads `resLFC`+`baseMean`), and the `has_counts <-
!is.null(vsd)` test. `assay_kind="results_only"` is NOT `"log2_intensity"`, so
`is_intensity` stays FALSE and the MA x-axis keeps `scale_x_log10` (correct for a
count-mean baseMean). Single sentinel only — do not add a second boolean.

---

## 1. EXACT file changes, in order

### 1.1 `app/core/config_models.py`

- **Line 21** — extend the Literal:
  ```python
  type: Literal["fastq", "sra", "mixed", "count_matrix", "microarray", "deseq2_results"] = "fastq"
  ```
- **After line 28** (`count_matrix: str | None = None`) — add:
  ```python
  # When type == deseq2_results: a user-supplied DESeq2-style results table
  # (must contain gene_id + log2FoldChange + padj). Ingested into
  # results/deseq2/deseq2_results.csv; up/down sets are regenerated from
  # deseq2.alpha + deseq2.lfc_threshold. No counts -> PCA / sample-correlation /
  # count heatmaps / Wilcoxon / GOI are gated off.
  deseq2_results: str | None = None
  ```
- No pydantic root validator (mirrors count_matrix — there is none; emptiness is caught
  at runtime).

### 1.2 `workflow/Snakefile`

- **After line 80** (`MICROARRAY_MODE = ...`) — add:
  ```python
  # DESeq2-results mode: the user supplies a finished DESeq2 results table. Skip
  # download/QC/alignment/featureCounts/DESeq2; ingest the table into
  # results/deseq2/deseq2_results.csv and regenerate up/down. No raw or normalized
  # counts exist, so PCA / sample-correlation / count-heatmaps / Wilcoxon / GOI gate off.
  DE_RESULTS_MODE = INPUT.get("type") == "deseq2_results"
  DE_RESULTS_TABLE = INPUT.get("deseq2_results")
  ```
- **Line 84** — exempt the new mode from the single-end guard (no layout):
  ```python
  if not (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE) and not ALL_PAIRED:
  ```
- **`final_targets()` (127-186)** — six edits:
  1. **Lines 130 + 132-137** — gate the VST export and all sample-correlation outputs.
     Remove `"results/export/normalized_expression_matrix.csv"` (130) and the six
     sample_correlation entries (132-137) from the literal list, and re-add them guarded.
     Insert after the literal list is built (e.g. right after line 148, before the
     `if not MICROARRAY_MODE:` block):
     ```python
     if not DE_RESULTS_MODE:
         targets.append("results/export/normalized_expression_matrix.csv")
         targets += [
             "results/figures/sample_correlation_pearson.png",
             "results/figures/sample_correlation_pearson.svg",
             "results/figures/sample_correlation_spearman.png",
             "results/figures/sample_correlation_spearman.svg",
             "results/export/sample_correlation_pearson.csv",
             "results/export/sample_correlation_spearman.csv",
         ]
     ```
     Keep `"results/export/ranked_genes.rnk"` (131) unconditional (results-derived).
  2. **Lines 138-140** (wilcoxon png/svg + csv) — move under `if not DE_RESULTS_MODE:`
     (needs the RDS count matrix). `set_overlap` (141-143) stays (CSV-only).
  3. **Line 150** — extend the counts.txt / unchanged_genes guard:
     ```python
     if not (MICROARRAY_MODE or DE_RESULTS_MODE):
         targets.insert(0, "results/counts/counts.txt")
         targets.append("results/deseq2/unchanged_genes.csv")
     ```
  4. **Line 153** — extend the MultiQC guard:
     ```python
     if not (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE):
         targets.insert(0, "results/qc/multiqc/multiqc_report.html")
     ```
  5. **Lines 156-159** (figures block) — mode-aware figure list:
     ```python
     if WF.get("figures", True):
         if DE_RESULTS_MODE:
             fig_list = ("ma_plot", "volcano", "pvalue_histogram")
         else:
             fig_list = ("pca", "sample_distance", "ma_plot", "volcano", "top_deg_heatmap")
         for fig in fig_list:
             targets.append(f"results/figures/{fig}.png")
             targets.append(f"results/figures/{fig}.svg")
     ```
  6. **Line 174** (GOI gate) — suppress in this mode (make_goi.R needs counts):
     ```python
     if config.get("gene_sets", {}).get("custom_gene_list") and not DE_RESULTS_MODE:
     ```
  Enrichment (160-173) and PPI (178-185) blocks stay unchanged (CSV-only).

### 1.3 `workflow/rules/deseq2.smk` — three-way branch (mandatory, not optional)

`rule deseq2` and `rule limma_de` both own `results/deseq2/deseq2_results.csv`; a new
ingest rule writing the same path needs the `elif` branch or Snakemake raises an
ambiguity/collision. Insert `elif DE_RESULTS_MODE:` between line 39 (end of `limma_de`)
and line 41 (`else:`):

```python
elif DE_RESULTS_MODE:

    # User supplied a finished DESeq2 results table. Validate + normalize it into
    # the canonical results CSV, regenerate up/down from alpha + lfc, and write a
    # synthetic RDS (no dds/vsd) so figures/export degrade to results-only outputs.
    rule ingest_deseq2_results:
        input:
            table=DE_RESULTS_TABLE,
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            contrast=_CONTRAST.get("factor", "condition"),
        log:
            "logs/ingest_deseq2_results.log",
        script:
            "../scripts/ingest_deseq2_results.R"
```

Note: this rule deliberately does NOT declare `normalized`, `unchanged`, or
`equivalence_check` (no counts -> no normalized matrix; no dds -> no TOST). `_DE`,
`_CONTRAST` already exist at deseq2.smk:4-5.

### 1.4 NEW `workflow/scripts/ingest_deseq2_results.R`

Mirror `run_deseq2.R:171-175` for the up/down split byte-for-byte in logic.
Behavior:
1. Read the uploaded table; sep by extension (`,` for `.csv`, else `\t`),
   `check.names = FALSE`, `stringsAsFactors = FALSE`.
2. Normalize headers (case-sensitive `%in% names(df)`) to the canonical schema:
   - `gene_id`: first of `gene_id` / `gene` / `id` / `Geneid`, else first column.
     **Required**, must be unique + non-NA (mirror `ingest_counts.py:44-50` duplicate
     rejection: `sys.exit`/`stop()` with guidance, do NOT silently keep first).
   - `log2FoldChange`: `log2FoldChange` / `log2FC` / `logFC`. **Required**, `stop()` if absent.
   - `padj`: `padj` / `adj.P.Val` / `FDR` / `qvalue`. **Required**, `stop()` if absent.
   - `pvalue`: `pvalue` / `P.Value` / `pval` (optional -> NA).
   - `stat`: `stat` / `t` / `statistic` (optional). **If absent, synthesize**
     `stat = sign(log2FoldChange) * -log10(pmax(padj, .Machine$double.xmin))` so the
     `.rnk` and PPI seeding stay non-empty. (Synthesize here, NOT in export_downstream;
     ship one fix.)
   - `baseMean` (optional -> NA), `symbol` / `lfcSE` / `biotype` (optional -> NA).
3. Coerce numeric columns; order by `padj`; `write.csv(res_out, results, row.names = FALSE)`
   with column order `baseMean, log2FoldChange, lfcSE, stat, pvalue, padj, gene_id, symbol, biotype`.
4. Build `symbol_map` (named vector gene_id -> symbol, NA/empty -> gene_id).
5. Up/down (exact mirror of run_deseq2.R:171-175):
   ```r
   sig  <- !is.na(res_out$padj) & res_out$padj < alpha
   up   <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >=  lfc_thr, ]
   down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
   up   <- up[order(-up$log2FoldChange), ]
   down <- down[order(down$log2FoldChange), ]
   write.csv(up,   up_path,   row.names = FALSE)
   write.csv(down, down_path, row.names = FALSE)
   ```
6. Build `R` (data.frame, `rownames(R) <- res_out$gene_id`, columns
   `baseMean, log2FoldChange, lfcSE, stat, pvalue, padj`) and
   `saveRDS(list(res=R, resLFC=R, symbol_map=sm, assay_kind="results_only"), rds)`.
7. Write `checks/08_metadata_design_qc.json` (PASS, "results table ingested"),
   `checks/09_deseq2_qc.json` (PASS if n_sig>0 else REVIEW_REQUIRED, message mirroring
   run_deseq2.R:180-184: `"%d genes padj < %.3g; %d up / %d down at |log2FC| >= %.2g"`),
   and `sessionInfo.txt`. Reuse the `write_check` helper pattern from run_deseq2.R.

### 1.5 `workflow/scripts/make_figures.R` — guard in place (do NOT reorder)

- **After line 23** — add: `has_counts <- !is.null(vsd)`
- **Line 115** — wrap the `colData(dds)` group-var lookup (`group_var` is consumed only
  by count figures):
  ```r
  if (has_counts && !(group_var %in% colnames(colData(dds)))) group_var <- colnames(colData(dds))[1]
  ```
- **PCA body (128-144)** — wrap in `if (has_counts) { ... } else save_placeholder(na_msg, out[["pca_png"]], out[["pca_svg"]])`.
- **Sample-distance body (147-164)** — same wrap with `out[["dist_png"]]`/`out[["dist_svg"]]`.
- **Top-DEG heatmap body (285-315)** — same wrap with `out[["heatmap_png"]]`/`out[["heatmap_svg"]]`.
- **MA (166-195)** — add a baseMean guard: when `!("baseMean" %in% colnames(ma)) ||
  all(is.na(ma$baseMean))`, `save_placeholder("MA plot needs baseMean", out[["ma_png"]], out[["ma_svg"]])`;
  else run the existing block. (With a full DESeq2 upload baseMean is present.)
- **Volcano (197-279)** and **pvalue (317-333)** — unchanged (read `resLFC`/`res$pvalue`,
  no dds/vsd; pvalue already placeholders when all-NA).
- **Diagnostics (line 339)** — change `if (!is_intensity) {` to `if (has_counts && !is_intensity) {`
  and generalize the placeholder text at line 394 from `"Diagnostic not applicable (microarray)"`
  to e.g. `"Diagnostic not applicable (no count matrix in this mode)"`.

### 1.6 `workflow/scripts/export_downstream.R` — vst-NULL guard

- **Lines 16-21** — guard the VST half:
  ```r
  vsd <- obj$vsd
  if (is.null(vsd)) {
    writeLines("gene_id", snakemake@output[["vst"]])   # header-only stub
  } else {
    mat <- as.data.frame(assay(vsd)); mat <- cbind(gene_id = rownames(mat), mat)
    write.csv(mat, snakemake@output[["vst"]], row.names = FALSE)
  }
  ```
  The `.rnk` block (25-32) is unchanged — `stat` is always present in the CSV (real or
  synthesized at ingest). The VST CSV is written as a stub but is NOT a `final_targets()`
  target in this mode (1.2.1), so it never blocks.

### 1.7 `workflow/rules/checks.smk`

- **After line 24** (end of the `elif COUNT_MATRIX_MODE:` block) — add a branch:
  ```python
  elif DE_RESULTS_MODE:
      # No reference/alignment/quantification; no TOST equivalence (needs a dds).
      ALL_CHECKS = [
          "checks/00_project_setup.json",
          "checks/01_input_validation.json",
          "checks/08_metadata_design_qc.json",
          "checks/09_deseq2_qc.json",
      ]
  ```
- **Line 39** — gate the unconditional wilcoxon append (else it forces the
  `wilcoxon_sensitivity` rule -> `assay(NULL)` crash):
  ```python
  if not DE_RESULTS_MODE:
      ALL_CHECKS.append("checks/14_wilcoxon_sensitivity.json")
  ```
  Lines 36-37 (enrichment), 40-41 (set_overlap), 43-44 (ppi) stay (CSV-only).

### 1.8 `workflow/rules/reports.smk` — gate `final_reports` like microarray

Results mode has the same input profile as microarray (no counts, no multiqc), which
already runs — so gating identically is proven safe.

- **Line 26**:
  ```python
  **({} if (MICROARRAY_MODE or DE_RESULTS_MODE) else {"counts": "results/counts/counts.txt"}),
  ```
- **Line 28**:
  ```python
  **({} if (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE) else {"multiqc": "results/qc/multiqc/multiqc_report.html"}),
  ```
`export_matrices` (8-18) is unchanged at the rule level (script does the vst stub).

### 1.9 `app/ui/main_window.py`

- **After line 375** (count-matrix `cm_row` block) — add a DESeq2-results import row:
  ```python
  dr_row = QHBoxLayout()
  dr_btn = QPushButton("Use a DESeq2 Results Table (skip alignment + DESeq2)")
  dr_btn.setToolTip("Start from a ready DESeq2 results CSV (gene_id + log2FoldChange + "
                    "padj required). Produces enrichment, volcano/MA, PPI, set-overlap; "
                    "PCA / sample-correlation / count heatmaps / Wilcoxon / GOI are skipped.")
  dr_btn.clicked.connect(self._import_deseq2_results)
  dr_row.addWidget(QLabel("Already have DE results?"))
  dr_row.addWidget(dr_btn); dr_row.addStretch(1)
  layout.addLayout(dr_row)
  ```
- **New handler `_import_deseq2_results()`** (model on `_import_count_matrix`, 487-544):
  file dialog (`*.csv *.tsv *.txt`), validate the header has gene_id + log2FoldChange +
  padj synonyms (warn + return if not), copy to `config/deseq2_results.csv` (write as
  canonical CSV), set `self.config.input.type = "deseq2_results"` and
  `self.config.input.deseq2_results = "config/deseq2_results.csv"`, write a one-row
  `samples.tsv` (`{"sample_id":"results","condition":"unknown","layout":"n/a","fastq_1":""}`)
  to satisfy Snakefile:18 and `01_input_validation` (condition "unknown" avoids the
  validate_metadata <2-replicate warning), clear stale fields:
  `self.config.input.count_matrix = None`, `self.config.microarray.gse_accession = None`,
  and clear a stale `SYMBOL` keytype (mirror 537-538) so FBgn falls back to FLYBASE.
  Then `save_config`, `gse_box.clear()`, `_apply_input_mode_ui()`, populate
  `input_preview` with a row/column summary.
- **`_apply_input_mode_ui()` (461-485)** — add before the `else` (482):
  ```python
  elif mode == "deseq2_results":
      self.reference_mode_banner.setText(
          "Results mode: alignment, DESeq2, counts, PCA, sample correlation and the "
          "count heatmap are skipped. Volcano / MA, enrichment, and the STRING PPI "
          "network are produced from your table. Select your organism below to enable "
          "GO/KEGG enrichment and PPI.")
      self.reference_mode_banner.setVisible(True)
  ```
- **Stale-field clears in the OTHER handlers** — every site that sets
  `count_matrix = None` must also set `deseq2_results = None`:
  - **Line 2458** (fastq handler): add `self.config.input.deseq2_results = None`.
  - The SRA handler and GEO/microarray handler (search for `self.config.input.count_matrix = None`
    / `self.config.input.type = "sra"` / `... = "microarray"`): add the same clear.
- **`allow_pending_sra` tuples** — **line 2541** and **line 2745**: add `"deseq2_results"`:
  ```python
  "sra", "count_matrix", "microarray", "deseq2_results")
  ```
- **`no_reference_mode` — line 2796**:
  ```python
  no_reference_mode = self.config.input.type in ("count_matrix", "microarray", "deseq2_results")
  ```
- **Run-monitor step labels — line 1170** (beside `("ingest_counts", ...)`), add:
  ```python
  ("ingest_deseq2_results", "Reading the DESeq2 results table"),
  ```
- **Outputs tab `_refresh_output_table_pick` (3009-3031)** — the new mode must not offer
  counts.txt / normalized_counts / unchanged_genes / wilcoxon. After line 3017, gate:
  ```python
  if itype == "deseq2_results":
      items = ["results/deseq2/deseq2_results.csv",
               "results/deseq2/upregulated_genes.csv",
               "results/deseq2/downregulated_genes.csv",
               "results/enrichment/kegg_ora.csv", "results/enrichment/kegg_gsea.csv",
               "results/stats/set_overlap.csv",
               "results/networks/enrichment_emap_nodes.csv",
               "results/networks/enrichment_genemap_nodes.csv",
               "results/networks/string_ppi_nodes.csv",
               "results/networks/ppi_hub_genes.csv"]
  else:
      # existing 3015-3024 logic
  ```
  (No change needed to the static list at 1408-1414 — it is replaced at runtime by
  `_refresh_output_table_pick`.)
- `app/core/project.py` — no change (no input-type branching there).

### 1.10 `app/constants.py`

- **Lines 4-5** — `APP_VERSION = "0.9.0"`, `WORKFLOW_VERSION = "0.9.0"`.

### 1.11 `pyproject.toml`, `packaging/installer.iss`

- Bump version string to `0.9.0` in both.

### 1.12 `README.md`

- Add the `deseq2_results` row to the Input-modes prose (the count_matrix / microarray
  entries) and paste section 2 below.

### 1.13 `tests/`

- New `tests/test_deseq2_results_ingest.R` is impractical in the Python harness; instead
  add a **config-model test** in `tests/test_config_generation.py`:
  ```python
  def test_deseq2_results_input_mode():
      from app.core.config_models import InputConfig
      c = InputConfig(type="deseq2_results", deseq2_results="config/deseq2_results.csv")
      assert c.type == "deseq2_results"
      assert c.deseq2_results == "config/deseq2_results.csv"
  ```
- Add a `final_targets()` gating test (mirror any existing Snakefile-target test, or a
  new one) asserting that with `type="deseq2_results"` the target set EXCLUDES
  `results/counts/counts.txt`, `results/figures/pca.png`,
  `results/export/normalized_expression_matrix.csv`,
  `results/figures/wilcoxon_concordance.png` and INCLUDES `results/figures/volcano.png`,
  `results/figures/ma_plot.png`, `results/enrichment/kegg_ora.csv`,
  `results/networks/string_ppi_nodes.csv`.
- Confirm `pytest tests -q` stays green (currently 53 passing).

---

## 2. DESeq2 results table format + ready-to-paste README section

### Format (authoritative)

- One CSV (the format `DESeq2::results()` writes via `write.csv`). TSV/`.txt` accepted;
  the ingest sniffs the delimiter by extension.
- Header on line 1; column names case-sensitive; no comment lines above the header.
- `gene_id` unique + non-empty (duplicates/missing rejected).
- `NA`/empty allowed in `padj` (treated as not significant); not allowed in `gene_id`/`log2FoldChange`.
- `.` decimal separator, UTF-8, no thousands separators.
- Up/down lists are DERIVED (`padj < alpha & |log2FoldChange| >= lfc_threshold`); the
  user does not upload them.

### README block (paste under the Input-modes section)

```markdown
## Input mode: DESeq2 results table (`input.type: deseq2_results`)

Upload a ready differential-expression results table and BulkSeq Studio produces
everything derivable from it — functional enrichment (GO, KEGG, GSEA), the volcano and
MA plots, the raw p-value histogram, the STRING protein-protein interaction network, the
MSigDB set-overlap test, and a preranked `.rnk` — without re-aligning reads or re-running
DESeq2.

Outputs that need the raw or normalized count matrix are not produced in this mode,
because a results table does not carry per-sample expression values: PCA, sample-distance
and top-DEG heatmaps, sample correlation, dispersion / Cook's-distance / library-size
diagnostics, the Wilcoxon concordance test, the normalized-expression export, and the
genes-of-interest heatmaps.

### File format

- One CSV file with a header row (the format `DESeq2::results()` writes via `write.csv`).
- Column names are case-sensitive and must match the names below.
- `gene_id` must be unique and non-empty. Duplicate or missing `gene_id` values are rejected.
- `NA` (or an empty field) is allowed in `padj` and is treated as "not significant".
- Use `.` as the decimal separator, UTF-8 encoding, and no comment lines above the header.

### Columns

| Column | Required | Type | Used for | If absent |
|---|---|---|---|---|
| `gene_id` | yes | text, unique | join key for every analysis | upload rejected |
| `log2FoldChange` | yes | number | up/down split, volcano, MA, GSEA ranking, PPI colour | upload rejected |
| `padj` | yes | number (NA allowed) | significance, enrichment universe | upload rejected |
| `baseMean` | no | number >= 0 | MA-plot x-axis | MA plot placeholdered |
| `pvalue` | no | number 0-1 | p-value histogram | histogram placeholdered |
| `stat` | no | number | preranked `.rnk` | synthesized from sign(log2FC) x -log10(padj) |
| `symbol` | no | text | figure labels, PPI seed | falls back to gene_id |
| `lfcSE` | no | number | carried through | no effect |
| `biotype` | no | text | carried through | no effect |

Extra columns are ignored. The up- and down-regulated gene lists are derived from the
table using `deseq2.alpha` and `deseq2.lfc_threshold`:
`padj < alpha & |log2FoldChange| >= lfc_threshold`.

### Minimal example (three required columns)

```csv
gene_id,log2FoldChange,padj
ENSG00000141510,3.21,1.2e-08
ENSG00000133703,-2.04,4.7e-05
ENSG00000136997,0.12,0.83
```

### Full example (keeps MA, p-value histogram, symbol labels, stat-ranked .rnk)

```csv
baseMean,log2FoldChange,lfcSE,stat,pvalue,padj,gene_id,symbol,biotype
1542.3,3.21,0.41,7.83,5.1e-15,1.2e-08,ENSG00000141510,TP53,protein_coding
880.7,-2.04,0.39,-5.23,1.7e-07,4.7e-05,ENSG00000133703,KRAS,protein_coding
410.2,0.12,0.33,0.36,0.718,0.83,ENSG00000136997,MYC,protein_coding
```

### Choosing the reference preset (so enrichment, KEGG, and STRING resolve)

The gene-ID namespace in your `gene_id` column must match the keytype the reference
preset selects, or no terms map. Pick the preset for your organism:

| Organism | `gene_id` should be | KEGG / STRING taxid |
|---|---|---|
| Homo sapiens | Ensembl `ENSG...` (version suffix OK) | hsa / 9606 |
| Mus musculus | Ensembl `ENSMUSG...` | mmu / 10090 |
| Drosophila melanogaster | FlyBase `FBgn...` | dme / 7227 |
| Caenorhabditis elegans | Ensembl `WBGene...` | cel / 6239 |
| Danio rerio | Ensembl `ENSDARG...` | dre / 7955 |
| Saccharomyces cerevisiae | systematic ORF `YAL001C` | sce / 4932 |
| Arabidopsis thaliana | TAIR locus `AT1G01010` | ath / 3702 |

For an organism without a Bioconductor OrgDb (most fungi, many crops), GO via OrgDb is
skipped but KEGG still runs: use native KEGG locus tags (e.g. `FGSG_00001`), set
`enrichment.kegg_organism` (e.g. `fgr`), and set `ppi.taxon` to the STRING taxid (e.g.
`229533` for Fusarium graminearum PH-1). NCBI RefSeq `LOC<GeneID>` ids are mapped to the
bare NCBI GeneID automatically.
```

---

## 3. Feasibility / gating decisions

### Produced (render with real data)
volcano, MA (when baseMean present), pvalue_histogram; full enrichment (GO/KEGG/GSEA +
enrichment figures + enrichment networks emap/genemap); set_overlap (dotplot + csv);
STRING PPI (graphml/sif/cyjs/nodes/edges/hubs/figure) + interactive PPI tab
(`meanExpr` null, log2FC colour works); `ranked_genes.rnk`; reports
(run/timing/software-versions); sanity checks 00/01/08/09 (+10 enrichment, +15 set_overlap,
+16 PPI when enabled).

### Gated out of `final_targets()` entirely (never requested, rule never runs)
counts.txt, unchanged_genes.csv (TOST), MultiQC, sample_correlation (4 figs + 2 csv),
normalized_expression_matrix.csv (still written as a header-only stub by export_matrices,
not requested), wilcoxon (2 figs + csv + check 14), GOI (2 figs + report + csv), and
checks 05/06/07/13/14.

### Placeholdered (declared output exists, labelled "not applicable")
Within the monolithic `figures` rule (which always declares 16 outputs but is requested
only for ma/volcano/pvalue): pca, sample_distance, top_deg_heatmap, dispersion,
cooks_distance, library_size are written as `save_placeholder` images. These are not
final targets in this mode, so they materialize only as a side effect of the rule running;
the placeholder path exists so the rule never crashes on NULL dds/vsd.

### GOI decision (deviation from the task brief — surface it)
The brief lists GOI as derivable. It is NOT: `make_goi.R:43` (`colData(dds)`), `:62/:93`
(`vsd`) all dereference per-sample counts unconditionally and crash on the synthetic RDS.
Report-only GOI would require a make_goi.R rewrite (out of scope). **Resolution: gate GOI
off entirely** (Snakefile 1.2.6). Recorded as a risk.

---

## 4. Validation run (real small DESeq2 table)

### Dataset: reuse pasilla (`~/bsrun_pasilla`)
Only validation project with GO + GSEA (has `org.Dm.eg.db` + symbols), small, known
68-node STRING PPI. The uploaded input is pasilla's own
`~/bsrun_pasilla/results/deseq2/deseq2_results.csv` (the exact 9-column schema), so the
CSV-only consumers receive identical input and outputs can be diffed.

### Build the project `~/bsq_deseq2res`
```bash
mkdir -p ~/bsq_deseq2res/config ~/bsq_deseq2res/data/uploaded ~/bsq_deseq2res/workflow
cp ~/bsrun_pasilla/results/deseq2/deseq2_results.csv ~/bsq_deseq2res/data/uploaded/deseq2_results.csv
cp ~/bsrun_pasilla/config/samples.tsv               ~/bsq_deseq2res/config/samples.tsv
```
`config/config.yaml` essentials (contrast + alpha/lfc must match pasilla to reproduce the
up/down counts):
```yaml
input:
  type: deseq2_results
  samples: config/samples.tsv
  deseq2_results: data/uploaded/deseq2_results.csv
reference:
  organism_name: Drosophila melanogaster
deseq2:
  alpha: 0.05
  lfc_threshold: 1.0
  contrasts: [{factor: condition, numerator: cg8144_rnai, denominator: untreated}]
ppi: {taxon: 7227, enabled: true}
gene_sets: {}
```
(Contrast is **cg8144_rnai vs untreated** — pasilla's actual condition levels — NOT
treated/untreated.)

### Sync workflow + run (targets first, then greedy flags)
```bash
REPO=/mnt/c/Users/tunabirgun/Desktop/"BulkSeq Studio"
P=~/bsq_deseq2res
cp -r "$REPO/workflow/scripts" "$P/workflow/"
cp -r "$REPO/workflow/rules"   "$P/workflow/"
cp    "$REPO/workflow/Snakefile" "$P/workflow/"
cd "$P"
export MAMBA_ROOT_PREFIX=$HOME/micromamba
# 1) dry-run: confirm final_targets() picks the right set under DE_RESULTS_MODE
$HOME/.local/bin/micromamba run -n bulkseq \
  snakemake -n --snakefile workflow/Snakefile --configfile config/config.yaml --cores 4
# 2) real run
$HOME/.local/bin/micromamba run -n bulkseq \
  snakemake --snakefile workflow/Snakefile --configfile config/config.yaml \
  --cores 4 --resources mem_mb=8000 --rerun-triggers mtime
```
Run step by step; check each output before the next.

### Pass criteria
1. **Dry-run target set**: includes `volcano/ma_plot/pvalue_histogram.{png,svg}`,
   `enrichment_summary.txt`, `kegg_ora.csv`, `kegg_gsea.csv`, enrichment figures/networks,
   `set_overlap.csv`, `ranked_genes.rnk`, `string_ppi_nodes.csv`; EXCLUDES `counts.txt`,
   `pca.*`, `sample_distance.*`, `top_deg_heatmap.*`, `sample_correlation*`,
   `wilcoxon*`, `normalized_expression_matrix.csv`, `unchanged_genes.csv`, `goi_*`,
   `multiqc_report.html`.
2. **No crash**: run completes; the `figures` rule writes ma/volcano/pvalue real +
   pca/dist/heatmap/disp/cooks/libsize placeholders (no NULL-dds/vsd error).
3. **Equivalence vs `~/bsrun_pasilla`** (strongest check; frame as row-counts + ID sets +
   identical .rnk ranking — NOT byte-identical CSV, since read.csv->write.csv reformats floats):
   - `wc -l upregulated_genes.csv` -> **61 rows** (+header); `downregulated_genes.csv` -> **85 rows** (+header).
   - `kegg_ora.csv`, `gsea.csv`, `go_ora_all.csv`: same term-ID sets / row counts as pasilla.
   - `string_ppi_nodes.csv`: **68 nodes** (network-reachability caveat: if string-db.org
     is down, build_string_network.R degrades to empty + a PASS check; do not hard-fail).
   - `ranked_genes.rnk`: identical ranking to pasilla's.
4. **MA x-axis** is log10 (count-mean baseMean; `is_intensity` FALSE).
5. **Checks**: `checks/sanity_checks.txt` present; 08/09 PASS; no FAIL anywhere; only
   00/01/08/09 (+10/15/16) present.
6. **GUI smoke**: import the CSV via the new button in the live app, confirm the mode
   banner, the Outputs tab table list omits counts/normalized/wilcoxon, and Start Run
   launches `ingest_deseq2_results` with the step label.

---

## 5. Screenshots to retake

No committed screenshot script exists; README PNGs are refreshed manually under `docs:`
commits. `BULKSEQ_SELFTEST` is the frozen-build QtWebEngine PPI gate only — not a
screenshot tool.

Procedure: launch the live `.venv` app (`".venv\Scripts\python.exe" -m app.main`), open
a project in the new mode, toggle theme via the in-app control, grab the window per theme,
recompose the montage to the existing layout.

| Asset | Action | Reason |
|---|---|---|
| `docs/screenshot-overview-light.png` | Retake | Input tab gains the DESeq2-results upload row |
| `docs/screenshot-overview-dark.png` | Retake if showcasing the mode | Outputs/figure list shifts (placeholders) |
| `docs/screenshot-ppi-interactive.png` | Leave | PPI tab unaffected by input mode |

PPI re-shoot caveat (from memory, do not re-discover): `win.grab()` cannot capture the
QtWebEngine cytoscape canvas — use `export_image` + a Pillow composite, never
`QScreen.grabWindow`.

---

## 6. Version bump + CHANGELOG

**0.8.4 -> 0.9.0** (minor; a new `input.type` is a minor bump, per the 0.4.0 microarray
precedent). Bump in lockstep: `app/constants.py` (APP_VERSION + WORKFLOW_VERSION),
`pyproject.toml`, `packaging/installer.iss`.

CHANGELOG.md entry:
```markdown
## 0.9.0
- New input mode `deseq2_results`: start from a ready DESeq2 results table (gene_id +
  log2FoldChange + padj required; the full 9-column DESeq2 schema is accepted) and produce
  every output derivable from it — GO/KEGG/GSEA enrichment, volcano, MA, p-value
  histogram, MSigDB set-overlap, the preranked .rnk, and the STRING PPI network. Up- and
  down-regulated sets are derived from the table using the configured alpha and
  log2-fold-change threshold.
- Outputs that require per-sample counts (PCA, sample-distance and top-DEG heatmaps,
  sample correlation, model diagnostics, the Wilcoxon test, the normalized-expression
  export, and genes-of-interest) are gated off or rendered as labelled placeholders, with
  no count-based targets requested.
- Input tab adds an upload control for the results table; README documents the format and
  per-organism gene-ID requirements. Screenshots refreshed.
```

Release steps (project convention): build via `scripts\build_release.ps1`; verify the
frozen build with `BULKSEQ_SELFTEST=1` + `BULKSEQ_SELFTEST_OUT` via
`Start-Process -Wait -PassThru` (PASS = `{webengine:true, version:"0.9.0", nodes:3,
pass:true}`, exit 0); keep the repo-root `BulkSeqStudio.exe` + `_internal` as the latest
build; commit + push `main` (no AI trailers, stage explicit paths), tag `v0.9.0`, push,
`gh release create v0.9.0` with installer + portable ZIP. Watch for the recurring
`Compress-Archive` AV-lock failure on the portable-ZIP step.

---

## 7. Risks / open questions

1. **GOI deviates from the brief.** The task lists GOI as derivable; it is not without
   per-sample counts (`make_goi.R:43/62/93`). Gated off. Report-only GOI (membership +
   per-gene DE stats from the CSV) is feasible only with a make_goi.R rewrite — flag for
   the user to decide if they want it as a follow-up.
2. **Synthetic-RDS coupling.** `make_figures.R` and `export_downstream.R` now branch on
   `is.null(vsd)` / `assay_kind`. Any future figure that assumes a real `dds`/`vsd`
   without a guard will crash in this mode. The contract (section 0) must be honored by
   new figure code.
3. **Duplicate gene_id is load-bearing** (known count-matrix defect, obs. 694). The
   ingest must reject duplicates, not silently keep the first, or PPI/`.rnk` corrupt.
4. **Organism/keytype mismatch** is the most likely user error: IDs not matching the
   preset's keytype -> empty enrichment. The README per-organism table and the cleared
   stale SYMBOL keytype mitigate; consider a GUI warning if the uploaded gene_id shape
   does not match the selected organism (future enhancement).
5. **STRING reachability** makes the 68-node equivalence check flaky; treat an empty PPI
   + PASS check as acceptable when string-db.org is unreachable (existing degrade path).
6. **lfcSE schema contract** (obs. 790): ingest sets lfcSE to NA when absent. No consumer
   reads lfcSE, so this is informational, but it preserves the documented 9-column schema.
7. **`final_targets()` test** must assert the gated SET, not just that volcano renders —
   a regression that re-adds counts.txt would otherwise pass silently.
