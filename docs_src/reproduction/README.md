# Learning-example reproduction

Run each phase separately from the repository root. Phases 1–3 use synthetic counts, identifiers, gene sets and network scores. Phase 4 constructs illustrative study estimates; phase 5 retrieves a separate public STRING network. The differential-expression estimates come from actual package fits; they are not results from a biological study or a complete BulkSeq run.

## Environment

The model phase uses the R and Bioconductor packages pinned in `workflow/envs/bulkseq.lock.yaml`, tested on Linux through WSL. Restore that Linux environment with `micromamba create -n lesson-models -f workflow/envs/bulkseq.lock.yaml`, or use an existing matching environment. Its exact R and package versions are recorded in `docs_src/model-examples.json` under `provenance`; the model script also prints `sessionInfo()`. Native macOS execution has not been verified.

The derived-example phase needs Python 3.12 or newer. Create an isolated environment with `python -m venv tmp/lesson-env`, activate it, then run `python -m pip install -r docs_src/reproduction/requirements.txt`. The small matrices run serially. The model phase explicitly uses one BiocParallel worker and one BLAS/OpenMP thread.

## 1. Fit the simulated data

```sh
Rscript --vanilla docs_src/reproduction/01_model_examples.R
```

This phase generates negative-binomial counts with seeds next to each stochastic step, fits the B-versus-A contrast with DESeq2, edgeR quasi-likelihood and limma-voom, and computes apeglm effects. Each engine keeps its documented filter and normalization. It writes `docs_src/model-examples.json`, including raw counts, full-precision estimates and transformed expression. The small DESeq2 example falls back from VST to rlog as the application does; the selected transformation is recorded. Review successful completion and finite outputs before the next phase.

## 2. Derive the learning states

```sh
python docs_src/reproduction/02_learning_examples.py
```

This phase reads the saved model output and writes `docs_src/learning-examples.json`. PCA uses the most variable transformed genes, centring without variance scaling. Heatmaps rank genes by adjusted p-value, use within-gene sample-standard-deviation z-scores, clip before Euclidean/Ward clustering, and save the complete merge trees. The enrichment counterexample holds the foreground and sets fixed while adding ineligible features to the background; its upper-tail hypergeometric p-values receive BH correction across all three toy sets. The design matrices and association network are controlled examples independent of the model fits.

## 3. Check the values independently

```sh
Rscript --vanilla docs_src/reproduction/03_validate_examples.R
python -m pytest -q tests/test_docs_learning.py tests/test_docs_parameters.py
```

The R check independently recalculates PCA with `prcomp`, Ward trees with `hclust(method="ward.D2")`, row scaling, design rank, normalization and hypergeometric/BH values. PCA geometry is compared without depending on arbitrary axis signs; clustering is compared by cophenetic distance without depending on reversible branch order. The numerical tolerance is 3e-8 for cross-library calculations. Pytest independently checks source-to-display consistency, row scaling and exact combinatorial enrichment probabilities and includes deliberately corrupted fixtures. A discrepancy must be resolved before rebuilding the website. The R checker accepts optional model and learning JSON paths to test corrupted copies without overwriting the reference fixtures.

## 4. Render the website

```sh
node docs_src/build.mjs
```

The build embeds the verified values and default SVG views directly in `docs/index.html`; no network request or statistical fitting is needed in the browser. `docs_src/learning-core.cjs` supplies the static and interactive rendering, and the build copies it to `docs/assets/learning-core.js`. Runtime controls select saved states or apply explicit display filters. Animated transitions interpolate positions for 260 ms; the volcano comparison uses 620 ms and selection guides use 200 ms. Reduced-motion mode makes these updates immediate. Verify every control, reset, label clearance, keyboard inspection and mobile layout in a browser after rebuilding.

## Method references

- [DESeq2 methods and transformations](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html)
- [R hierarchical clustering and Ward.D2](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/hclust.html)
- [SciPy linkage conventions](https://docs.scipy.org/doc/scipy/reference/generated/scipy.cluster.hierarchy.linkage.html)
- [STRING association interpretation](https://string-db.org/help/getting_started/)

## 4. Fit the forest-plot examples

```sh
Rscript --vanilla docs_src/reproduction/04_forest_examples.R
```

This phase uses constructed unshrunken log2 fold-change estimates and standard errors for four studies. It fits all four studies and each leave-one-out subset through metafor using DerSimonian–Laird random effects with z-Wald 95% intervals. No differential-expression fit, combined p-value, adjusted p-value or meta-DEG call is implied. The script checks an independent DL calculation before writing `docs_src/forest-examples.json`; its one-based omission indices follow R. The fixtures and visual weight contract also have independent Python checks and deliberate estimate/weight corruption tests in `tests/test_forest_examples.py`.

## 5. Retrieve or validate the STRING example

```sh
python docs_src/reproduction/05_string_snapshot.py
```

This phase maps twelve fixed public teaching symbols in human STRING v12.5, checks every mapping, waits between API requests and retrieves only functional associations among those proteins at score 400 or higher. It writes `docs_src/network-example.json` with the query, mapping pairs, version, retrieval date, score scale and CC BY 4.0 attribution. The browser uses this cache, never a live query. Retrieval needs a network connection; validation does not.

```sh
python docs_src/reproduction/05_string_snapshot.py --validate docs_src/network-example.json
python -m pytest -q tests/test_forest_examples.py tests/test_docs_network_fixture.py tests/test_docs_explorers.py
```

The network tests deliberately corrupt an edge score and swap two protein labels. Browser graph counts are independently checked with union-find across every displayed cutoff. Do not treat the cached network as a result from the synthetic expression data.

## 6. Generate the standard report example

```sh
python -m pip install -r docs_src/reproduction/report-requirements.txt
python docs_src/reproduction/06_standard_report.py
```

Use the project Python environment and the pinned Pillow dependency for PNG rendering. The script reuses the saved DESeq2 fixture, derives normalized mean counts with the same median-ratio rule, writes full and selected tables, and calls the application’s HTML report renderer. It does not refit a model or execute a complete workflow. Its status record explicitly requires review because this is a synthetic teaching artifact. `--output` selects another example directory; the default is `docs/examples/standard`.

PNG text uses Times New Roman when available, with installed Times, Liberation Serif, DejaVu Serif or FreeSerif fallbacks at the same text size. `BULKSEQ_REPORT_FONT` selects a specific font file; an unavailable explicit font stops generation rather than silently substituting another face.

In the pinned R environment, independently check normalized mean counts:

```sh
Rscript --vanilla docs_src/reproduction/06_standard_validation.R .
```

## 7. Generate the meta-analysis report example

```sh
python docs_src/reproduction/07_meta_report.py --output tmp/meta-report-example
```

This phase needs the project Python environment, R with metafor, metaRNASeq and jsonlite, and `rsvg-convert` on PATH. It executes `tests/meta_pooled_methods.R`, which calls the application’s combination code, then renders a report from two constructed study-level tables. The three complete-case features form the entire pooled adjustment family; the matching-sign combined family contains one feature. These are constructed estimates, not per-study DESeq2 fits. The report and check records state the limited scope and that enrichment was not run.

`--rscript` selects an R executable. After inspecting the generated example, regenerate the served copy with `--output docs/examples/meta --overwrite`. This overwrites the example’s named output files without deleting its directory. Inspect both SVG and PNG figures and the generated report before packaging.

## 8. Package and verify the examples

```sh
python docs_src/reproduction/08_package_example_reports.py
```

The two archives contain only the reviewed example project files, including each report and its linked tables. The packager rejects scripts, temporary directories, logs and unexpected file types, stores text with LF line endings for portable checkout consistency, then checks every archive member against its source file. PNG bytes remain unchanged. Rebuild the ZIP files whenever an example changes.

```sh
python -m pytest -q tests/test_standard_example_report.py tests/test_meta_example_report.py tests/test_docs_report_examples.py
```

The standard reproduction test builds in two temporary directories and compares their outputs; it does not modify the served example. The meta checks include an independent inverse-normal and common-effect calculation and a deliberately corrupted pooled estimate. The application fixture’s `--inject-old-family` option must fail, exercising the corrected pooled BH family. Final browser checks cover the actual report tables, figure viewer, links and mobile layout.
