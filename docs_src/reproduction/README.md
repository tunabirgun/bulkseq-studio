# Learning-example reproduction

Run each phase separately from the repository root. All input counts, identifiers, gene sets and network scores in these examples are synthetic. The differential-expression estimates come from actual package fits; they are not results from a biological study or a complete BulkSeq run.

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
