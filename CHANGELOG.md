# Changelog

## 0.32.0 — 2026-09-18

> **Scientific output changes.** Count-matrix imports that contain malformed values, negative fractions or undeclared fractional values are now rejected instead of being converted or rounded silently. Explicitly admitted RSEM/tximport estimated counts retain the existing rounding route. Microarray probes assigned to multiple distinct genes are now excluded before gene-level collapse instead of being assigned to the first listed gene. GSEA now retains independently filtered rows with a finite route-specific rank and finite raw p-value even when the adjusted p-value is missing; the ORA tested family is unchanged. Fallback KEGG rankings now reduce source aliases once in the final GeneID space, so an already-ranked alias group can also change. Cross-study enrichment excludes unresolved one-to-many identifier mappings instead of selecting the first returned Entrez identifier and applies the configured per-study effect threshold instead of admitting every FDR-significant nonzero effect. Alternative-engine contrasts now distinguish condition labels that previously collapsed to the same R-safe coefficient name. Synthetic regressions establish these corrected contracts; the historical pasilla, *Fusarium*, rice, GEO microarray, enrichment and meta-analysis benchmark claims have not been rerun under 0.32.0.

### Fixed

- **Installed environment provenance distinguishes a fixed lock from every floating solve.** The backend marker now records `lock` only when the exact linux-64 lock file was installed. A floating-spec install records `fallback` with that file's actual name and SHA-256, whether linux-64 reached it after a lock failure or native Linux ARM selected it directly because the fixed lock is platform-specific. Core installs remain `core`. Synthetic shell excerpts cover all four branches and reject the former ARM `lock` misclassification; they do not execute setup, install packages, or establish an end-to-end ARM run. The published AppImage remains x86-64.

- **Backend setup logs now remain writable for installed and portable applications.** The environment installer writes `wsl_bioenv_install.log` in the current user's BulkSeq Studio application-data directory rather than beside the bundled setup script. Windows WSL and native-Linux launchers pass that same location explicitly, and the readiness dialog reads it from the same path. A direct shell invocation falls back to the XDG user-data location. This changes diagnostics only; it does not install, rebuild, or alter an existing environment.

- **A failed environment verification can receive one bounded in-place repair.** When tool, R or import verification fails, setup reinstalls the same installed specification once and reruns verification. Post-link steps can redownload required files. A remaining verification failure still fails; this does not claim to repair unrelated corruption.

- **Critical interface controls expose their visible labels to Qt accessibility APIs.** Public-accession and GEO fields now retain visible labels instead of relying only on placeholders. The differential-expression engine, factor and groups, adjusted-p threshold, absolute log2 fold-change, advanced design and organellar settings, output and figure selectors, figure preview, and current-view PPI edge filter expose specific names and descriptions; composite labels now identify their actual controls. The adjusted-p threshold follows the recorded imported-results method, and the edge filter states that its 0–100 slider range maps to displayed confidence 0.00–1.00 without rebuilding the network. Tab leaves the multiline accession editor. Focused Qt regressions query names and label relationships in loaded and expanded views and exercise keyboard focus. This does not establish screen-reader conformance or replace native assistive-technology testing.

- **The cross-study report now uses the same native figure viewer as the main report.** `meta_analysis_report.html` no longer places an always-visible legacy overlay over the page. Its comparative figures open in a labelled modal dialog with Fit, Actual size and zoom controls; Escape and Close return focus to the figure trigger, and the dialog keeps keyboard focus within the viewer. The shared main-report component is an explicit workflow input for the meta-report rule, so a viewer repair regenerates cached cross-study reports. The figures and tables are unchanged.
- **Sortable report tables retain their header semantics.** Main and cross-study report headers remain column headers and contain one native labelled sort button. Mouse, Enter and Space cycle ascending, descending and the source order without changing saved results. `aria-sort` identifies only the active column, and the focus indicator remains visible in the report's light and dark presentations. Machine, imported-results provenance and full-configuration key/value tables now expose their left cells as row headers. Numeric sorting continues to use stored values when displayed text is grouped.
- **Cross-study enrichment foregrounds match published per-study DEG tables (*scientific*).** Each study's enrichment foreground now uses the same configured FDR and absolute log2-fold-change thresholds as its `upregulated.csv` and `downregulated.csv` tables. The strict FDR boundary remains `padj < alpha`, the effect boundary remains inclusive, and an exact zero effect is neutral when the configured threshold is zero. Nonfinite or missing adjusted p-values and effects are excluded. The resulting table selections are restricted to the shared tested universe and the same accepted ambiguity-aware mappings before enrichment; the convergent `meta_sig` policy, pooling models and universe definition are unchanged. Synthetic base-R regressions cover positive and zero thresholds, both directions, exact boundaries, missing and nonfinite values, shared-only genes and mapping ambiguity. No real study, enrichment analysis or published benchmark was rerun.
- **Wilcoxon small-sample wording is qualitative.** The sensitivity check now describes exact p-value discreteness and limited power for small groups instead of claiming that a two-sided p-value below 0.05 is unattainable whenever the smaller group has fewer than five samples. The Wilcoxon statistic, adjusted p-values, diagnostic status and differential-expression calls are unchanged; a pinned R 4.5.2 regression covers the exact 4-v-4 counterexample.
- **Alternative-engine contrasts retain raw condition identity (*scientific*).** edgeR quasi-likelihood, limma-voom and microarray limma now construct the configured numerator-minus-denominator comparison as a positional numeric contrast. Distinct admitted condition levels containing spaces, hyphens or dots, including `A-B` and `A.B`, therefore cannot collapse into the zero contrast `A.B - A.B`, and a sample-sheet column named `grp` cannot shadow the configured contrast factor during model construction. Synthetic internal variable names prevent group/covariate coefficient collisions; coefficient names are unique but do not define the estimand. Fixed-seed synthetic fits for all three engines agree numerically with an independently constructed clean-label design and numeric contrast, retain the expected effect direction, preserve sample identities, and preserve ordinary-label results. The additive group-means design, covariate values, thresholds, fitting methods and package pins are unchanged; interactions remain rejected. No real study or published benchmark was rerun.
- **GSEA eligibility is separated from ORA eligibility (*scientific*).** Built-in OrgDb, g:Profiler fallback, KEGG-only and custom gene-set routes admit a row to the ranked population when its adjusted p-value is missing but its selected rank and raw p-value are finite. Main enrichment retains its established signed-statistic choice and fallback after route-specific QC and identifier acceptance; custom enrichment intentionally remains ranked by log2 fold change. An additional adjusted-p-missing row without a finite raw p-value, including a DESeq2 Cook's-distance outlier, and an additional row with a nonfinite selected rank remain excluded; legacy adjusted-p-finite rows keep their supported eligibility. The ORA tested universe, foregrounds, identifier bridge, ambiguity policy and direction-conflict collapse remain isolated. Full eligible mapped aliases use the established median reducer, while an ORA conflict cannot re-enter through a rank-only alias. Fallback KEGG routes reduce all eligible source scores once after the final GeneID bridge instead of taking a median of already-collapsed aliases; this can change already-ranked alias groups as well as restore adjusted-p-missing genes. Tables lacking raw-p evidence retain legacy eligibility. Synthetic offline R regressions cover exact ranks and ORA sets across all routes, metric fallback, empty ORA, duplicate and nonfinite accounting, alias conflicts and helper dependency tracking; no live enrichment service, real study or published benchmark was rerun.
- **Count-matrix validation is strict and shared by the interface and copied workflow (*scientific*).** Missing, nonnumeric, nonfinite and negative cells are rejected before rounding or output creation, so `-0.1` cannot become zero and malformed cells cannot become counts. Any fractional value now requires the explicit estimated-count declaration; admitted RSEM/tximport estimates use the existing round-half-to-even convention, while integer text is preserved without conversion through binary floating point. At least half of the column totals falling within 1% of one million produces a normalized-data suspicion warning, not a categorical TPM rejection, because valid integer libraries can have the same total. The copied workflow treats its ingest entrypoint and validator as inputs, so a change to either re-runs the import rather than reusing stale counts. Entry-point regressions cover text, blank/NA, NaN/infinity, a single fraction among integers, a small negative, exact million-count totals, large integers and an independent decimal rounding oracle. These are R-free synthetic checks, not a reproduction of published benchmarks.
- **Microarray probe annotation is ambiguity-aware before MaxMean collapse (*scientific*).** Ordinary symbol fields are parsed across all `//` and `///` candidates. Affymetrix `gene_assignment` is parsed as `///`-separated records whose second `//`-separated field is the symbol, so transcript, description and other fields are not mistaken for genes. Repeated annotation rows and repeated transcripts are combined by probe and deduplicated; exactly one distinct gene is retained, while ambiguous, unknown and missing-annotation probes are excluded. `probe_gene_map.tsv` records the raw annotations, complete sorted candidates, classification and annotation-row count with escaped control characters, and check 12 reports each exclusion count. Local gene-level matrices retain direct identifiers. Synthetic R regressions cover order invariance, repeated same-gene assignments, unresolved mappings, evidence escaping, mapping coverage and an independent expected MaxMean matrix. No GEO study or published benchmark was rerun.
- **Cross-study enrichment uses the main enrichment route's ambiguity-aware identifier resolver (*scientific*).** Official-identifier routing, configured keytype handling and alias fallback retain the established main-analysis policy. Duplicate copies of one source-to-Entrez pair collapse harmlessly; unresolved one-to-many and cross-keytype mappings are excluded from both the shared universe and every study/convergent foreground instead of inheriting database row order. `meta_enrichment_mapping.tsv` records every accepted, unmapped and ambiguous source identifier, its candidates, resolution route and foreground memberships. Check 18 reports universe and foreground coverage and remains `REVIEW_REQUIRED` when ambiguity was excluded, including when enrichment later skips for small gene sets or no terms. Synthetic offline regressions establish order invariance and exact set membership; no real study, enrichment service, benchmark or published result was rerun.
- **Project destinations are checked before scaffolding.** Project names `.` and `..`, resolved paths outside the selected working directory, and symlink or junction targets that escape it are refused. A non-empty existing directory now requires explicit overwrite whether or not it is already a BulkSeq Studio project; an existing file is never overwritten. An empty directory remains a valid new target. The command line and interface distinguish an occupied non-project directory from an existing project, so their messages no longer assert that arbitrary files are a recognized project.
- **Workflow synchronization is verified and fail-closed.** Before replacing an outdated or legacy copied workflow, the application stages a complete copy, verifies its content digest, and promotes it with rollback if promotion fails. A valid recorded digest that disagrees with the project workflow now blocks the run without overwriting local files. A copy without a valid digest is repaired only when its actual tree matches the trusted bundled tree; otherwise it stops for review. A valid legacy recorded workflow is upgraded with the original tree retained in a project-local backup. The command line and interface stop before creating a runner when synchronization fails, and the run report distinguishes the actual execution-tree digest from the bundled-workflow identity. Re-run only after reviewing any reported local workflow edits.
- **Command-line sample-sheet checks follow the configured input route.** `bulkseq project info`, `samples show`, and `check` now read `input.samples`, including project-relative and absolute paths, instead of silently using `config/samples.tsv`. `check` permits pending reads only on SRA and processed-input routes; local FASTQ paths remain required. Relative FASTQ cells are resolved against the selected project even when the command is invoked from another directory. `samples show` and `check` name a missing or malformed configured sheet; `project info` retains its established zero-sample fallback for an unreadable sheet.

### Installation

The Windows installer and portable application are unsigned. Verify downloads against `SHA256SUMS.txt`; Windows SmartScreen may display a publisher warning before launch.

## 0.31.0 — 2026-09-14

> **Scientific output changes.** The three alternative differential-expression engines (edgeR quasi-likelihood, limma-voom, microarray limma) gain result columns and a covariate screen they did not have, three sanity checks now report findings on designs and routes they previously passed over in silence, the covariate screen stops testing the sample sheet's descriptive identifier columns, and the wording of checks 21 and 23 and of the count-model diagnostic placeholders changed. Re-run any result you intend to publish, and see the items marked *scientific* below.

Minor release: engine parity and honest reporting. Everything an alternative engine can produce, it now produces; the report, run summary and pre-run checks state what the run actually did instead of describing the DESeq2 route by default. The DESeq2 route's numbers are unchanged, verified byte for byte on the bundled pasilla benchmark and on two *Fusarium graminearum* studies (evidence below). The workflow version is bumped to 0.31.0, so an existing project re-copies the bundled workflow on its next run; that copy is how the new shared `workflow/scripts/de_common.R`, which every engine script now sources at start-up, reaches an existing project.

### Changed

- **edgeR-QLF and limma-voom write the `ncbi_geneid` annotation column (*scientific*).** `run_edger.R` and `run_voom.R` now take the NCBI GeneID from the annotation's `db_xref` field through the same shared `annotate_from_gtf` helper `run_deseq2.R` uses, at the same position in the results table. KEGG enrichment reads that column when an organism's KEGG gene list is keyed by bare NCBI GeneIDs and no OrgDb is available — *S. pombe* is the catalogued case — so KEGG over-representation and GSEA on those two engines are no longer empty by construction for such an organism. The wiring is verified; the realized row counts are not, because no *S. pombe* run was performed on an alternative engine for this release and the Ensembl *Drosophila* annotation the pasilla benchmark uses carries no `db_xref` GeneID (the column is written and empty on all 7,481 rows there). The microarray limma route has no annotation file and does not write the column.
- **The alternative engines carry the effect-size companion column `padj_lfc_ge_threshold` (*scientific*).** edgeR computes it with `glmTreat(..., null = "interval")` and limma-voom and the microarray route with `treat()` on the contrast fit, each at the same threshold rule DESeq2 uses (the configured log2 fold-change threshold when positive, otherwise 1.0, because the test needs a positive value; a non-positive value reaching the test itself now stops the run with a named message instead of being tested silently). The column is a companion only: it never enters the up/down split, which a run-level recomputation on all four engines confirms. The method that produced it is recorded per engine as a `Fold-change threshold test: <method> (H0 |log2FC| <= <L>)` line in `results/reports/sessionInfo.txt`, in the run summary and in `deseq2_objects.rds`. Measured: on pasilla, 893 genes at padj < 0.05 against 59 by the companion test on edgeR and 938 against 37 on limma-voom; on the microarray benchmark, 493 against 17.
- **The covariate-structure screen runs on every locally fitted engine (*scientific*).** edgeR-QLF, limma-voom and microarray limma now write `results/deseq2/pca_coordinates.csv` — `prcomp` on the 500 highest-variance complete rows of that engine's own log matrix, `plotPCA`'s convention, reproducing `DESeq2::plotPCA` to a relative 4.8e-14 — and check 23 is gated only against the imported-results route, which fits no model. The DESeq2 path still uses `plotPCA(vsd)` unchanged. Because each engine screens its own coordinates, the screen is engine-relative and advisory: on the bundled pasilla benchmark the `replicate` column reports `REVIEW_REQUIRED` under edgeR and limma-voom (adjusted R² 0.92, F(1,2) = 36.76, p = 0.0261, against the chance ceiling of 0.85 at n = 4, k = 2) while DESeq2 stays `PASS` on the same samples (adjusted R² 0.54, p = 0.168). Both readings are correct — that subset really is replicate-paired, and the variance-stabilising transform reranks the top-variance rows — but two of the four engines now raise a review finding on the flagship benchmark.
- **Check 23's decision rule is sample-size-aware (*scientific*).** The fixed adjusted-R² floor of 0.5 is replaced by the value only 5% of random label assignments reach for that sample and group count, derived analytically from the same F distribution as the p-value (0.85 at n = 4, 0.57 at n = 6, 0.42 at n = 8, 0.26 at n = 12, 0.17 at n = 18, for two groups). Because that ceiling is derived from the same F distribution as the p-value, exceeding it is algebraically the same event as p < 0.05, and the two conditions agreed on every one of 40,000 random sample-size, group-count and F-statistic draws; the practical effect is therefore that the independent effect-size requirement is removed rather than rescaled, leaving the nominal 5% F-test, and the conjunction survives only as a guard for a principal component with no variance, where the p-value degenerates. The fixed floor was the stricter of the two rules from eight samples upward in two groups, so a column explaining 35% of a principal component across eighteen samples (p = 0.0097) was reported as within chance and is now review-required, as is a column explaining 45% across twelve (p = 0.0169). Neither release was silent about such a column: 0.30.1 named it in a `PASS` line with its statistics, and 0.31.0 returns a different verdict on the same column rather than mentioning one it previously ignored. Measured per-axis false-positive rate under permuted labels: 0.0484 (484 of 10,000). The screen scores each column on whichever of PC1 and PC2 fits it better and tests that maximum, so the screen as a whole is not calibrated at 5% (measured 0.095 at n = 18, k = 2); that multiplicity is unchanged from 0.30.1 and the message states that the screen is advisory.
- **The covariate screen no longer tests the sheet's descriptive identifier columns (*scientific*).** Check 23 screens every sample-sheet column outside the design formula, skipping an exclusion list and any column that has one level or as many levels as there are samples. Two free-text labels sat outside that list: `sample_title`, which the accession importer writes from the archive's own record, and `title`, which the series importer writes from the repository entry. A project imported either way could therefore have its sample titles tested against the principal components and reported for review. That happens when the titles partition the samples the same way the contrast does, which is common because titles are usually written to name the group. The interface's design helper already refused to offer that column as a covariate, so the two disagreed. The set of descriptive columns is now declared once and both sides read it, with a test that parses each source and fails when they diverge. A project whose sheet carries either label can therefore lose a check 23 finding it previously reported; no other check changes, and a genuine covariate such as `batch`, `library_prep` or `sequencing_run` is screened exactly as before.
- **A DESeq2 design carrying an interaction or nesting operator is reported for review (*scientific*).** Check 08 now adds a `REVIEW_REQUIRED` message naming the coefficient that was actually tested and stating that it is the contrast at the reference level of the other design factors, not the interaction effect, and advising an explicit contrast or coefficient. The fit is not refused and additive designs are untouched — pasilla's `~ condition` check 08 is byte-identical. The three alternative engines continue to refuse such a formula outright, as they have since 0.29.1.
- **Check 10 reports when the KEGG identifier form cannot be used (*scientific*).** It escalates to `REVIEW_REQUIRED` when the key form stays `unknown` for an organism whose gene ids are mostly non-numeric, and when a GeneID-keyed organism is reached from a route that carries no usable `ncbi_geneid` values, naming the engine or input route and the number of genes that could not be bridged.
- **limma-voom reports a moderated standard error (*scientific*).** `lfcSE` was `NA` on that route and is now derived as the microarray route derives it, through one shared helper (`stdev.unscaled × sqrt(s2.post)`, with the same finite-and-positive validation): finite and positive on all 7,481 pasilla rows, where 0.30.1 wrote `NA` on all of them. The genes-of-interest forest plot draws its error bars from that column, so its intervals change from zero-width to real ones on limma-voom runs. edgeR keeps `NA` with the reason recorded: the quasi-likelihood fit reports no per-gene log-fold-change standard error.
- **Produced text changed in three places.** The count-model diagnostic placeholders (dispersion, Cook's distance, library size) name the engine that ran, so an edgeR run's figures read `edgeR-QLF logCPM backend` instead of the `limma-voom logCPM backend` they wrongly carried; check 21 and check 23 messages lead with the finding and the action, with sample, column and study identifiers moved to a trailing clause; and check 00 now carries a `WARNING` when more than one contrast is configured, naming the contrast analysed and those preserved but not run.
- **The KEGG identifier form comes from the organism catalogue, with the live probe as the fallback.** All 45 catalogued KEGG organism codes were resolved live on 2026-09-13 and now carry a measured `kegg_key_form` (20 keyed by NCBI GeneID, 25 by locus tag), forwarded to the enrichment step as a rule parameter, so a catalogued organism needs no network round trip. A code outside the catalogue is still probed live, now through a four-step fallback chain (`link/<org>/pathway`, `list/<org>`, `conv/<org>/ncbi-geneid`, `info/<org>`) and classified by a majority rule over the whole key list instead of by requiring unanimity across the first fifty keys; a total outage degrades to `unknown` instead of aborting. The `KEGG key form observed:` evidence line now also names the source, the endpoint that answered and the measured numeric-key fraction. An opt-in network test (`BULKSEQ_KEGG_LIVE=1`) resolves every catalogued code against the two endpoints `download_KEGG` uses and re-derives the form independently, so a re-keyed organism is caught rather than assumed.
- **The environment drops six R packages no workflow script loads.** `r-ggtext`, `r-ggraph`, `r-tidygraph`, `bioconductor-complexheatmap`, `bioconductor-enhancedvolcano` and `bioconductor-biomart` are removed from the full specification and the lock, which a new reverse dependency gate found by scanning every `library`/`require`/`::` reference in `workflow/scripts/*.R` and comparing it with the declared set; the gate was watched failing and naming exactly those six before they were removed. A linux-64 dry solve goes from 703 to 697 packages and re-adds none of them, a clean environment built from the trimmed lock loads all 49 probed namespaces, and a full pasilla run on it reproduced the pinned counts body hash and a byte-identical DESeq2 table with byte-identical protein-network figures. An existing installation is unaffected until it is rebuilt. The protein-network comment that described a `ggraph` figure was corrected to name what the script actually does, and `fastp` and `sortmerna` gained pin-rationale comments next to the `r-base` one.
- **Application dependency pins.** PySide6 6.11.1 → 6.11.2, pandas 3.0.3 → 3.0.5 (3.0.4 is yanked on PyPI, recorded reason "Reported segfaults with datetime-related functionality"), pydantic 2.13.4 → 2.13.5, PyInstaller 6.21.0 → 6.22.2; numpy stays at 2.4.6, inside the range pandas 3.0.5 declares for this interpreter. Pillow is removed from the build requirements: no tracked source imports it, the application's only openpyxl consumer reads cells through `pandas.read_excel`, and both a workbook with an embedded image and a plain one read correctly with Pillow uninstalled. The rebuilt Windows bundle carries no `PIL` and its frozen self-test passes.
- **The graphical interface reports the run it is describing.** The Outputs picker no longer offers `unchanged_genes.csv` on either alternative engine, gated on a declared alternative-engine set rather than on a single hardcoded engine name (edgeR previously listed a table the workflow never produces on that route), and the engine tooltip is written from a declared mapping stating what each engine's effect-size test is and which artefacts it produces. The Pre-run checks page shows each check under a plain-language name with its recorded findings underneath instead of a bare identifier and status, re-reads the phase checks when a run finishes, and the completion message counts the checks that reported a warning or a review-required finding. The window's status bar carries a permanent version label with an accessible name, and the inset status bar now measures its message area against the leftmost laid-out child instead of a fixed reserve, so the message cannot be painted under it.

The artefact-by-engine matrix below is the state this release declares. `tests/test_engine_parity.py` derives the same table from the engine scripts and rule blocks and fails on any disagreement in either direction, including against this table.

| Artefact | DESeq2 | edgeR-QLF | limma-voom | limma | imported |
| --- | --- | --- | --- | --- | --- |
| `ncbi_geneid` | yes | yes | yes | no | no |
| `pca_coordinates` | yes | yes | yes | yes | no |
| `check_23_covariate_structure` | yes | yes | yes | yes | no |
| `padj_lfc_ge_threshold` | yes | yes | yes | yes | no |
| `lfc_companion_threshold_rule` | yes | yes | yes | yes | no |
| `companion_column_outside_the_up_down_split` | yes | yes | yes | yes | yes |
| `lfc_threshold_test` | greaterAbs | glmTreat | treat | treat | — |
| `unchanged_genes` | yes | no | no | no | no |
| `check_13_equivalence` | yes | no | no | no | no |
| `lfcSE_populated` | yes | no | yes | yes | yes |
| `stat_column` | yes | yes | yes | yes | yes |
| `check_14_wilcoxon` | yes | yes | yes | yes | no |
| `interaction_handling` | review required | refuses | refuses | refuses | not applicable |
| `sources_de_common_before_library` | yes | yes | yes | yes | no |

Two cells need a word of qualification that a table cannot carry. `lfcSE_populated` on the imported route means the column is preserved when the uploaded table supplies one: the importer accepts `lfcSE`, `lfcse` or `se` and leaves the column empty when the table has none, because an imported result is only ever as complete as the file it came from. `check_23_covariate_structure` means the screen runs, not that it always assesses something: a project whose sample sheet carries no column outside the design formula gets a `PASS` that says so explicitly, which is the pre-existing behaviour on every route.

### Added

- **An optional `library_name` column on the sample sheet.** The sample table carries a human-readable library name directly after `sample_id`, editable by hand and filled from the archive's own `library_name` field when a study is imported by accession. It is optional, may be blank and may repeat across rows: `sample_id` remains the only identifier the workflow keys on, for file names, counts-matrix columns and every sample match, and nothing derives a path or a column name from the library name. The pipeline uses it where a human-readable name helps and a duplicate cannot do harm: the run summary, the study-design export and the report show it beside the sample identifier rather than instead of it, and the per-sample figure labels use it, appending the sample identifier only for the rows whose name repeats, so no plot carries two identical point labels. A project whose sheet has no such column is unaffected, and its sheet is not rewritten to add an empty one, which matters because the sheet is an input to most of the workflow and a changed timestamp would force a resumed run to rebuild. The column is descriptive, so the covariate screen does not test it.
- **The documentation site is rebuilt and expanded.** The published handbook at https://tunabirgun.github.io/bulkseq-studio/ is replaced: six pages become nineteen, grouped as Start here, Project and data, Analysis setup, Validate and run, Explore results and Reference, so the manual's four middle groups carry the same names as the application's four-stage navigator and a reader looking at a stage in the window can find the matching chapter. New pages cover the five input routes, the sample sheet and reference, the experimental design, the pre-run checks, enrichment, meta-analysis, the microarray route, imported results, compute resources and a sixteen-term glossary whose terms are clickable wherever they appear in the text; a walkthrough page puts the decisions in order for each of the five starting points. Every page states the version it documents, and the version notices name the output changes from 0.26.6 onward so a reader can tell which release produced a given result. The site is generated rather than hand-edited: `docs_src/content.mjs` holds the content and `node docs_src/build.mjs` writes `docs/`, which a continuous-integration step rebuilds and compares, so a hand edit under `docs/` fails the build. The 0.30.1 site's benchmark figures are not carried over, and its ten interface screenshots are replaced by two retaken at 0.31.0. Those two are now derived rather than hand-supplied: `scripts/capture_docs_screenshots.py` renders them from the real interface against a synthetic project, at the size the site embeds, and a test holds the script and the site to each other so an image the handbook embeds cannot lack a recipe and a recipe cannot produce an image nothing uses. The README is cut to a front page and now defers to the handbook rather than restating it: the four per-release "what changed" sections and the accumulated output-change notices are gone, since the changelog and the site's version notices already carry them, and the long safeguard prose is reduced to five lines that link into the chapters that explain them. It gains a badge row whose release badge resolves the published version from the release list, and whose Python, Snakemake and licence badges are held to `pyproject.toml` and the environment lock by a test, so a bump that updates one and forgets the other fails.
- **The results report is redesigned.** The self-contained report gains a section index, readable typography, viewport-bounded definitions, and a figure viewer with Fit, Actual size, zoom percentage and keyboard controls. Sorting and zoom affect presentation only; analysis values and embedded source figures are preserved, and the explanations distinguish adjusted p-values and diagnostic patterns from probabilities of biological truth.
- **A run-provenance table in the report.** The report's versions section now carries App version, Workflow version, Workflow digest, Workflow copied, Project created with (only when it differs from the executed stamps), Installed environment spec, Environment lock md5 and Workflow commit, using the run summary's own wording when a value was not recorded; the muted lock-hash paragraph it replaces is gone. A non-uniform strandedness record renders as `mixed` with the per-sample list on both surfaces, from one shared renderer, so the report can no longer describe a mixed run as uniform.
- **Mapping evidence reaches the run summary.** `run_summary.txt`, `run_summary.json` and `tools_references.txt` now carry the KEGG retrieval date and tool versions, the observed KEGG key form and the GO readable-symbol conversion line, from one shared list of evidence prefixes that a drift test holds against `run_enrichment.R`.
- **Single-contrast disclosure on every non-interface surface.** A project configuring more than one contrast now gets a parse-time note on stderr, a `WARNING` in check 00, an explicit `Contrast analysed` and `Configured but not analysed` pair in `run_summary.txt` and `study_design.txt`, and a card in the report. A single-contrast project is unchanged on all four surfaces.
- **Two *Fusarium graminearum* benchmarks.** `fg_spores_mycelium_paired` (GSE55477 / PRJNA239711 / SRP039087, six paired 90 bp samples, unstranded, spores versus mycelium; dataset publication Zhao et al. 2014, *BMC Genomics* 15:191, doi:10.1186/1471-2164-15-191) and `fg_heat_shock_paired` (GSE78885 / PRJNA314297 / SRP071140, six paired 151 bp samples, reverse-stranded, 37 °C versus 25 °C; Bui et al. 2016, *Scientific Reports* 6:28154, doi:10.1038/srep28154) join the benchmark catalogue with every run accession, URL, mate checksum, read and base count generated from the ENA filereport and re-verified against a fresh query (120 values across 12 runs, no differences). Both scaffold from the interface and the command line onto the *F. graminearum* PH-1 reference preset, and example project scaffolds ship under `examples/benchmarks/`.
- **A declared artefact-by-engine matrix as a test.** `tests/test_engine_parity.py` reads the engine scripts, the differential-expression rule blocks and the check gates and compares them with the declared table above; it also parses the table out of this changelog entry, so a published matrix that drifts from the implemented one fails too. Adding an artefact to one engine without declaring it, and declaring a cell the sources do not support, both fail. Ten negative controls cover those directions, including a companion column wired into the significance call, a diverging threshold-fallback rule and a mutated changelog cell.
- **One capability probe for the tests that need bash or Rscript.** Six divergent probes are replaced by `tests/_runtime.py`, which resolves a runtime only once it has proved it can load the packages that test needs; the skip lists before and after are identical on Windows and under WSL. Two defects it exposed are fixed: an argument with no space was relayed into a shell as a syntax error, and an unreadable candidate path raised instead of being skipped. A separate controlled re-count test flips one sample's strandedness on a completed project, asserts the counts and the DESeq2 table move and check 21 names the sample, and asserts the uniform table returns both hashes exactly.

### Fixed

- **Results report: long run-provenance values (for example the "not recorded (workflow copied before 0.30.1)" disclosure) wrap inside the Software and provenance table instead of being clipped at the column edge.**
- **Results report: runtime phase labels are no longer truncated with an ellipsis on narrow screens.**
- **Results report: the enlarged-figure viewer keeps the caption's question, method note and how-to-read text as separate sentences instead of running them together.**
- **`bulkseq run` reacts to Ctrl-C during a quiet phase.** Output is drained on a reader thread and the consumer polls it, so an interrupt is seen immediately instead of waiting for the next line of Snakemake output; it still stops the whole process tree, and the queue is drained to exhaustion after the process exits so a masked failure marker printed at the end is still detected. Measured: interrupt to process-tree termination in 0.36 s during a two-second silent phase, against no response within 20 s before.
- **Check Environment names the phase it is running** — the WSL distribution, the bioinformatics tools, the R and Bioconductor load test — instead of one static line, and the allowance for the R load test is derived from the number of packages probed (128 s for the current 49) rather than fixed at 120 s.
- **The GUI benchmark harness moved out of `installer_output/`.** `run_gui_full.py` and `run_gui_preflight.py` are a normal test package at `tests/gui_benchmark/`, so nothing tracked in git remains under `installer_output/` and the release upload steps no longer need the exclusion lines 0.30.1 added; a test asserts both that the upload paths carry no exclusion and that `git ls-files installer_output` is empty. Harness run evidence is written to the ignored `build/gui-benchmark-runs/`.
- **The sample sheet no longer changes in every byte when it is saved on a different platform.** The sheet was serialised with the host's own line ending, so one written by the interface on Windows and saved again on Linux differed everywhere, even with nothing edited. The saver only skips writing when the bytes match, so that difference moved the file's timestamp, and the sheet is an input to most of the workflow: a resumed run rebuilt from alignment instead of continuing. The terminator is now read back from the sheet on disk and reused, so an existing project is stable on either platform and no one pays a one-off rewrite, and a newly scaffolded project is written with line feeds so its sheet does not depend on where it was created. The tests pin the behaviour to the file rather than the host, so a return to the old behaviour fails on one platform or the other.
- **The Outputs picker lists the count matrix on the mixed input route.** A project combining local FASTQ with downloaded accessions aligns reads and the workflow writes `results/counts/counts.txt`, but the picker offered that file only on the plain FASTQ and accession routes, so the table existed and could not be opened. The three copies of the alignment-route list in the interface are replaced by one named constant, and the picker's own comment, which described the file as meaningless for a count-matrix run when the workflow does write it there, is corrected: the count-matrix route is still excluded, now deliberately and on the stated ground that the file is the user's own uploaded matrix copied to the canonical path. A test derives the routes that produce the file from the workflow guard itself and asserts the picker offers exactly those minus that one documented exclusion, so neither half can drift unnoticed.
- **The R regression for the KEGG GeneID bridge runs again.** It read a function from `run_deseq2.R` that now lives in `de_common.R`, and nothing executed the file; it is now driven by pytest through the shared runtime probe. That probe also finds the pipeline's own Rscript on Linux, so eight R regressions that silently skipped under WSL now execute there.

### Continuous integration and release

- Every pytest job runs with `-rs` and publishes the skipped tests and their reasons as a table in the run summary, so a test that stops running is visible instead of silent.
- The test matrix gains macOS (interface, configuration and validation layer only; the pipeline does not run there). The release script gates on the Tests workflow, so macOS is now a release-blocking platform. Its first run earned its place by failing three ways that no local run could reproduce: a test executed the pipeline's read-length rule on a host whose `zcat` does not read gzip, and now probes that capability instead of assuming it; a negative control read the previous release's renderer out of git history, which a shallow checkout does not have, so the job now fetches full history and the control skips with a stated reason where it cannot run; and two test modules treated the presence of `wsl.exe` as a working runtime, when Windows ships it with no distribution installed, so both now use the shared probe that requires a distribution to have actually run a command. Because that same mistake escaped twice, it is now a gate rather than a habit: a test parses every test module and fails on a `shutil.which("wsl")` availability check or a subprocess call whose command begins with `wsl`, naming the file, so a ninth ad hoc probe cannot reach a runner before it is caught.
- Both packaging jobs verify the installed dependency set before building: `pip check`, then a comparison of the frozen environment against the exact pins in `constraints.txt` and `requirements-build.txt`, failing on a mismatch and on finding no pins at all.
- `scripts/release.ps1` downloads both artifact sets from the gated build run instead of trusting a local build, and reports the age and conclusion of the last Environment workflow run as a warning without gating on it. It now publishes this version's own changelog entry as the release body, read from `CHANGELOG.md` between this version's heading and the next, so the page people download from states what changed and carries the scientific-output notice instead of pointing at a file they would have to go and find. A missing or empty entry stops the release rather than publishing a bare page, and re-publishing an existing release refreshes the notes as well as the assets.
- The Environment workflow gains an osx-arm64 dry-solve job: the specifications filtered to what Apple Silicon can install must solve, and the unfiltered ones must still fail naming each unavailable package, so a package becoming available is noticed.
- Structural gates were added for the micromamba bootstrap pins in the setup script (one version, two distinct digests, a URL that interpolates the version) and for the build requirements' pin form, each watched failing under mutation.

### Evidence

- The DESeq2 route is unchanged on the bundled pasilla benchmark: every differential-expression table, both significant-gene lists, the equivalence table, the normalized counts, the PCA coordinates and all 54 figures are byte-identical to the pinned pasilla baseline the earlier releases were measured against (`deseq2_results.csv` sha256 `2b040052…`, 7,532 rows, 467 genes at padj < 0.05; counts body sha256 `f68d2786…`). The artefacts that differ are check 23's reworded message, which reports `PASS` before and after; the report and summary files, which carry the new provenance and evidence lines; and `deseq2_objects.rds`, which now records the threshold-test method.
- *Fusarium graminearum* SRP039087, run from FASTQ under a v0.29.1 workflow copy and under the 0.31.0 candidate, produced identical counts (body sha256 `0e6cf4f5…`) and an identical differential-expression table on every shared column: 9,028 rows, 5,734 genes at padj < 0.05, of which 2,723 up- and 2,478 down-regulated at |log2FC| > 1; all six samples inferred unstranded. The reverse-stranded SRP071140 pair is likewise identical: counts body sha256 `5eeca15e…`, 8,280 rows, 5,836 genes at padj < 0.05, 1,996 up and 1,255 down at |log2FC| > 1, all six samples inferred reverse-stranded. The candidate tables additionally carry `ncbi_geneid` and `padj_lfc_ge_threshold`. "Counts body" above names a stated normalisation rather than the raw file, because the count matrix carries a header comment that records the per-sample strandedness the run inferred, which differs between releases by design: it is `tail -n +3 results/counts/counts.txt | cut -f1,7- | sha256sum`, which drops the comment line, the column header and the coordinate columns and hashes the gene identifiers with the per-sample counts. A digest published without its recipe is not reproducible, and an earlier release of this project retired one for exactly that reason.
- Nothing in this release was executed on macOS. Every R, Snakemake and packaging measurement comes from Windows and from WSL2 Ubuntu 24.04; the macOS test job and the osx-arm64 solve job have not yet run on a runner.

## 0.30.1 — 2026-09-11

Patch release: run-provenance, CLI, and CI/build-hygiene fixes from the post-0.30.0 review. No produced number changes; the workflow version is bumped because a workflow script changed, so an existing project re-copies the workflow on its next run.

### Fixed

- **The run summary and tools-references report the workflow version that actually executed.** `run_summary.json`, `run_summary.txt`, and `tools_references.txt` now read the executed workflow version from `workflow/workflow_metadata.yaml` instead of the project's creation-time stamp; when the app re-syncs an outdated project's workflow copy, the reported version now reflects the copy that actually ran instead of the version the project was created under. The app now also records its own version in `workflow_metadata.yaml` when it copies the workflow, so the reported app version is the one that executed; a workflow copy made before 0.30.1 carries no app version and the reports say `not recorded` instead of repeating the creation stamp. The project's original creation stamps are kept as separate `project_created_app_version` and `project_created_workflow_version` fields rather than being overwritten.
- **`bulkseq run` re-syncs an outdated project workflow copy before running,** matching the interface, instead of running against a stale copy left behind by an earlier app version.
- **The theme-toggle timing test no longer counts the cold first paint against its budget.** Only the warm toggles are held to the 500 ms ceiling (the warm median and 95th-percentile bounds are unchanged), and a negative control forces every toggle onto the cold path and asserts the gate fails.
- **Release packages no longer include the GUI benchmark harness.** The two harness scripts under `installer_output/gui-benchmark-runs/` are tracked in git although the rest of `installer_output/` is ignored, so a fresh CI checkout placed them where the `installer_output/*` upload glob swept them into the Windows and Linux release artifacts; both upload steps now exclude that directory, and a test guards the workflow file.

## 0.30.0 — 2026-09-10

> **Scientific output changes.** *S. pombe* KEGG enrichment and mixed-library-type strandedness now differ from 0.29.1 for the same input. Re-run any result you intend to publish, and see the items marked *scientific* below.

### Changed

- **KEGG enrichment reaches *S. pombe* through a measured GeneID bridge (*scientific*).** Before a raw-locus-tag KEGG query, the workflow probes the organism's live KEGG gene list; when the keys are observed to be bare NCBI GeneIDs (recorded as `kegg_key_form_observed` in the check 10 evidence), locus-tag gene ids (`SPOM_...`) are bridged to NCBI GeneIDs through the GTF's `db_xref` field before the query. Previously 0 of 50 test genes mapped for *S. pombe*; measured after the fix, 18 of 50 map against a universe of 2,061, so every earlier *S. pombe* KEGG ORA/GSEA table was empty. Organisms whose KEGG keys are observed to be locus tags (*Fusarium graminearum*, *Candida albicans*, and others) are unchanged, and a failed probe (no network) applies no bridge and records the key form as `unknown` in the evidence. The limma-voom and edgeR routes do not yet carry the GeneID column and remain unmapped for *S. pombe*.
- **Strandedness is inferred and applied per sample (*scientific*).** STAR infers each sample's library type from its own `ReadsPerGene` table and HISAT2 from a per-BAM featureCounts pass; featureCounts then runs once per BAM with that sample's own `-s`, and the STAR gene-counts quantifier selects each sample's own column from its `ReadsPerGene` table. The merged `counts.txt` header records every sample's `-s`, and `run_summary.json` records the realized per-sample codes and whether they were `uniform`. The per-sample calls are written to `results/aligned/strandedness_per_sample.tsv` (sample, code, reverse-strand ratio). A run where every sample shares one library type produces identical counts to 0.29.1 (verified on pasilla); a run mixing library types, such as a multi-study meta-analysis pooling studies prepared differently, now counts each sample with its own orientation instead of one orientation for all. `sjdbOverhang` is now derived from the maximum read length across every sample rather than the first sample's. Check 21, previously run only on multi-study projects, now runs on every STAR and HISAT2 project and reports `REVIEW_REQUIRED`, naming the samples and ratios, when samples within one study disagree on the inferred code. Because the read-length and STAR index-build rules now read every sample's FASTQ, an existing STAR project's next run re-derives the read length, rebuilds the index, and re-aligns; the HISAT2 index rule is unchanged, so an existing HISAT2 project re-aligns (its alignment rule now declares the summary output and index prefix) without an index rebuild.

### Added

- **Check 23 (covariate structure).** For DESeq2-engine routes, a one-way ANOVA of PC1 and PC2 against every sample-sheet column outside the design formula (file, path, and download-metadata columns excluded, numeric columns treated as categorical); a column is `REVIEW_REQUIRED` when its adjusted R² exceeds 0.5 and its F-test p-value is below 0.05, and a column whose grouping exactly coincides with the contrast factor is reported `REVIEW_REQUIRED` as aliased with it instead of being scored. Single-level and all-distinct columns are skipped. PCA coordinates are written to `results/deseq2/pca_coordinates.csv`.
- **Check 06 covers the HISAT2 route.** The uniquely-aligned-fraction check now reads HISAT2's own alignment summary on that route, at the same 70%/50% warning/review-required thresholds already used for STAR.
- **Two DESeq2 table columns.** `padj_lfc_ge_threshold` is the adjusted p-value of the `greaterAbs` test at the configured log2 fold-change threshold, or at 1.0 when the threshold is 0 (the test requires a positive value; the primary significance call is unchanged), and `ncbi_geneid` carries the NCBI GeneID from the GTF's `db_xref` field when present.
- **KEGG evidence records its retrieval date and tool versions.** The KEGG evidence block now states the UTC query date and the installed KEGGREST and clusterProfiler versions.
- **Prebuilt reference inputs.** Setting `reference.star_index`, `hisat2_index`, or `salmon_index` to an existing path skips the matching index build (`hisat2_index` is the hisat2-build prefix), and `reference.transcriptome_fasta` is used as the Salmon index input in place of the generated transcriptome; the gffread transcriptome step still runs against the genome and annotation because the transcript-to-gene map is derived from it and is not replaced. A configured but missing path is refused at validation, naming the setting. `reference.protein_fasta`, which no rule ever read, is removed from the model; a leftover value in an existing project's configuration now produces a warning instead of being silently accepted.
- **Run provenance records the installed environment spec.** `run_summary.json` carries `environment_spec` (the installed spec file, its SHA-256, and whether it came from the lock, the core profile, or a fallback solve; `unknown` for an environment installed before 0.30.0), alongside the unchanged `environment_lock_md5`, which still hashes the repository's own lock file regardless of what was actually installed. `run_summary.txt` and `tools_references.txt` print the installed environment spec next to the lock hash; the HTML results report still prints the lock hash alone.
- **`bulkseq run [--mode run|dry-run|resume] [--exec-profile ...]`.** Runs the Snakemake workflow to completion from the command line through the same command builder as the GUI and `print-command`, streaming output and exiting non-zero on a failure, including a `micromamba run` masked exit code the GUI already detects. `--mode resume` against a project left locked by a killed or crashed run unlocks it first, mirroring the GUI's resume flow, instead of Snakemake refusing to start.
- **CI coverage.** The Windows installer build now silently installs, self-tests, reinstalls over an update branch, and uninstalls the built package; the R test job runs the Bioconductor-dependent figure contracts against a cached micromamba layer pinned to the pipeline's own R/Bioconductor builds; a static job lints the WSL/native-Linux setup script (`bash -n`, shellcheck, and a compile of its embedded Python bootstrap heredoc); and a separate Environment workflow installs both the core and full profiles live on a push or pull request touching the script or environment specs, weekly, and on demand. These are CI checks, not clean-machine verification.
- **Pinned application dependencies.** `constraints.txt` pins the application's own Python dependencies and is applied in both the CI test job and the packaging build.
- **Check Environment probes every WSL tool in one call**, instead of one WSL invocation per tool.
- **KEGGREST is now an explicitly declared, load-tested R package.** The full environment spec names `bioconductor-keggrest`, and Check Environment, the setup script's R-stack probe, and project validation load-test it alongside the other Bioconductor packages, because the KEGG key-form probe and identity check call it directly; an environment whose KEGGREST does not load is now reported instead of failing inside the enrichment step.

### Fixed

- **The runtime estimate again skips the STAR index-build cost when a prebuilt index is configured.** 0.29.1 removed this short-circuit because `reference.star_index` short-circuited nothing at the time; now that the field actually skips the `star_index` rule, the estimator reads it again and the STAR index build no longer appears among the reported bottlenecks when the index is prebuilt.
- **A prebuilt Salmon index that does not match the annotation is caught before tximport silently under-counts genes.** tximport drops any `quant.sf` transcript absent from the transcript-to-gene map without erroring; the Salmon route now asserts every quantified transcript name is present in that map before importing, and stops, naming `salmon_index`/`transcriptome_fasta` and the first unmatched transcript names, when they disagree.
- **A prebuilt HISAT2 index is read from its own build prefix, not a hardcoded `genome`.** The HISAT2 alignment rules previously appended a literal `/genome` to the index directory; a prebuilt index built under any other prefix name was silently ignored. The prefix is now read from `reference.hisat2_index`.
- **Updating over an installed version keeps the new uninstaller.** The installer's update branch removed the previous version and waited only for its registry key to disappear; Inno's uninstaller deletes that key first and its own `unins000.exe` last, so the old uninstaller could finish after the new files were written and delete the new uninstaller (caught by the new installer CI step and reproduced locally). The update branch now waits until the previous uninstaller's files are gone before installing.
- **A failed micromamba download names its cause.** When the bootstrap download fails (no network, a blocked proxy), the setup script now stops with a one-line message naming the download URL and the error, instead of printing a Python traceback and then a message blaming a missing `python3`, `curl`, or `wget`; the exit code is non-zero as before.
- **The reference-integrity gate stays a required run target even when every index and the transcriptome are prebuilt.** With `star_index`, `hisat2_index`, `salmon_index`, and `transcriptome_fasta` all configured, no remaining build rule would require the gate; `final_targets()` now always includes it on the alignment route, so a mismatched genome/annotation pair is still caught.

## 0.29.1 — 2026-09-10

Patch release: guards, messages, interface and documentation corrections from the post-0.29.0 review. No produced number changes for a run that succeeded under 0.29.0; the workflow version is bumped so existing projects pick up the engine guard and the wider enrichment map.

### Fixed

- **limma-voom, edgeR and the microarray limma engine refuse a design formula with an interaction or nesting operator.** These engines fit an additive group-means design (`~ 0 + group + covariates`) rebuilt from the formula's variables, so a typed `~ genotype*treatment` was silently fitted without its interaction while the check message echoed the typed formula as full rank. The run now stops with a message naming the formula and the fitted design, and the full-rank message reports the design actually fitted. DESeq2 fits the typed formula as before.
- **The meta-analysis check states the per-study design.** Each study is fitted with the contrast factor only (`~ condition`); covariates in the design apply to the joint model, and the 17_meta_analysis_qc message now says so.
- **The contrast list says that only the first contrast is analysed.** Additional contrasts are preserved on save but not run.
- **A failed run shows its error.** The execution log expands on failure and scrolls to the first `Error in rule` (or WorkflowError / MissingOutputException) line of the run that just failed instead of leaving the hint inside a collapsed panel.
- **The interactive protein network reports pruning.** The viewer draws the most connected proteins (up to 300); when it prunes, the status line and the export status read "showing the N most connected of M proteins" while the tables and provenance keep the full network.
- **Accessible names contain their visible captions** (WCAG 2.5.3) for the eleven buttons that did not, a test now walks every button in the main window, and the sample table has an accessible name.
- **The runtime estimate no longer assumes a prebuilt STAR index.** The workflow always builds the index; the estimator branch that zeroed that step when `reference.star_index` was set is removed. A host whose runtime calibration was recorded with that field set will re-converge over its next runs.
- **Rat, chicken, pig and cow reach the OrgDb enrichment route on hand-edited configurations and the CLI.** The workflow's organism map lacked the four entries the catalogue declares; a test now derives the expected map from the catalogue.
- **The quantification check names a plausible cause per route.** A low assignment rate is attributed to the strandedness setting only on the featureCounts and STAR gene-count routes; Salmon runs with automatic library detection, so a low rate there points at a reference or annotation mismatch, and count-matrix imports have no unassigned category.
- **The release script requires green CI before it tags.** It reads the Tests and Build packages runs for the exact commit and refuses unless both completed successfully.

### Changed

- **Documentation.** The site landing page carries a notice for the 0.29.0 output changes, and a docs gate now requires such a notice for any changelog entry marked scientific; the archive section states which deposited output classes predate 0.29.0; the README qualifies the meta-analysis p-value statement (finite up to a combined |Z| of about 38; per-study p-values at the double-precision floor still saturate it); the analysis page states the built-in over-representation universe (tested genes), the STRING version default and where the realized version and query date are printed, the two-level contrast rule with the interaction-formula refusal, and the per-study meta-analysis design; the outputs page documents the mapping (06) and assignment (07) thresholds and the network pruning rule; the FAQ's S. pombe KEGG explanation states the catalogue's key form and the open locus-tag question.

## 0.29.0 — 2026-09-08

> **Scientific output changes.** Several tables and figures differ from 0.28.0 for the same input: GSEA rankings, KEGG GSEA availability, STRING seed selection, meta-analysis combined p-values, and the genes-of-interest heatmap colour scale. Re-run any result you intend to publish, and see the items marked *scientific* below.

### Changed

- **GSEA ranks on the model statistic (*scientific*).** In-pipeline GO, KEGG, and custom GSEA now rank genes on the signed test statistic (DESeq2 Wald, limma or voom moderated t, edgeR signed root-F), the metric the exported preranked file already used, with log2 fold change only as a fallback when no finite statistic exists. The Entrez many-to-one collapse gates direction on the same column, and the enrichment summary states which column was ranked. Raw fold change had placed low-count, poorly estimated genes at both ends of the list.
- **STRING seeds are split by direction and chosen by significance (*scientific*).** The seed cap (400 by default) is divided between up- and down-regulated genes in adjusted-p order, with unused budget from the smaller direction passed to the larger one. Previously the cap took the first genes of an up-then-down list sorted by fold change, so any run with 400 or more up-regulated genes built its network and hub table from up-regulated genes only. Realized `seed_up_count` and `seed_down_count` are now recorded in the network provenance.
- **Meta-analysis combined p-values no longer underflow to zero (*scientific*).** The inverse-normal combination is computed in tail form, so a combined |Z| above about 8.3 yields a finite p-value instead of exactly 0. Meta-DEG calls are unchanged; the ordering of the most significant genes in the results table, forest plot, heatmap, and report is now by evidence rather than by tie order.
- **Genes-of-interest and enrichment-term heatmaps use a zero-anchored colour scale (*scientific*).** Row z-scores are clamped to the configured symmetric limit with the neutral colour at z = 0, matching the top-DEG heatmap; a single-gene list is now z-scored as the documentation states, and the per-gene jitter is seeded so the PNG and SVG agree.
- **MA and volcano plots show what the tables say (*scientific*).** The MA plot keeps every gene with a finite mean and fold change, including the independently filtered low-count cloud, and rings the significant ones; the volcano plots the unshrunken effect so its dashed fold-change guides, its colouring, and its ranked side key share one coordinate (the key now prints the same value as the up- and down-regulated tables); a comparison with no adjusted p-values writes placeholders instead of failing the figure step.
- **Report captions follow the realized assay.** limma-voom and edgeR runs are captioned as log2 CPM with an unshrunken fold change and an average-expression glossary, instead of the variance-stabilised-count wording of DESeq2 runs.
- **Every figure script honours the figure style.** The set-overlap, Wilcoxon, and GSVA figures now apply the per-group palette, font, size, and dpi overrides (the set-overlap dot plot previously ignored the palette because the scale was set on colour only), the GSVA heatmap's font follows the base font size, and the genes-of-interest figures use the configured width, height, and dpi (default width 6 in, previously 7 in); a style change is a rerun trigger for the GSVA rule.
- **Per-rule memory reservations follow the selected resource profile.** The Compute resources page now writes `rule_memory_gb` clamped to the configured pool, featureCounts, the differential-expression engines, trimming, FastQC, and MultiQC declare memory to the scheduler, and the resource keys match the rules that read them (index, strandedness-inference, and contamination-screen threads added; the unused `fasterq_dump` entries removed).
- **Protein-network seeding, provenance, and export are consistent across renderers.** Editing the genes-of-interest list now rebuilds a list-seeded network (the list is a rule input, not a parameter); the static figure and the interactive viewer resolve a duplicate symbol by the same rule (lowest adjusted p, ties keep the first table row); the component order that seeds each layout no longer depends on the collation locale; the configured and realized STRING score thresholds are recorded separately, and the score control's range is 1 to 1000 (a legacy project with 0 loads as the 400 the pipeline already ran). The report's network section now includes the top hub table its caption refers to, the Cytoscape node tables carry a `scope` column naming which enrichment object was exported, and a GSEA object is never exported under an over-representation name. The static-layout control and the static-figure label row were removed because the static figure always uses the deterministic shelf layout and carries no labels.
- **Enrichment identifier forms follow measured KEGG key forms.** The organism catalogue now records `kegg_keytype` separately from the AnnotationDbi keytype and forwards it to KEGG; the declared key forms for *S. pombe*, grape, cotton, *Z. tritici*, *P. falciparum*, and *C. albicans* were corrected after checking the KEGG gene identifiers each organism actually uses. g:Profiler GO tables are sorted by adjusted p before writing, the custom gene-set normaliser applies the same keytype-gated `LOC` rule as the built-in route, and the meta-analysis enrichment dot plot applies the project palette to fill as well as colour.
- **Per-rule resources and shell commands are derived and quoted.** HISAT2's sort buffer is computed from the rule's memory declaration and thread count; every user-supplied FASTQ path is shell-quoted in the trimming, QC, read-length, and STAR skip-trim commands; the Trimmomatic adapter lookup no longer dereferences an unset `MAMBA_ROOT_PREFIX` (native runs export it) and fails with a named message when no adapter file is found; `perl` is declared for the Salmon transcriptome step and guarded like `gffread`.
- **The rRNA reference database is pinned.** The SortMeRNA database archive and its extracted FASTA are verified against recorded SHA-256 digests and a sidecar records the realized digest; existing rRNA projects re-fetch the database once under verification on their next run.
- **Meta-analysis tables expose the effect-size test.** `rem_pvalue` is joined by a Benjamini-Hochberg `rem_padj` corrected within the concordant genes whose pooled effect could be estimated, in both `meta_analysis_results.csv` and `meta_convergent_genes.csv`; the per-study MA plots colour significant genes correctly again.
- **Interface housekeeping.** Advanced parameters are greyed by input mode (alignment settings on FASTQ routes only, DESeq2 settings off in microarray and imported-results modes); run-phase labels name the reference download; Regenerate figures also re-renders the GSVA heatmap and per-study meta figures; the enrichment-term picker reads the GO MF, GO CC, and custom gene-set tables and matches multi-symbol identifiers; the Check Environment summary states that it counts requirement groups; Stop install now stops the installer inside WSL and releases the setup lock; the Linux R card has its install button; the timing summary names sanity checks, ingest, voom, meta-analysis, organellar filtering, and genes-of-interest phases instead of "Other".
- **Meta-analysis figure settings are exposed.** The Figure style panel gains controls for the number of genes labelled on the cross-study volcano, the genes in the effect-size heatmap, and the terms in the cross-study enrichment dot plot, and the Analysis settings page gains a meta-analysis GO ontology choice (biological process by default, molecular function, or cellular component); these settings were previously read by the pipeline but could not be set. The organism catalogue's `kegg_keytype` reaches the enrichment configuration and provenance.
- **Network-share paths are refused where they are chosen.** The reference, FASTQ, and gene-set pickers, the project folder check, and the run and setup launchers report a `\server\share` path with a message instead of a traceback or a path the pipeline cannot open; a setup launch that fails to start now finishes the Check Environment thread instead of leaving it running.
- **Dead configuration and files removed.** `deseq2.lfc_shrinkage`, `fastp.detect_adapter_for_pe`, `resources.temp_dir`, `resources.keep_intermediate`, and `figures_style.rasterize_points` were read by nothing and are dropped from the model and defaults, as is `ppi.hub_label_count`, whose static-figure labels no longer exist (existing projects still load); two unreferenced scripts, three unused environment files, and an unread tool-defaults file were moved to `outdated/`.
- **Environment setup and readiness are pinned, profile-aware, and path-safe.** micromamba is fetched at a pinned version with a per-platform SHA-256 verified before it is made executable; the setup script is BOM-free and ASCII-clean; the installed profile (core or full) is recorded and Check Environment reports full-only tools and the R stack as not applicable on a core environment instead of advising a rebuild; readiness resolves `snakemake` through the environment like every other tool, accepts only the environment prefix the run path uses, and probes every catalogued OrgDb; the blocking R gate in project validation lists every package the mandatory rules load and is tested against the readiness list; a non-WSL network path (`\server\share`) is refused with a named error instead of being translated into a path no run can open; apostrophes and spaces in the install path survive both the WSL and the elevated setup launchers; `software_versions.txt` records the trimmer, rRNA tool, contamination screen, RSeQC, gffread, perl, and aria2 versions when those routes are configured; resource totals below 1 are rejected; macOS is refused with a clear message rather than a script exit.
- **The release script cannot publish a mismatched tree.** It refuses a dirty working tree or a local HEAD that differs from the pushed branch, tags the exact local commit, and re-reads the published release to compare every asset name and size against the checksum manifest.

### Fixed

- **A local microarray matrix with text tokens or comma decimals is refused instead of being silently converted to rank codes.** The ingest step parses every sample column strictly and names the offending values; the import dialog performs the same check before the file enters the project.
- **KEGG GSEA results are kept when KEGG over-representation cannot run.** The two legs are audited separately with explicit `KEGG ORA status` and `KEGG GSEA status` evidence lines; an empty significant-gene foreground is reported as not run rather than as a resource failure, and the report describes each leg on its own.
- **The contrast-orientation check now uses the contrast denominator.** The explicit DESeq2 contrast fixes the sign of log2 fold change regardless of `reference_level`, so the check no longer passes an inverted contrast when the shipped default reference level is set.
- **Numeric-looking covariates are flagged.** A design term with only numeric values and ten or fewer distinct levels is fitted as a continuous trend by every engine; the design check now reports it for review, the design helper labels such columns, and file, checksum, count, and title columns are no longer offered as covariates.
- **Hyphenated dataset names survive the meta-analysis figures.** Per-study columns are read without name mangling, so an ArrayExpress-style study such as `E-MTAB-2523` appears in the forest plot and per-study summary instead of leaving them blank.
- **Rat, cow, chicken, and pig annotation packages are installed.** The four OrgDbs named by the organism catalogue are now in the environment lock and fallback specification, and every catalogued OrgDb is load-tested by Check Environment and the setup script; those presets previously fell back silently to the g:Profiler route.
- **Run output is decoded as UTF-8 and the log reader cannot hang the interface.** On Windows the pipeline pipe used the console code page in strict mode, so one curly quote from R stopped the reader and left the run pinned at Running with a full pipe.
- **Figure-style overrides appear in the run provenance.** `figure_overrides` was the one setting absent from the bundled defaults and was therefore skipped by the customized-parameter diff.
- **The administrator WSL setup script no longer aborts on wsl.exe diagnostics.** Native stderr lines were terminating under strict error handling, which made the plain-install retry and the broken-distribution guidance unreachable.
- **Figure-layout contracts execute the R layout helpers.** The sample-distance and correlation label-angle tests run the helpers and assert the returned angle (a transposed projection now fails), the rendering tests fail rather than skip when `BULKSEQ_REQUIRE_R` is set, and CI gains an R-equipped job that runs them.
- **Sanity-check aggregation tolerates a malformed check file.** The network step writes its check through a JSON serializer, and an unreadable check is reported as a FAIL badge with the parse error instead of aborting the aggregation.
- **The Windows installer refuses to run while BulkSeq Studio is open.** The application holds a named mutex that Setup checks before it removes the previous version, Setup also refuses when it finds the application's main window (which protects upgrades from builds that predate the mutex), and helper processes are force-closed at the copy stage; previously a silent update could uninstall the old version and then abort on a file still in use, leaving no installed copy (reproduced during the installed-build validation of this release).
- **The Reports page describes the reports on disk.** After a completed run, and when a project that already holds reports is opened, the page shows the run and timing summaries instead of "No reports yet" (found during the installed-build validation of this release).
- **The figures-regeneration path reads the configured sample sheet** when deciding whether meta-analysis rules exist, matching the Snakefile.

## 0.28.0 — 2026-08-13

### Added

- Added a recomputed `SHA256SUMS.txt` covering every Windows and Linux release artifact, with a fail-closed digest verification before tagging or upload.

- **Five same-source-snapshot graphical-interface acceptance runs now cover every bundled preset.** The retained Pasilla, UME6, rice CY1000 salt, Arabidopsis hub2-3, and yeast cbc2 runs passed source, workflow, artifact, and responsive-rendering gates. Sample-structure and limited-annotation findings remain visible as descriptive warnings rather than being converted into biological claims.
- **Reports expose route-specific audit evidence.** Outputs now distinguish configured from realized strandedness, report deterministic GSEA ordering and duplicate collapse, retain custom-gene-set evidence when configured, and state when dense static PPI topology is replaced by the interactive network and exported identity tables.

### Changed

- **The documentation site now uses the desktop application's visual system.** The public pages use the same system type, blue-grey palette, control geometry, and light/dark surfaces as BulkSeq Studio; decorative landing-page treatments were removed in favour of a compact manual layout, and installation, input-route, output-map, and citation guidance is explicit.
- **Documentation interactions now retain keyboard and reflow access.** Prose links remain underlined, mobile navigation and wide tables stay contained, and the image viewer has an accessible name, modal focus containment, focus restoration, and keyboard zoom/pan controls.
- **Resource profiles use the WSL logical-CPU allocation that actually constrains Snakemake.** Low, balanced, and high profiles derive 45%, 75%, and 90% of the available guest logical CPUs, while host and guest physical-core counts remain diagnostic. CPU and memory probe fallbacks are reported independently, and custom profiles are verified against both the graphical controls and the freshly reloaded project configuration.
- **Scientific figures and reports use deterministic, fail-closed layout contracts.** Sample-distance and correlation heatmaps allocate measured label and cell geometry, standalone GSEA plots name the selected term, volcano highlights use a ranked key instead of ambiguous crossing leaders, responsive reports contain wide figures and tables without page spill, and static PPI layouts either satisfy explicit topology/visibility bounds or return a labelled fallback.
- **The runtime dependency declarations and readiness checks now follow direct imports and executable use.** NumPy and the direct R namespaces are declared explicitly, setup verifies the selected environment profile and companion commands, and ordinary repair no longer deletes an environment unless rebuild was explicitly requested.

### Fixed

- **CI now distinguishes a present R executable from a usable scientific test environment.** R-dependent safety and enrichment tests probe their required namespaces before selecting the native runtime, while compact GUI and report geometry reserve measured font and content space across platform font metrics.
- **Reference staging, input validation, and resume gates now bind the files the workflow actually consumes.** Content fingerprints cover reference material, sample sheets, copied external-result tables, and generated indexes; unsafe paths, malformed tables, missing realized inputs, and stale validation records fail before execution.
- **Organism-specific enrichment and identifier mapping no longer assume a universal `SYMBOL` namespace.** Yeast uses its effective OrgDb namespaces, KEGG identity distinguishes species codes from strain taxon metadata, attempted-but-missing GO output is review-required, and readable labels are requested only when the OrgDb supports them.
- **STRING identifiers preserve source-case nomenclature across one-to-many mappings.** Exported node and hub identities retain canonical input spelling while joins remain case-normalized, and dense-network exports survive even when a static layout cannot meet its bounded search contract.
- **STAR sorting memory is now bounded by each job's declared reservation.** The generated `--limitBAMsortRAM` receives half of the effective `mem_mb`, leaving explicit headroom for alignment and making scheduler accounting honest.

## 0.27.0 — 2026-08-10

> **Release status:** 0.27.0 is a local acceptance-test candidate. Its Windows installer and portable archive, Linux AppImage and zsync metadata, and Linux portable archive have not been published. The current public GitHub release and latest deposited Zenodo benchmark snapshot remain 0.26.6 until explicit release approval.

### Changed

- **The desktop interface is reorganized around four scientific stages rather than a twelve-tab strip.** Project and data, Analysis setup, Validate and run, and Explore results each expose only their relevant pages; wide windows use a stage rail and page buttons, while windows below the derived 1366-pixel breakpoint use stage and page selectors without losing access to any task. Dense secondary controls move behind purposeful disclosures, the Run Monitor separates primary actions from options and execution detail, and Outputs and PPI inspectors remain usable at compact desktop sizes.
- **Light and dark appearance is now one live application theme.** The persistent header control repaints the main window, readiness dialog, plots, progress surfaces, empty states, and embedded protein network without restart, while preserving the current project, page, focus, and dialog actions. PPI nodes can also be traversed by keyboard with their selected-node details exposed to assistive technology.
- **Windows and Linux packaging are reproducible at the build-tool layer.** `requirements-build.txt` pins PyInstaller 6.21.0 and Pillow 12.2.0 exactly; the application, workflow, Python package metadata, Inno Setup fallback, Windows executable resource, and all five artifact names derive from the application version. Both platform builds must pass the frozen QtWebEngine/Cytoscape probe before packaging; Linux additionally requires verified zsync metadata and a conventional portable archive, and CI verifies a checksum-pinned AppImage tool.

### Fixed

- **A saved pre-run pass could outlive the inputs it described.** Input validation now stores a content fingerprint over the configuration, configured sample sheet, local input files, reference locks, and index contents. Launch and resume compare that state with the current project, including same-size file replacements and index-shard changes; missing or unreadable inputs, unsupported direct URLs, unsafe symlink or junction escapes, malformed manifests, and over-broad directory materialization fail closed. Unchanged files reuse recorded content digests, and initial hashing and revalidation are cancellable without blocking the GUI thread.
- **Imported differential-expression tables could be accepted from a short preview and later be reinterpreted by the workflow.** The GUI and R ingest now validate the complete CSV/TSV with matched field counts and aligned UTF-8, Windows-1252, and Latin-1 handling; require unique, nonblank, whitespace-safe gene IDs; reject malformed, non-finite, or out-of-range numeric fields; accept canonical missing values only where finite data remain; and reject a supplied statistic whose sign contradicts the confirmed log2-fold-change direction. The verified project copy is bound to its SHA-256, byte size, row count, column schema, selected columns, import timestamp, and source-direction record before Snakemake can ingest it.
- **Imported-result provenance could incorrectly inherit a local contrast, DESeq2 model, shrinkage method, or adjustment label.** Direction now comes only from the explicitly confirmed source numerator and denominator; stale local design fields cannot change it. Reports and exports state that no local differential-expression model or shrinkage ran, identify the upstream method and p-adjustment only when recorded, omit inactive read-processing and local-model settings, and exclude stale count-dependent figures. Unknown adjustment methods remain generic rather than being called Benjamini–Hochberg.
- **The imported-results preranked export could use an undocumented upstream statistic.** `ranked_genes.rnk` now ranks imported rows by the confirmed `log2FoldChange`; locally fitted routes retain their model statistic. This is the only intentional scientific-output change in 0.27.0: it affects the exported ranking for results-only projects and may cause previously tolerated malformed or provenance-incomplete imports to stop. The local FASTQ, count-matrix, and microarray statistical routes and the deposited 0.26.6 benchmark claims are unchanged.
- **Run provenance could list tools and inputs that were configured but inactive.** Run summaries now report the selected trimmer, rRNA tool, contamination screen, DE engine, operating system and architecture, and recorded R/BLAS/LAPACK details for the route that actually ran; study-design export follows the configured sample-sheet path instead of assuming `config/samples.tsv`.
- **A successful frozen WebEngine probe could terminate with a Windows fast-fail during interpreter shutdown.** The self-test now closes and deferred-deletes its Chromium views while the Qt event loop is still alive, then exits after deferred destruction; a passing sentinel and a zero process exit are both required.
- **Silent upgrades could wait indefinitely behind an invisible existing-version prompt.** Explicit `/SILENT` and `/VERYSILENT` installs now take the normal fresh-update path without opening the interactive three-way dialog; interactive installs retain the update, uninstall, and cancel choices.

## 0.26.6 — 2026-08-07

### Fixed

- **The multi-study meta-analysis corrected for multiplicity over the wrong family.**
  `run_meta_analysis.R` adjusted the combined p-values across every gene and then removed
  direction-discordant genes from the called set, so the reported meta-DEGs were a
  Benjamini–Hochberg rejection set minus a subset chosen after the threshold was fixed. The
  guarantee does not transfer to such a subset, and the filter is not independent of the
  p-values: a gene with strong opposing per-study effects has both a small combined p-value and a
  high chance of being discordant, so discordant genes crowded the low tail, raised the cutoff and
  admitted null genes that then survived the filter. Measured in B19's discordance arm, which
  plants only discordant genes and therefore contains no true positive, the combination reported
  **58 meta-DEGs across five runs, every one a false positive**, at approximately α × (discordant
  rejections) per run. The correction restricts the family to concordant genes before adjusting;
  discordant genes keep their row and raw combined p-value but carry no adjusted value, because
  they are not being tested. Re-run, the arm reports **1** gene across five runs, and the power
  arm improves slightly because the tested family is smaller. Anyone who has run a meta-analysis
  should re-run it: the differentially expressed gene set changes.
- **The meta-volcano would have drawn discordant genes as the most significant points.** With
  discordant genes carrying no adjusted p-value, `NA <= 0 | !is.finite(NA)` evaluates to TRUE, so
  the fit-all branch placed every one of them at the capped ceiling. The volcano now excludes
  genes with no adjusted p-value and states how many in its subtitle.
- **B19's acceptance criteria missed the defect they existed to catch.** Criterion C asked only
  whether discordant genes were kept out of the called set, never what the called set contained.
  A new criterion F bounds the discordance arm's false-positive count; scored against the pre-fix
  data it fails at 5 of 5 runs with a lower confidence limit of 0.478.
- **Five release gates could not fail.** `check_typography.py` returned 0 unconditionally and, run
  from outside the repository root, read no files while reporting "0 issues across 2 file(s)";
  `build_main_tables.py` returned 0 regardless of its own checks, so deleting a table body
  propagated silently into every deliverable; an early return made the article-wide placeholder
  sweep unreachable; `check_b19` verified only the study-count stratum that decides the verdict,
  which let a wrong three-study count stand in the manuscript; and `extract()` scanned forward
  with no stop condition, so a missing table body made it return a different table's rows. Each is
  now demonstrated to fail on an injected defect.
- **B19 was not regenerable from the deposit.** Its simulator sources
  `workflow/scripts/run_meta_analysis.R`, which lives outside `article/` and was never staged. The
  archive builder now derives its external dependencies from what the deposited scripts actually
  source.

### Changed

- **Benchmark archive deposited (Zenodo)** as version 0.26.6, [10.5281/zenodo.21833538](https://doi.org/10.5281/zenodo.21833538), under the concept DOI [10.5281/zenodo.20955660](https://doi.org/10.5281/zenodo.20955660), which resolves to it. This is the first deposit carrying the corrected meta-analysis; 0.26.4 and 0.26.5 were built but never deposited, and every earlier deposit describes software with the defect.

### Added

- **`scripts/preflight.py`** runs every release gate in one command and reports which could not
  run. The manuscript and benchmark gates cannot run in CI because `article/` is gitignored, so
  they only ever run when someone runs them; a gate that cannot run is reported as a failure
  rather than a pass.

## 0.26.5 — 2026-08-06

### Fixed

- **B19 simulated from a defective dispersion estimate, and its central result changed when that
  was corrected.** `make_sim_params.R` took `mcols(dds)$dispGeneEst`, the unshrunken gene-wise
  maximum-likelihood estimate. That estimator collapses to the optimiser's lower boundary for any
  gene whose observed variance sits at or below the Poisson expectation, which at six samples is
  common: 4,203 of 10,303 genes (40.8%) carried dispersion 10⁻⁸, including 1,133 with a base mean
  above 100, so two fifths of the simulation was very nearly Poisson. Using the maximum-a-posteriori
  dispersions a DESeq2 analysis actually tests with puts 0.0% at the boundary. Re-simulated, the
  complete-null rejection falls from 5 of 10 runs to 2 of 10, and the null-calibration criterion is
  met — weakly, since at ten runs a true rate of 0.20 escapes the criterion 68% of the time, which
  the manuscript now states rather than reporting the pass as a demonstration of control.
- **B19's source count matrix was unnamed and unreproducible.** The manuscript said only "a real
  count matrix"; no matrix available reproduced the deposited parameter file's row count. The
  source is the pasilla count matrix, now named in the script, recorded in the output table, and
  printed in the run log.
- **B19 ran at one replicate count only.** Five replicates per group was hardcoded in four places,
  so every conclusion was scoped to that design. The count is now `--reps`, and a ten-replicate arm
  is deposited alongside. The power criterion passes at five replicates (+11 to +33 true positives
  over the best single study) and **fails** at ten (−16 to +14, four runs of ten below the best
  single study), while the combination's observed false-discovery rate stays at about half the best
  single study's at both. Combining buys sensitivity when the constituent studies are individually
  underpowered and specificity when they are not.
- **The exact binomial interval was wrong for n ≥ 60.** `clopper_pearson` in `score_b19.py` returned
  (1.0, 1.0) because the continued fraction for the regularised incomplete beta diverges above
  x = (a+1)/(a+b+2) without the reflection identity. No reported interval was affected — B19 scores
  ten runs and B17's 200-draw intervals come from R's `binom.test` — but the helper ships in the
  archive. Fixed, and guarded by `score_b19.py --self-test` against values from `binom.test`.
- **The B9 bread-wheat index was reported as two different measurements as though it were one.**
  There are two Salmon indexes: 221,398 transcripts at 1.14 GB for the resource-scaling row and
  216,567 at 1.18 GB for the end-to-end quantification. The Results quoted the first index's
  transcript count and the second run's expressed-transcript count, and the two were labelled GiB
  and GB, which do not reconcile. Both statements now name their index and use one unit.
- **The deposited B17 figure was a superseded version** carrying the pre-correction rejection rates
  and KEGG counts, contradicting the published Figure B17 and the B17 tables beside it.

## 0.26.4 — 2026-08-06

### Fixed

- **B2's complete-null conclusion was scored on the wrong quantity, and the paper argues so
  itself.** B17 and B19 both state that under a complete null every rejection is false, so the
  Benjamini-Hochberg guarantee reduces to the probability that a run rejects at all, and B19's
  correction of record says a count "made the arm incapable of failing". B2 nonetheless concluded
  from a mean false-positive count that its null was well calibrated at ten replicates. Scored on
  the run-level rate, B2 rejects in 5 of 5 null runs at n=5 and 2 of 5 at n=10, both exact
  intervals lying above 0.05, and B18's own deposited per-seed table records 5 of 5 seeds
  rejecting at the nominally identical design. Table 1, the B2 summary and B18's opening premise
  are corrected.
- **B15's marker claim was not supported and its evidence was not deposited.** The manuscript
  named six canonical targets as "the most strongly up-regulated genes"; in the run's own DESeq2
  table *FKBP5* ranks third by adjusted p and ninth by fold change among 125 up-regulated genes.
  The panel is pre-specified, not top-ranked, and is now described that way. The per-gene table
  and a marker panel are deposited, and the panel ships as Table B15b.
- **B19's realism justification omitted a limitation of its own parameters.** 4,203 of 10,313
  resampled rows carry the gene-wise dispersion estimator's lower boundary of 10⁻⁸, so about 41%
  of simulated genes are near-Poisson and the absolute true-positive counts in the power arm are
  optimistic. Disclosed.
- **The documentation site contradicted the deposit on B19**, claiming 96% sensitivity, an
  empirical false-discovery rate "about 1%", and that the null design showed type-I control. The
  deposit records a true-positive rate of 0.65 to 0.72 and a failed null-calibration criterion.
- **`manifest.sha256` was written with CRLF terminators**, so `sha256sum -c`, the command the
  archive itself documents, failed on every line under GNU coreutils 8.32 and earlier.
- **`REGENERABILITY.md` labelled a family "partly" regenerable with nothing regenerable.** The
  verdict counted implemented scripts alone; it now accounts for whether the family deposits any
  result, and a new "driver only" category separates code-without-output from the rest.
- **Three accessions used to produce reported results were missing from the availability
  statement** (SRA000299, GSE11045, DRR003149), and every image reference in the preprint
  resolved from the wrong directory, so no figure rendered without an explicit resource path.
- **Four table legends promised columns their tables did not carry.** B14 and B17.1 now deliver
  the enumerated columns from their deposited sources; B20's legend describes the table as
  delivered and points to the deposited files holding the per-sample split and the Salmon
  cross-check.

### Added

- **Supplementary tables ship as a single workbook**, `Supplementary_Tables.xlsx`, one sheet per
  table grouped by theme with a contents sheet, alongside the per-table CSV and DOCX. All three
  forms are generated from the manuscript by the same script.
- **`check_consistency.py` gained four gates**: every accession used in the text must appear in
  the availability statement; the supplementary set must agree across CSV, DOCX, workbook and the
  manuscript's own declaration; no deposited table may leave a ground-truth basis unresolved; and
  the documentation site's version badge must match the application.

## 0.26.3 — 2026-08-06

### Changed

- **Released as 0.26.3, aligning the application with the deposited benchmark archive.** Versions
  0.26.0, 0.26.1 and 0.26.2 were never released: 0.26.0 and 0.26.1 carried benchmark, manuscript
  and packaging work under version-string bumps, and 0.26.1 and 0.26.2 were archive builds each
  superseded by review before deposit — 0.26.1 over an unsatisfiable form of the B19 null
  criterion, 0.26.2 over six deposited tables that left a ground-truth basis unresolved. Their
  entries below are kept as the record. The analysis path is unchanged across all of them:
  `git diff v0.25.0..HEAD -- workflow app` is empty, so every benchmark reported against 0.26.x
  ran against the same analysis code this release ships.
- **Benchmark archive deposited (Zenodo)** as version 0.26.3, [10.5281/zenodo.21825754](https://doi.org/10.5281/zenodo.21825754), under the concept DOI [10.5281/zenodo.20955660](https://doi.org/10.5281/zenodo.20955660), which resolves to the latest version. Versions 0.26.1 and 0.26.2 were built but never deposited: each was superseded by review before it went out, 0.26.1 over an unsatisfiable form of the B19 null criterion and 0.26.2 over six deposited tables that left a ground-truth basis unresolved. The published chain therefore runs 0.26.0 to 0.26.3.

### Added

- **Two generators replace hand-maintained deliverables.** `article/scripts/build_main_tables.py`
  derives Table 1, Table 2 and all nineteen supplementary tables from the manuscript; the
  standalone Table 1 had stopped at B13 while the manuscript ran to B20, and three supplementary
  tables carried values the manuscript contradicted. `article/scripts/check_consistency.py`
  derives every checkable claim from the deposited tables and verifies it across the manuscripts,
  README, documentation site and `CITATION.cff`, including that the archive description agrees
  with the deposited acceptance table.

### Fixed

- **The documentation site advertised v0.24.0 on all seven pages** while the application was at
  0.26.x, and the FAQ's citation examples named version 0.24.0 under a superseded archive title.
- **`build_b_tables.sh` is superseded and now refuses to run.** Its table bodies were hardcoded
  heredocs that had drifted from the manuscript, and its final line invoked a build whose input
  does not exist, killing the script after it had already rewritten `main.docx`.

## 0.26.1 — 2026-08-06

### Fixed

- **B19's null-calibration criterion bounded a quantity that cannot fail, and the correct quantity fails.** The criterion compared the proportion of genes called under the complete null to 0.05. A Benjamini-Hochberg procedure at q = 0.05 can never call more than that fraction under the null, so the test passed trivially and concealed the result; B17 states the correct principle explicitly for the same situation, and B19 did the opposite. Measured as the run-level rejection rate, which is what Benjamini-Hochberg bounds when every rejection is false, the arm fails: 5 of 10 runs reject at least once, 4 of 5 at two studies, against a bound of 0.05. The combination is still a large improvement on its inputs, but the comparison that first appeared here was withdrawn: it set the combination's run-level rejection RATE against a mean of 19.4 false-positive COUNTS from B2, a different simulation at a different gene count and dispersion model. Measured on one quantity within one simulation, all 15 distinct constituent single studies reject in every null run against 5 of 10 for the combination. A two-study combination at that replicate count still does not control the family-wise error rate, and the manuscript says so.
- **The second study in B19's real-data arm was unidentified.** It was described only as "kidney podocytes", with no accession, citation or design, so the arm could not be reproduced. It is Jiang et al. 2016 (GEO GSE80651, SRA SRP073810): three conditionally immortalised human podocyte lines from independent donors, 0.1 µM dexamethasone or vehicle for 24 hours. Now cited, referenced and listed under availability of data.
- **B19's real-data arm reported only the panel that passed.** The scoring defines an induced and a suppressed marker panel and deposits both; the manuscript reported the induced panel and omitted the suppressed one, in which one gene is discordant between studies and two are not significant. Both are now reported, with the reason the negative panel is weak.
- **Table 1 listed the airway libraries as B20 data.** They carry no strandedness call: they were quantified through the Salmon route and produced no alignment for either inference mechanism to read. Their actual role, as the best-documented library against which the independent cross-tool check was itself validated, is now stated.
- **B19 and B20 rendered as subsections of "Availability and requirements".** They were inserted before the Discussion without accounting for that heading sitting between, and are now inside Results.
- **The Discussion still declared the meta-analysis and strandedness detection unvalidated**, thirty lines after the two families that measure them.
- **Figure 1 did not render in the built submission document.** Pandoc emits an SVG as a bare `<a:blip>` with no `r:embed`, so Word had no image part to draw. The DOCX build now substitutes the raster on a copy, leaving the markdown vector-first for every other output.
- **Both tables in the built document had no borders or header styling**, because pandoc writes a `tblStyle` the reference template does not define. The existing `fix_docx_tables.py` is now wired into the build.
- **Six of twenty figures shipped as vector only.** The rasterisation loop hardcoded fourteen filenames from an earlier revision; it now derives the list from disk and cross-checks it against the figure legends in the manuscript.
- **The read-level determinism comparison was reported but not deposited.** `b10_readlevel_rerun.csv` now carries both re-runs: the rice STAR route against an earlier local run of the same accessions (identical gene set, Jaccard 1.0, maximum absolute log2 fold-change difference 0) and the rice Salmon route against the deposited results table (Jaccard 1.0, maximum difference 0.0894, the expected signature of its expectation-maximisation step).

### Changed

## 0.26.0 — 2026-08-06

### Added

- **Two benchmark families, both previously shipped but unmeasured.** **B19** benchmarks the
  multi-study meta-analysis: four simulation arms driving the shipped combination directly
  (null calibration, power against the best single study, direction concordance, between-study
  heterogeneity), a real-data arm combining two independent human glucocorticoid experiments of
  deliberately unequal power, and a gate arm confirming that designs which cannot support the
  contrast are refused. **B20** benchmarks automatic library-strandedness detection across
  documented, independently inferred and constructed libraries, scoring both inference
  mechanisms separately and reporting the margin from each observed ratio to its decision
  boundary. Acceptance criteria for both were fixed before the measurements were taken, and
  both families are fully regenerable from the deposited code.

### Changed

- **Benchmark archive re-deposited (Zenodo).** The validation suite now covers twenty families
  and is deposited as version 0.26.0, DOI
  [10.5281/zenodo.21819488](https://doi.org/10.5281/zenodo.21819488). The concept DOI
  10.5281/zenodo.20955660 is unchanged and continues to resolve to the latest version. The
  archive holds 430 files with 281 released tables and figures under `manifest.sha256`, and
  `REGENERABILITY.md` now records 52 of 103 scripts as implemented.

### Fixed

- **The KEGG background-correction result was measured on the wrong gene list.** The B17
  scoring selected differentially expressed genes on adjusted p-value alone, while the pipeline
  thresholds on adjusted p-value *and* raw fold change and submits the resulting up- and
  down-regulated files. The earlier measurement therefore described a query the software never
  issues, and it reported the correction as uniformly raising the significant-pathway count.
  Rescored on the real query it reproduces the pipeline's own output exactly, and the direction
  is dataset-dependent: it removes all four *Fusarium* pathways, leaves pasilla with none under
  either background, adds two net on a rice STAR run and removes two net on a rice Salmon run.
  The B17 null-control draws were sized on the same wrong list and were re-run; all three arms
  still straddle the nominal 0.05 and the power arm still fails to separate, so both
  conclusions of that family survive.

## 0.25.0 — 2026-08-06

### Added

- **A `bulkseq` command line.** A headless front end for the parts of the tool that do not
  need a window: `bulkseq project create` scaffolds a project, `project info` summarises one,
  `config show/get/set` reads and edits the configuration, `samples show` prints the sample
  sheet, `check` validates a project before committing a cluster allocation to it, and
  `print-command` emits the exact Snakemake invocation the graphical application would run.
  It shares `app.core` with the interface rather than reimplementing it — the same project
  scaffolding, configuration model, sample-sheet validation and, critically, the same
  `build_snakemake_command()`, so the two front ends cannot drift into running subtly
  different analyses. Output discipline is deliberate: stdout carries only what a command was
  asked to produce and the banner, progress and warnings go to stderr, so
  `bulkseq config show --json > c.json` is always a clean file. Documented at `docs/cli.html`.

- **Execution profiles for Slurm and Kubernetes.** `bulkseq print-command --exec-profile slurm`
  (or `kubernetes`) emits an invocation that hands each job to a scheduler through the matching
  Snakemake executor plugin instead of running everything on one machine, using the site
  profiles under `workflow/profiles/site/`. It prints the command and stops; submitting is left
  to you, because a command that queues sixteen jobs against a fair-share budget is worth
  reading first. Both profiles set a per-rule thread ceiling explicitly, because
  Snakemake otherwise reuses `jobs` as that ceiling and `--jobs 2` would silently cap every
  alignment at two threads; `tests/test_exec_profiles.py` derives the real maximum from the
  rule sources and fails if the profile drops below it. Neither profile overrides `mem_mb` or
  per-rule threads, so the run summary cannot report one allocation while the scheduler was
  told another. Partition, account and walltime have no defensible default and are left for
  the site to fill in. The Kubernetes profile carries an explicit warning that it cannot work
  from `executor: kubernetes` alone: no rule declares a `conda:` or `container:` directive, so
  a container image carrying the full tool stack must be supplied first, and a green
  `--dry-run` proves the job graph is valid and nothing about whether a rule can execute.

### Changed

- **Benchmark archive re-deposited (Zenodo).** The validation suite now covers eighteen
  families and is deposited as version 0.24.0, DOI
  [10.5281/zenodo.21807799](https://doi.org/10.5281/zenodo.21807799). The concept DOI
  10.5281/zenodo.20955660 is unchanged and continues to resolve to the latest version. The
  archive is built by `article/scripts/build_zenodo_package.py`, which derives its version
  from `app/constants.py`, and now ships `REGENERABILITY.md` stating per family how many of
  its scripts are runnable code: 34 of 85 are implemented, so several families are deposited
  as data only. Publishing that inventory keeps `REPRODUCE.md` from being read as a promise
  the archive cannot keep.

### Fixed

- **A config naming a file the project does not have now fails with a message that names the
  setting.** Several rules are defined only when a setting names a file — `custom_gene_list`,
  `custom_gene_sets`, `functional_annotation_table`, `background_gene_list`, the count-matrix
  and DESeq2-results input tables, and the local microarray matrix — and then use that value
  as a rule input. Snakemake resolves inputs while building the job graph, so a path that is
  not there stopped the run before any job executed, with a `MissingInputException` naming a
  rule and a filename and nothing that connected it to the setting responsible. Copying a
  colleague's `config.yaml` into a fresh project was enough to hit it. The paths are now
  checked as the workflow is parsed, which is the only point early enough to precede the job
  graph, and the error names the setting, the missing path, the feature it enables, and that
  clearing the setting is a valid way to proceed. The same check runs inside
  `validate_project.py`, so the message also reaches the sanity-check panel on runs that do
  build.
- **Workflow diagram: twelve factually wrong labels.** An audit of all 168 text claims in
  `docs/assets/bulkseq-workflow.svg` against the 0.24.0 source found twelve wrong or
  misleading. Salmon was described as "alignment-free" when `salmon quant` runs with
  `--validateMappings` (selective alignment); rRNA removal was marked an "additive" step
  although it re-routes the aligner input and so changes the counts; the Affymetrix log2
  badge sat on the one branch where the setting is inert; the multi-study lane omitted that
  an explicit opt-in switch is required. The same "alignment-free" error was present in the
  README, two documentation pages and the manuscript, and is corrected throughout. The
  diagram is the source of the manuscript's Figure 1, which is now derived from it rather
  than maintained separately.
- **`scripts/check_svg_layout.py` reported fabricated overlaps.** The checker read `x`/`y`
  from the `<text>` element only, so a figure carrying them on child `<tspan>`s collapsed
  every box to the origin and produced thousands of spurious findings instead of admitting
  it could not measure the file. It now positions per baseline, accepts single-quoted
  attributes and unit-suffixed root dimensions, and reports SKIP with a reason when its
  monospace width model does not apply.
- **`article/scripts/seeds.yaml` contradicted its own data.** Three of four entries
  disagreed with the results they govern: polyester recorded five seeds against six result
  directories, B2 twenty against five, and B10 listed thread counts the determinism run never
  used. It now records what each family ran, cites the deposited file each value came from,
  and covers B3, B17 and B18.
- **`b7_limma_validate.R` rounded a floating-point residual to zero.** `round(maxd, 4)`
  printed the maximum absolute log2 fold-change difference as 0, a value it cannot take, so
  the manuscript contradicted its own deposited table. It now uses `signif(maxd, 3)` and
  reports 1.25e-14.

## 0.24.0 — 2026-08-04

> **Results may differ from earlier versions.** KEGG over-representation was corrected to
> test against the expressed-gene background instead of the whole annotation, so a KEGG
> term list produced by 0.23.x or earlier was computed against a larger universe.
> The direction depends on the dataset: measured on four archived runs, the correction
> removed all four *Fusarium* pathways, left pasilla with none under either background,
> added two net on a rice STAR run and removed two net on a rice Salmon run. Eight
> previously significant pathways are absent from the corrected lists. Differential-expression
> results are unaffected. If you have published a KEGG enrichment from an earlier version,
> re-run it before citing it against this release. See *Changed* below.

### Added

- **Platform provenance in the run summary.** Runs now record the operating system,
  architecture, and R's BLAS/LAPACK libraries, so a run under WSL2 on Windows is
  distinguishable from a native Linux run when comparing results.
- **Tool versions for every configured route.** The run summary now records the trimmer,
  rRNA filter and contamination screen actually selected, rather than assuming fastp and
  SortMeRNA.

### Changed

- **KEGG over-representation now uses the expressed-gene background.** `enrichKEGG` was
  the only over-representation call in the pipeline that did not pass a `universe`, so it
  tested against every gene in the KEGG organism while GO and Disease Ontology tested
  against the genes actually measured. That inflates KEGG enrichment: a pathway absent
  from the experiment counted as depleted background rather than as untested. KEGG **GSEA**
  is unaffected — it ranks the full gene list and takes no universe.
- **g:Profiler results are labelled with their real correction method.** The report showed
  g:Profiler's `p_value` under a `p.adjust` header and glossed it as Benjamini–Hochberg.
  g:Profiler corrects by g:SCS, its own graph-aware method. Enrichment tables now
  distinguish `p.adjust (BH)` from `p (g:SCS)`. No value changed — only the label, which
  was naming the wrong statistic.
- **The volcano plot's colours now agree with the gene lists.** Up/Down classification was
  taken from the shrunken fold change while `upregulated_genes.csv` and the report caption
  used the raw one, so the number of coloured points disagreed with the caption beneath the
  figure. Classification now uses the raw fold change, matching the tables; the x-axis
  still shows the shrunken effect size and now says so.
- **The realised shrinkage method is reported.** When `apeglm` fails and the run falls back
  to `ashr`, the summary said `apeglm`. It now records what actually ran, and why.
- **Status colours meet WCAG-AA contrast in both themes.** Run status, the readiness cards
  and the reference advisory were painted with light-theme colours regardless of the active
  theme, so a completed run in dark mode rendered at 3.3:1. Five status colours also failed
  AA against their own backgrounds and were re-derived.
- **`WORKFLOW_VERSION` bumped to 0.24.0**, so existing projects re-copy the bundled
  workflow on open and pick up this release's script corrections — including the KEGG fix.

### Fixed

- **Creating a project over an existing one no longer destroys it.** Re-using a project
  name in the same working directory silently reset `samples.tsv`, `contrasts.yaml`,
  `gene_sets.yaml` and `config.yaml` to empty defaults, with no warning and no backup. The
  one-click *Create Benchmark Project* flow was the likeliest way to hit it, since the
  project name defaults to the benchmark id. Both paths now stop and ask, naming exactly
  what would be lost, and default to keeping the existing project.
- **Sample sheets and matrices exported from Excel now import.** The count-matrix, DESeq2
  results, microarray-matrix and metadata importers read files as UTF-8 only, so Excel's
  default CSV export on a non-English Windows locale (cp1252) failed with a raw
  `UnicodeDecodeError` on the first accented character.
- **Sample ids differing only in capitalisation are now rejected.** `Sample1` and `sample1`
  passed validation but resolve to one file on Windows (NTFS is case-insensitive), so the
  two samples overwrote each other's intermediates and the run reported whichever wrote
  last.
- **Stopping a run now terminates the whole process tree on Linux.** Stop signalled only
  the Snakemake process, leaving STAR, featureCounts and Rscript running.
- **Combo-box arrows, spin-box arrows and checkbox ticks now render.** Styling the
  drop-down sub-control suppressed the native arrow without supplying a replacement, so
  every combo box in the application showed an empty square.
- **Form controls are sized to their content.** Fields and buttons stretched to the window
  width — a combo box holding a unit abbreviation rendered 756 px wide, and 57 buttons were
  wider than 400 px.
- **The GUI uses the platform's system font.** The interface hardcoded Segoe UI, which does
  not exist on most Linux installs.
- **Per-user files follow platform convention.** The error log was written to a folder in
  the user's home directory on Linux instead of following the XDG base-directory
  convention.
- **A benchmark dataset with single-end reads no longer crashes project creation.**
- **The microarray normalization setting is no longer silently ignored.** It has one
  implemented code path (RMA on raw CEL); a configuration it cannot honour now raises a
  warning in the sanity checks instead of doing nothing.

## 0.23.1 — 2026-07-21

### Fixed

- **Free disk space is no longer over-reported for a project on the WSL filesystem.** When the working directory sat on the WSL-native filesystem (`\\wsl.localhost\...`), the free-space readout came from the Linux disk, which is a sparse virtual disk (ext4.vhdx) whose *virtual* capacity defaults to ~1 TB. A machine with, say, 91 GB physically free could therefore be shown as having ~991 GB free, and the low-disk warning that guards against a run filling the drive never fired. The app now resolves the Windows drive that actually backs the WSL disk (from its registered `BasePath`, so a relocated vhdx is handled) and reports the smaller of the vhdx's reported free space and that drive's real free space. The resources readout also names the backing drive (e.g. "428 GB free disk on D: (backs the WSL disk)"). A plain local-drive path is unaffected.

## 0.23.0 — 2026-07-16

### Added

- **A per-study breakdown for every meta-analysis.** A multi-study run now writes a complete single-study report for each study it combines, under `results/meta/per_study/<STUDY>/`: a volcano, MA plot, p-value histogram, PCA, and top-DEG heatmap (PNG and SVG), the `de_results`/`upregulated`/`downregulated`/`summary` tables, and a self-contained `index.html` linking them. A top-level `manifest.json` lists every study with its tested/up/down counts, and the cross-study report links out to each study's page. Because each study is drawn on its own, a re-deposited or duplicated dataset is obvious — its volcano, PCA, and heatmap visibly match another study's instead of showing independent biology. The per-study PCA and heatmap reuse a variance-stabilised transform saved during the per-study DESeq2 fan-out, so no counts are recomputed.
- **Optional per-study functional enrichment.** A new **Per-study enrichment** switch (Workflow Settings, off by default) runs GO over-representation on each study's up- and down-regulated gene lists over that study's own tested-gene universe, writing per-study `go_ora_up`/`go_ora_down` tables and a dotplot plus an `enrichment_manifest.json`. It is opt-in because it repeats a clusterProfiler run once per study; the pooled cross-study enrichment still runs regardless.
- **GO over-representation split by ontology.** The enrichment step now writes separate Biological Process, Molecular Function, and Cellular Component tables and a dotplot for each, so the three GO namespaces are read apart rather than pooled into one plot. On organisms without a Bioconductor annotation package (the g:Profiler route) each category degrades to a labelled placeholder instead of a misleading empty plot.
- **A fold-change forest and a statistics table for genes of interest.** Supplying a gene list now also produces `goi_deseq2_results.csv` — the full DESeq2 statistics (fold change, p-value, adjusted p-value, base mean) sliced to just those genes — and `goi_log2fc`, a per-gene log2 fold-change plot with 95% confidence intervals coloured by direction. Both are built from the finished run, and both work in the uploaded-DESeq2-results mode where no count matrix exists.
- **Three new pre-run sanity checks.** A **contrast-orientation** check flags an inverted case-versus-control contrast where a positive log2 fold change would mean up in the control group. A **re-deposited-study** check (meta runs) flags two studies whose per-sample library sizes match exactly or whose per-study fold-change vectors correlate above 0.999 — pseudo-replication that inflates meta significance. A **per-study strandedness** check flags a study whose featureCounts assignment rate diverges from the others, which a single global strandedness setting cannot fit.
- **Two optional meta-volcano y-axes.** The combined FDR from the inverse-normal method underflows to exact zero for the strongest genes, which the default axis shows as off-scale triangles at a finite cap. Two opt-in styles are now available: **fit all elements**, which expands the axis to the largest finite value instead of clipping, and **inverse-normal Z**, which plots the always-finite combined Z statistic so no gene is off-scale. Both default off; the default axis is unchanged.
- **Direction filter and multi-select in the PPI network viewer.** A **Show** control restricts the network to up- or down-regulated proteins (by fold-change sign), and Ctrl/Cmd-click adds a protein and its interactors to the current selection so a gene can be dragged together with its neighbours.

### Changed

- **Figure-style and PPI settings now apply on a normal Run.** Edits made in the Figure Style panel — including the STRING score threshold and hub-label count — were previously honoured only by Save or Regenerate figures; a plain Run dropped them. They are now persisted at the start of every run.
- **The contrast dropdowns follow the configured factor.** The numerator/denominator/reference menus now read the levels of whatever contrast factor the design uses, instead of a hardcoded `condition` column, so a custom factor populates them correctly.
- **Interface polish.** New tooltips throughout the project, input, workflow, resources, checks, and run tabs; the Extended-QC (RSeQC) toggle greys out under Salmon (which has no genome BAM for it to read); the launched-command box is read-only; and the microarray-source selector is disabled and explained when the project uses a locally uploaded expression matrix.

### Fixed

- **Stale per-study results no longer carry over between meta-analysis reruns.** The per-study tables and saved transforms are undeclared side-effect files that Snakemake never cleans up, so dropping or renaming a study left its old outputs on disk, and the on-disk study discovery would resurrect the removed study. The meta step now clears the previous `per_study_*` files before rewriting.
- **Per-ontology GO figures no longer show misleading placeholders on a g:Profiler run.** The GO dotplots now switch on the enrichment backend, so a run without a Bioconductor annotation package reads "not run for this organism" rather than the misleading "nothing passed the threshold".

## 0.22.0 — 2026-07-11

### Added

- **A volcano-plot y-axis scale.** A marginal gene with an extremely small p-value used to sit at the very top of the volcano regardless of the others; the new scale option (cap / full / square-root) lets it show at its true height. The `cap` default reproduces the previous plot exactly.
- **Resume an interrupted run after closing the app.** Reopening a project whose run was stopped or interrupted now shows a Run Monitor banner that unlocks and continues from where it left off, reusing the completed steps instead of restarting.
- **Sample-label declutter on the heatmaps.** The "Show per-sample labels" toggle now also hides the sample columns on the differential-expression, up/down, genes-of-interest, and GSVA heatmaps, so a many-sample run is readable.

### Fixed

- **A comprehensive audit closed sixteen issues** (one blocker, five major, ten minor). Enrichment-figure draw errors now degrade to a placeholder instead of aborting the rule (a two-term emap plot could crash on a zero-dimension viewport). The results report now waits for the figures step, so it no longer embeds a stale or blank figure on Regenerate or on a fresh multi-core run. Reference validation reads gzipped FASTA/GTF instead of failing them; a local-microarray upload's source is no longer overwritten; the resume banner no longer strands on a blocked approval gate; a GSVA pathway with zero variance no longer crashes clustering; the custom-enrichment figures inherit the project palette and font; and the run's saved-settings diff now covers every configuration key. `WORKFLOW_VERSION` is bumped so existing projects re-sync the fixed scripts.

## 0.21.1 — 2026-07-11

### Fixed

- **The multi-study joint design no longer crashes on an interaction formula.** When a run combined two or more studies and the differential-expression design was an interaction model (for example `~ batch * condition`) whose covariate is aliased with study-of-origin, the automatic study-covariate injection built a rank-deficient design that DESeq2 rejects, aborting the run. The joint design now auto-injects `dataset` only into an additive design whose terms are all plain factor columns; an interaction design, or one where the covariate already spans study, is kept unchanged with a warning that study-of-origin is not modelled in the joint fit (the per-study meta-analysis still handles studies separately). The default `~ condition` and additive `~ batch + condition` cases are unchanged.
- **Regenerate figures no longer fails after a sample sheet is edited down to a single study.** With leftover `results/meta/` outputs from an earlier multi-study run and the Multi-study meta-analysis switch still on, Regenerate figures could force meta-analysis rules that are no longer defined for a single-study sheet (or for a microarray / uploaded-DE-results input), aborting the run. The comparative-figure and cross-study-report targets are now gated on the current run mode exactly as the pipeline defines them.
- **The app and the pipeline now agree on the study-of-origin name and confounding checks.** The app flags an unsafe `dataset` name (spaces, slashes, other path characters) only on a genuine multi-study sheet — matching the pipeline's own gate — and no longer shows a redundant meta-analysis warning next to the hard confounding failure when the two compared groups are fully split across studies. A freshly fetched multi-study sheet whose conditions are still unset no longer produces a spurious confounding failure.

### Changed

- **Clearer sample-sheet guidance.** The metadata note after an SRA/ENA fetch now states that the condition grouping is only a suggestion and must be reviewed on the Metadata tab before running, because it sets the differential-expression contrast. A multi-study sheet that does not yet have at least two studies with both compared groups replicated warns that the meta-analysis will not run. A failed ENA lookup caused by no internet connection now shows a plain, actionable message instead of a raw network error.
- **Interface polish.** Long tab labels elide with an ellipsis instead of clipping; the Outputs figure-style panel is wider so the per-figure override table fits without horizontal scrolling; the review-approval checkbox wording is clearer; the PPI network viewer no longer flashes white on first paint when the app is in dark mode.
- **Check Environment load-tests the meta-analysis R stack.** The environment probe now loads `metaRNASeq`, `metafor`, and `HTSFilter` so a broken meta-analysis install is caught before a run rather than partway through it.

## 0.21.0 — 2026-07-10

### Added

- **Multi-study meta-analysis.** Two or more independent studies of the same comparison — a `dataset` (study-of-origin) column in the sample sheet with more than one study, and the new **Multi-study meta-analysis** switch on the Workflow Settings tab — can now be combined in a single differential-expression meta-analysis. Each study is analysed on its own with DESeq2 and filtered independently (HTSFilter), so a difference in platform, library preparation, or sequencing depth stays inside that study; only the per-study statistics are combined. The p-values are combined with `metaRNASeq`'s replicate-weighted inverse-normal method and the unshrunken log2 fold changes are pooled with a `metafor` effect-size model (fixed-effect for two studies, DerSimonian-Laird random-effect for three or more, reporting between-study heterogeneity I²/τ²). Because the inverse-normal combination is directionless, a gene whose fold-change direction disagrees across studies is flagged as discordant and never reported as a cross-study hit — a convergent meta-DEG is significant on the combined FDR and consistent in direction in every study. A dedicated, self-contained **cross-study report** (`meta_analysis_report.html`) shows the convergence/divergence summary, a meta-volcano, per-gene forest plots, a cross-study log2FC concordance scatter, a convergent-gene heatmap, and a `compareCluster` dotplot contrasting each study's up/down sets with the convergent sets; the convergent-gene table, a per-study differential-expression summary, and the cross-study enrichment table are written to `results/meta/` and appear in the Outputs tab. The comparative figures restyle with **Regenerate figures** like the rest, and a `comparative_meta` group is available in the per-figure style overrides. The joint DESeq2 that runs alongside automatically models study-of-origin as a covariate (a default `~ condition` design becomes `~ dataset + condition`, with a rank-deficiency fallback), and a hard sanity gate blocks a design in which the compared groups are perfectly confounded with study (no single study contains both arms). Studies must share one organism and gene-identifier namespace; a mismatch is flagged before the run. Validated on simulated ground truth (96% sensitivity at ~1% empirical FDR, null type-I control, discordant flagging, random-effect heterogeneity recovery) and on two independent human glucocorticoid studies that recover the conserved FKBP5/KLF15/TSC22D3/DUSP1 signature convergently.

### Changed

- **Uploaded count matrices are now checked for normalized data before ingestion.** A gene-by-sample matrix whose values are mostly non-integer is TPM/FPKM, log-CPM, RMA, or otherwise normalized data that the DESeq2 count model cannot use. On import the app now detects this and asks whether the values are RSEM/tximport estimated counts (which it rounds and accepts) or normalized data (which it declines, with guidance to re-export raw counts); a matrix whose columns sum to ~1,000,000 is identified as TPM specifically. Zero-inflation-aware, so a sparse normalized matrix is no longer mistaken for counts.

## 0.20.1 — 2026-07-10

### Fixed

- **Functional-enrichment tables now appear for non-model organisms.** On the g:Profiler route (organisms without a Bioconductor annotation package) the GO over-representation table rendered empty because the g:Profiler output uses different column names (`term_name`, `p_value`, `intersection_size`) than the clusterProfiler route; the report now maps both, so the table populates. An enrichment category that ran but found no significant terms now says so plainly instead of the block silently disappearing.

### Changed

- **HTML report refinements for end users.** Gene tables right-align their numbers to match the enrichment tables, and `baseMean` shows as a rounded count (e.g. 1,234) instead of scientific notation, with sorting still keyed to the exact value. A print / Save-as-PDF stylesheet opens every collapsible section, lets wide tables wrap instead of clipping, keeps figures with their captions, and preserves the direction and status colours on paper. Sortable table headers are now operable by keyboard and announced by screen readers, with a visible sort cue before the first click. Figure-group titles join the document outline, table headers carry column scope, the figure-zoom viewer is a proper focus-managed dialog, and focus outlines meet the contrast minimum. The study design now records the multiple-testing method (Benjamini-Hochberg) and, when the sample sheet allows it, the per-group replicate counts.

## 0.20.0 — 2026-07-10

### Added

- **Check Environment now installs and repairs the WSL Linux distribution, not just WSL itself.** The app only checked that `wsl.exe` existed, then assumed Linux was ready — so a machine where WSL2 is installed but the distribution is missing or its virtual disk is gone (nothing to run the tools in) slipped through as "WSL ready" and every step silently failed. Check Environment now actually starts the default distribution to confirm it works, and shows an **Install Ubuntu distribution** action when it does not. Installing provisions Ubuntu non-interactively (`wsl --install -d Ubuntu --no-launch`), sets it as the default, and confirms it starts; a distribution that is registered but will not start is reported with the exact manual commands to reinstall it (never deleted for you). The environment check re-opens on the next launch until a working distribution is present.

### Fixed

- **The working directory no longer fills with a WSL error message when the Linux distribution is broken.** When WSL could not start its distribution, the app read `wsl.exe`'s own error text as the Linux home directory and pasted it into the Working-directory field as an unusable path. It now requires the command to succeed and return an absolute Linux path, so a broken WSL yields no auto-filled path instead of a garbage one.
- **Unexpected-error logs now record where the error happened.** The crash log could capture only an exception's name with no traceback (a Qt-boundary error routed to the handler without one); it now recovers the traceback from the exception itself, so `%LOCALAPPDATA%\BulkSeq Studio\logs\error.log` shows the file and line.

## 0.19.4 — 2026-07-09

### Fixed

- **"Open existing project" now starts where your projects actually live.** The picker opened in the app's install/AppData folder (the process working directory), which was especially confusing for projects created on the WSL filesystem. It now opens in the folder you last opened a project from, falling back to the current working directory — which on WSL is the WSL-native project location the app auto-detects — so a project generated in WSL is right there.
- **The harmless "package ... was built under R version 4.5.3" warning no longer prints on every R step.** Many conda R packages are now built under R 4.5.3 while the environment pins the protocol's r-base 4.5.2; the r45 ABI is stable, so they load and run correctly, but the load-time warning was noisy in the run log. It is now muffled in the analysis R scripts (only that exact warning — real warnings still surface). r-base is deliberately kept at 4.5.2: moving to 4.5.3 would force salmon off the benchmarked 1.10.3 onto the salmon 2.x Rust rewrite, changing quantification.
- **The Check Environment window now follows the app's light/dark theme.** It was a fixed light dialog regardless of the theme toggle; its colors (background, cards, text, status pills, buttons) now come from the active palette, so it matches the rest of the app in dark mode.

## 0.19.3 — 2026-07-09

### Fixed

- **Check Environment now detects a broken R stack, not just a missing one.** The environment check load-tests the R/Bioconductor packages (it actually loads each) instead of only checking they are present, so a stack that is installed but will not load — a dropped GO.db, or an R update that left the packages incompatible — now shows the R card as needing attention instead of a false "ready". The load-test runs off the UI thread with a generous timeout, so a slow first check never freezes the window.
- **The environment check re-opens after an app update, and keeps nudging a broken environment.** The first-run environment prompt is now tied to the app version and clears itself whenever the R stack is found broken, so updating the app re-surfaces a carried-over broken environment instead of silently keeping it. Previously the prompt fired once ever, so an environment that broke between versions was never re-checked — the direct cause of "the same error again" after an update.
- **A run that fails because the R environment can't load now offers a one-click fix.** When a run stops with an R load error (a dropped GO.db or an incompatible package), the app recognizes it as an environment problem — not a data or design error — and offers to open the environment check to rebuild from the pinned lockfile. Generic setup, contrast and download errors are excluded, so the offer only appears when a rebuild is the right fix.
- **A load-broken R stack is routed to a clean rebuild, and the microarray check load-tests GEOquery.** The environment guidance now sends a stack that will not load to a clean rebuild from the lock (an in-place install cannot repair an ABI-inconsistent stack), and the microarray run check now also load-tests GEOquery so a microarray run whose environment is missing it fails fast with a clear message instead of dying raw during GEO ingest.
- **Removed conflicting duplicate pins from the environment lockfile.** numpy, pandas and python-dateutil were pinned in both the conda and pip layers, letting pip silently overwrite the conda build; the redundant pip pins are gone. New tests keep the lockfile a superset of the environment spec, forbid conda/pip double-pins, and keep the GO.db enrichment cluster present in every environment guard so these gaps cannot silently return.

## 0.19.2 — 2026-07-09

### Fixed

- **The environment install/repair no longer drops packages, and it now verifies itself.** The `bulkseq` environment is created from the pinned lockfile (`bulkseq.lock.yaml`) instead of re-solving the floating spec — a re-solve is what silently dropped a transitive dependency like `GO.db` and left clusterProfiler unable to load. After installing, the setup load-tests the R/Bioconductor stack (DESeq2, limma, clusterProfiler, GO.db, DOSE, enrichplot, fgsea, STRINGdb) and, if anything fails to load, does one clean rebuild from the lock — so setup can no longer report success while leaving a broken enrichment stack behind. The floating spec is kept only as a fallback for a build removed from the channels or a non-linux host.
- **Check Environment now catches a broken enrichment/PPI stack before a run starts.** The environment check probes GO.db, DOSE, enrichplot, fgsea and STRINGdb (the clusterProfiler enrichment cluster and the STRING PPI package) in addition to DESeq2/limma, so a missing or dropped package is flagged from the Check Environment button rather than only when a run reaches the enrichment step ~30 minutes in.

## 0.19.1 — 2026-07-09

### Fixed

- **Enrichment no longer dies ~30 minutes into a run when `GO.db` is missing.** `GO.db` (the GO term database, a hard dependency of clusterProfiler / DOSE / enrichplot) could be dropped from a solve of the `bulkseq` environment, leaving clusterProfiler unable to load and the run failing at the enrichment / DE-vs-gene-set overlap step with `there is no package called 'GO.db'`. It is now pinned explicitly in the environment spec so a solve can never omit it.
- **The project environment check now load-tests the core R/Bioconductor stack.** Before a run starts, the setup check loads (not just looks for) DESeq2, limma, clusterProfiler, GO.db, DOSE, enrichplot, fgsea, and the figure/network packages. A missing package, or one left binary-incompatible by an `r-base` drift off the pinned 4.5.2 (which alone breaks compiled Bioconductor packages), now fails immediately with a clear message and a recovery command instead of wasting minutes of alignment and counting first.

## 0.19.0 — 2026-07-08

### Added

- **Extract an enrichment term's genes.** A new "Enrichment Terms" tab (Outputs) lists the terms from a finished run's GO/KEGG enrichment. Pick one and its member genes are pulled into a sortable table with their full DESeq2 statistics (fold change, adjusted p, base mean) — instantly, from the existing results. A second button builds a focused, z-scored heatmap (and per-condition expression panel) for just those genes, reusing the genes-of-interest machinery with no re-alignment or re-analysis. It resolves the term's genes across routes — GO symbols and KEGG NCBI gene ids (via a new `id_map.csv` bridge) — and gracefully handles the cases it can't: a g:Profiler run (which records no per-term gene lists) or a DESeq2-results upload with no expression matrix (the table still works).
- **Self-calibrating runtime estimate.** The runtime estimate now learns your machine's real speed. After each compute-heavy local run it records the predicted-versus-actual time and stores a per-machine correction factor (by host and core count); future estimates apply it and narrow their range as the app gains data, with a plain confidence note ("Uncalibrated…", "Rough — based on N past runs", "Calibrated to this machine"). Network-bound SRA/GEO downloads and the fast alignment-free modes are excluded, so download variance and workload shape never distort the learned hardware speed.

### Fixed

- **A DESeq2-results upload no longer shows an inflated runtime estimate.** It was mis-modeled as a full alignment run; it is now correctly treated as an alignment-free, near-instant path.

## 0.18.3 — 2026-07-08

### Added

- **Per-figure-group style overrides.** The figure style controls gain an override table where each figure group — Core figures (PCA, volcano, MA, heatmaps), Sample-correlation heatmaps, Enrichment plots, and the PPI network — can set its own palette, font, point size, base font size, and size (width/height), independently of the global settings. Every cell defaults to *inherit*, so figures stay uniform unless you deliberately change one group. (This generalizes the per-figure palette added in 0.18.1.)
- **Rebuild the environment from scratch.** The Check Environment dialog gains a "Rebuild from scratch" button that deletes the `bulkseq` environment and recreates it cleanly (re-downloading the tools and the R/DESeq2 stack). Updating an environment in place across versions can leave the R/Bioconductor packages inconsistent — R base moves but packages built against the old R do not — which makes the first R step (for example the microarray GEO ingest) fail on load while every earlier step still runs. A clean rebuild restores a self-consistent stack. The setup script honors `BULKSEQ_REBUILD=1` for the same effect on the command line.

## 0.18.2 — 2026-07-08

### Fixed

- **A failed run is no longer reported as "Completed".** On Windows the pipeline runs through `micromamba run`, which returns a success code even when Snakemake failed, so a run that errored could still show green. The app now also watches Snakemake's output for a definitive failure ("Error in rule", "WorkflowError", a job that exited because a step failed, or a missing-output error) and marks the run failed regardless of the masked exit code, pointing you at the error line and the rule's log.
- **Microarray GEO ingest fails loudly with the reason.** When the GEO ingest step could not produce its outputs it previously ended with an empty log and the confusing "job completed successfully, but some output files are missing". It now prints its progress and, on any failure, the actual cause (empty accession, a GEO download that returned nothing, a record with no expression matrix, or an unwritten output) to the run log, and records a FAILED check — so the problem is visible instead of silent.

## 0.18.1 — 2026-07-08

### Added

- **Per-figure palette.** The figure style controls gain an optional per-group palette: Core figures (PCA, volcano, MA, heatmaps), Sample-correlation heatmaps, Enrichment plots, and the PPI network can each use a different palette, or "Global" (the default) to follow the main palette. Figures stay uniform unless you deliberately differ one.

### Fixed

- **Enrichment dot plots now honor the palette.** The GO/KEGG/disease-ontology dot plots mapped significance to the fill aesthetic, but only a colour scale was applied, so they silently kept enrichplot's default red-blue instead of the configured palette. Both aesthetics are now set, so the dot plots match the rest of the enrichment figures (and the per-figure palette above).

## 0.18.0 — 2026-07-08

### Added

- **Sortable Outputs table.** The table viewer on the Outputs tab is now click-to-sort by any column, and numeric columns (log2 fold change, adjusted p, base mean) sort in true numeric order rather than as text. The preview still shows the first 200 rows.
- **Gene symbols in italic (default on).** Gene symbols now render in italic — the HGNC convention — on the volcano labels, the DEG and genes-of-interest heatmap rows, the STRING PPI network, and the results-report differential-expression tables. A "Italicize gene symbols" toggle in the figure style controls turns it off.
- **Declutter per-sample labels.** A "Show per-sample labels on PCA and sample heatmaps" toggle hides the per-sample text on the PCA, sample-distance, and sample-correlation figures — useful when a run (a microarray series in particular) has too many samples to label legibly.
- **PPI network: italic labels and click-to-focus.** The interactive PPI network gains two toggles: italic gene labels, and "Focus labels on click" — clicking a protein now shows only its own and its interactors' labels and hides the rest of the network's names, so a dense network stays readable.

### Fixed

- **The color palette now applies to every enrichment figure.** The gene-concept network (cnetplot) and term-similarity map (emapplot) used enrichplot's built-in gradients and ignored the configured palette; they now follow the project palette like the dot/ridge/GSEA plots.
- **Figures scale with the data instead of crowding.** Heatmaps pinned their width regardless of sample count, so a many-sample run crushed columns into unreadable slivers and overprinted the sample labels. The top-DEG, up/down, genes-of-interest, sample-distance, and sample-correlation heatmaps now size their canvas from both the row and the sample count (with a legibility floor and a cap), so a large study stays readable.
- **PPI rebuild now honors the confidence you set.** "Rebuild from STRING…" read a score control on a different tab, so changing the confidence next to the button and clicking Rebuild produced the same network. The rebuild score now sits next to the button and drives the rebuild, and the STRING interactions are filtered explicitly by the combined-score threshold.
- **Clearer PPI controls.** The PPI panel is reorganized into a view-filter row, a rebuild row (with the score next to the button), and an export row, so it is obvious which control does what.

## 0.17.2 — 2026-07-08

### Fixed

- **First-run environment setup recovers from a corrupted package cache.** On a fresh machine the Check Environment install could die repeatedly with `parse error ... attempting to parse an empty input` and never finish. This happens when a micromamba shard-cache JSON is left empty or truncated by an interrupted or concurrent download; every later run then re-reads the same empty file and fails at the same point. The setup script now detects a failed environment step, clears only the index/shard cache (leaving downloaded packages in place so the retry is fast), and runs the step once more — turning a permanently stuck install into a self-healing one.
- **Only one environment setup runs at a time.** Two setups resolving at once could both write the shared shard cache without holding micromamba's transaction lock and leave the truncated JSON above. The setup script now takes a single atomic lock for the whole run (portable across WSL, native Linux, and macOS), so a second invocation waits for the first instead of racing it; a stale lock from a dead process is reclaimed automatically. On Windows the GUI also reuses an already-open Check Environment window instead of opening a second one, closing the path where a first-run auto-open plus a manual click started two installs.

### Changed

- **Plainer environment-check details.** The details/log panel of the Check Environment dialog now groups items into Needs attention / Optional / Ready with plain labels, instead of a dense list of raw `REVIEW_REQUIRED:` status tokens with nested parentheses.

## 0.17.1 — 2026-07-08

### Added

- **Upload a local microarray matrix — no GEO accession needed.** Microarray mode gains an "Upload a local microarray matrix" button next to the GEO fetch: point it at your own gene × sample expression matrix (first column gene ids or symbols, one column per sample; TSV or CSV, already-normalized log2 intensities) and it runs the same limma → figures → enrichment → report path as a fetched GEO series, with no download. It is the microarray counterpart of "Use a Count Matrix" and handles processed array data from any platform. On identical data it reproduces the GEO-route result exactly (validated on the Arabidopsis hub2-3 set: 154 differentially expressed genes either way).
- **First-run environment check.** On the first launch after install, BulkSeq Studio now opens the Check Environment dialog automatically (once), so a missing tool — for example the R/DESeq2 stack behind an "exit 127" — is caught up front rather than partway through a run. The Check Environment button reopens it anytime.

## 0.17.0 — 2026-07-08

### Added

- **Mode-aware Workflow Settings.** Selecting a microarray, count-matrix, or DESeq2-results input now greys out the settings the run ignores — aligner, quantifier, read trimming, rRNA filtering, contamination screen, RSeQC, and organellar-gene handling; the differential-expression engine for microarray (which uses limma-trend) and for results-upload (which skips DE); and GSVA for results-upload — so the Workflow Settings tab shows only the controls that actually apply. This is a UI-only change: those settings were already ignored by the workflow in these modes, so nothing about a run changes; it just stops the interface implying an aligner or trimmer is used for intensity data.
- **Affymetrix raw-CEL route in the GUI.** Microarray mode now exposes a processing selector: the GEO series matrix (submitter-normalized, the default and correct for the large majority of datasets) or Affymetrix raw CEL → RMA re-normalization, plus a log2-transform choice (auto-detect / force / off). The raw-CEL route downloads the GEO supplementary archive and re-normalizes with `affy::rma`; it needs the full R environment.
- **GEO platform in the outputs.** Microarray runs now record the GEO platform (GPL id) and series accession in the run summary, the tools-and-references export, the study-design export, and the results report, so the array platform is visible in the provenance rather than only inside the normalization log.
- **Sixteen new organism presets.** Added rat, chicken, pig, cattle, grape, cotton, *Medicago truncatula*, tobacco, *Zymoseptoria tritici*, *Ustilago maydis*, *Sclerotinia sclerotiorum*, *Aspergillus niger*, *Pseudomonas aeruginosa*, *Mycobacterium tuberculosis*, *Staphylococcus aureus*, and *Plasmodium falciparum* to the reference catalog (46 presets total). Each entry's genome and annotation URLs were checked to resolve, and its KEGG code, STRING taxon, and Bioconductor OrgDb (where one exists) were verified, before inclusion. Vertebrate models use Ensembl release-111 with the matching `org.*.eg.db`; plants, fungi, bacteria, and the parasite use NCBI RefSeq.
- **Dual-audience results report.** `results_report.html` is redesigned as a guided "Read-Along" report that serves both a non-specialist and a bioinformatician from one file: each section opens with a plain-language finding (a templated key-findings summary — what was compared, how many genes changed and in which direction, the strongest genes) with the statistics glossed inline and collected in an end glossary, above the full tables and figures with their exact numbers. The overview is a compact row of headline stat chips plus the run cards (no more crowded card stack); figures are grouped and lettered (Quality / Differential expression / Function) with a plain caption and a technical caption each; the click-to-open, zoomable figure gallery is preserved. Still a single self-contained file that opens offline in any browser.

### Changed

- **Readiness check verifies the microarray R stack.** The R-package probe now includes `GEOquery` and `affy`, and the R/DESeq2 readiness card is marked ready only when both the `Rscript` binary and the required Bioconductor packages are present — so an environment that has R but is missing the differential-expression or microarray packages is flagged instead of appearing ready. This closes a path where a partially-installed environment looked green and then failed a run with a command-not-found (exit 127) at the first R step.
- **`WORKFLOW_VERSION` bumped to 0.17.0**, so existing projects re-copy the bundled workflow on open and pick up this release's script fixes.
- **Decimal point is always a dot, regardless of the OS/BIOS language.** On a comma-decimal locale (Turkish, German, and others) the interface, the pipeline tools, and R would otherwise use a comma, which silently corrupts numeric settings (`alpha`, `|log2FC|`) and can write `0,05` into result tables that are then misread. The app now forces a dot decimal separator in every numeric field, exports `LC_NUMERIC=C` to the workflow so R and the command-line tools emit dots, normalizes a comma that was hand-edited into a config back to a dot on load, and shows a gentle notice when it finds one — so a project behaves identically on any machine.

### Fixed

- **Live WSL readiness probes now report accurately.** The environment probes issued complex shell commands (a bash array and loop, nested command substitution) that did not survive the `wsl -- bash -lc` round-trip through `wsl.exe`, so a fully-installed environment could probe as "not found" and fall back to a stale install log — which, if that log was from a differently-named build environment, wrongly reported every tool as present. Commands are now passed base64-encoded and decoded inside WSL, so they run exactly as written and each tool/env/R-package check reflects the real environment; the install-log fallback additionally only trusts paths recorded for the same environment name.
- **GEO metadata no longer produces a spurious "nan" group.** When a GEO series gives different samples different characteristic keys (or a sample simply lacks one), the missing cells were written to `samples.tsv` as the literal string `nan`, which became a bogus factor level if that column was used as the contrast or a design covariate. Missing characteristics are now blank.
- **edgeR ranking metric is signed.** The edgeR engine wrote the unsigned quasi-likelihood F-statistic into the `stat` column, so the preranked-GSEA export put strongly up- and down-regulated genes together at the top. It now writes a signed statistic (`sign(logFC)·√F`), restoring direction in the ranked list.
- **Design covariates are validated.** The limma, limma-voom, and edgeR engines silently dropped a design-formula covariate that was not a sample-sheet column and ran an unadjusted (confounded) model with no warning. A missing covariate now fails the run with a clear message, matching DESeq2's behavior.
- **GSVA runs on the correct data scale.** GSVA scored samples from the linear DESeq2 normalized counts with a Gaussian kernel that assumes log-scale input. It now reads the log-scale variance-stabilized/expression matrix (VST for DESeq2, log-CPM for voom/edgeR, log2 intensity for microarray), so the per-sample pathway scores are valid on every route.
- **Organellar filtering works on the STAR gene-counts route.** Discard/separate handling of mitochondrial and chloroplast genes did nothing when STAR's own `--quantMode GeneCounts` table was used (its chromosome field is `.`), leaving organellar genes in the matrix and skewing size-factor normalization. The GTF fallback now runs for that route.
- **PPI hub centrality uses edge confidence correctly.** STRING betweenness treated the combined-score similarity as a distance, so high-confidence edges counted as the longest paths and centrality routed around the true hubs; the weight is now inverted.
- **PCA no longer crashes with many groups.** The PCA plot used a fixed 5-colour palette, so a contrast factor with more than five levels (for example a multi-group GEO series) aborted the whole figures step. The palette is expanded to the number of groups.
- **RiboDetector no longer aborts on read-length estimation.** The `zcat | head` read-length step died with SIGPIPE under Snakemake's default `pipefail`, failing the rule before RiboDetector ran; `pipefail` is now disabled just for that sub-shell.
- **Affymetrix CEL sample names match the sample sheet.** On the raw-CEL route the RMA column names kept the `GSM…_descriptor` form and never matched the bare `GSM` accession in `samples.tsv`, aborting ingestion; they are now reduced to the accession.
- **Genes-of-interest matching is version-safe.** The genes-of-interest heatmap stripped everything after the first dot on both the gene list and the matrix row names, which could collapse distinct dotted identifiers (for example versioned Ensembl ids) onto a shared prefix and match the wrong gene. Matching now uses the full id first and falls back to version-stripping only for Ensembl ids.
- **Symbol-keyed enrichment keeps `LOC` symbols.** The NCBI `LOC`-prefix strip (for gene-id routes) was applied on the SYMBOL route too, mangling legitimate gene symbols such as `LOC101927877` in microarray runs; it is now skipped for SYMBOL-keyed runs.
- **Wilcoxon diagnostic tolerates all-missing groups.** A gene with an entirely-missing group in the microarray intensity matrix crashed the Wilcoxon sensitivity step; non-finite values are now dropped per group and such genes return NA.
- **Locally-selected FASTQ paths run under WSL.** FASTQ files picked from the file dialog were written to `samples.tsv` as Windows paths, which a WSL run cannot resolve; they are now translated to `/mnt/<drive>/…` when WSL execution is selected.
- **Removed two dead configuration fields** (`workflow.custom_gene_list_analysis`, `featurecounts.count_read_pairs`) that no part of the workflow read.

## 0.16.0 — 2026-07-01 (revised 2026-07-02)

### Added

- **Design pre-check stops bad contrasts in seconds.** If the reference level or a contrast's numerator/denominator does not match any value in the sample sheet's condition column, the run now fails at the first validation step with a plain message (e.g. "The design uses 'control' for 'condition', but the sample sheet has no such value. Available condition values: MUT, WT"), instead of running download, trimming, alignment and counting and only then crashing at DESeq2 with "'ref' must be an existing level".
- **Fonts bundled for the figures.** A serif/sans/mono font set (`font-ttf-dejavu`) plus fontconfig setup (`fonts-conda-ecosystem`) are now part of the pipeline environment, and a resolver maps a configured Windows font name (e.g. "Times New Roman") to an installed serif when that exact font is absent — so a serif choice renders as a serif rather than silently falling back to a sans default, on any machine.
- **Alternative differential-expression engines.** Alongside the default DESeq2, RNA-seq counts can now be tested with **limma-voom** or **edgeR** (quasi-likelihood F-test). All three engines emit the same result schema (`deseq2_results.csv`, up/down gene lists, figures), so downstream enrichment and PPI steps are identical regardless of engine. Concordance with DESeq2 is high (fgval Jaccard ≈ 0.94 for limma-voom, direction agreement 100%); see benchmark B14.
- **Alternative read trimmers.** The trimmer is now selectable: **fastp** (default), **Trim Galore**, or **Trimmomatic**. Each exposes its own parameters in the GUI.
- **Alternative rRNA removal.** rRNA filtering can use **SortMeRNA** (default, reference-based) or **RiboDetector** (reference-free, machine-learning). RiboDetector runs on CPU and needs no rRNA reference database.
- **Contamination screening.** Optional **FastQ Screen** step maps a read subsample against a panel of reference genomes to flag cross-species or adapter/vector contamination before alignment. It runs against a FastQ Screen config you point it at (Advanced parameters → Contamination: FastQ Screen config); it does not auto-download a genome panel. If screening is enabled without a config, it is skipped and the sanity check flags it.
- **Single-end FASTQ support.** The FASTQ route now accepts single-end libraries end to end (trimming, rRNA filtering, STAR/HISAT2/Salmon, featureCounts). Mixed single- and paired-end samples in one project are rejected with a clear message.
- **GSVA pathway-activity module.** Optional per-sample gene-set variation analysis on the normalized expression matrix, with a sample-by-set activity heatmap. Organism-safe: it runs only on user-supplied gene sets, so it works for any organism.
- **RSeQC alignment QC.** Optional read-distribution and gene-body-coverage reports from aligned BAMs (BED12 derived from the annotation with `gtfToGenePred`/`genePredToBed`).
- **Redesigned self-contained HTML report.** `results_report.html` is restyled to match the documentation site (logo, version chip, summary cards, footer links to the repo/releases/docs) and now embeds every figure as a **zoomable SVG** (click to open full size, sharp at any magnification; dense scatter figures fall back to PNG), shows **up- and down-regulated genes in separate tables**, renders **functional enrichment as GO/KEGG tables** (top terms by adjusted p-value) instead of a raw text dump, reports **per-step runtimes**, and lists the sanity checks as colour-coded status badges. Still a single file that opens in any browser with no external assets; reachable from the GUI via **Open Results Report**.
- **Separate up- and down-regulated top-DEG heatmaps.** The figures step now also produces `top_upregulated_heatmap` and `top_downregulated_heatmap` (top genes by significance within each direction) alongside the combined top-DEG heatmap, in every mode. The Outputs tab table picker can preview the up- and down-regulated gene lists separately.
- **Guided design/covariate builder (GUI).** A dialog reads the sample metadata columns and helps assemble the design formula and contrast (for example adding a batch covariate), instead of typing the formula by hand.
- **Advanced parameters panel (GUI).** A collapsible per-tool section exposes the important parameters of fastp, Trim Galore, Trimmomatic, SortMeRNA, RiboDetector, and the aligners for manual tuning; defaults are unchanged.
- **fastp poly-X trimming** is now an exposed option (`--trim_poly_x`).
- **Repair environment button (Check Environment window).** A persistent button reinstalls/updates the full bioinformatics environment (all tools plus the R/DESeq2 stack), for the case where a run reports a tool as missing even though the per-card status looks ready — for example an environment that predates a later tool. It runs the same setup used for a fresh install, in WSL on Windows or natively on Linux.
- **Estimated download size.** The runtime estimate now reports how much will be downloaded before a run when the input is an SRA/ENA accession (approximate gzipped FASTQ size from the ENA base counts), so the network cost is visible up front. Local-input and count-matrix routes report that no read download is needed.
- **Faster, verified, resilient FASTQ downloads.** SRA/ENA downloads now use **aria2** (4 connections per file, with at most 3 files downloading at once) when it is available, falling back to a single stream otherwise — far faster than one throttled connection while staying under ENA's per-IP connection limit. Downloads **auto-retry with backoff**: ENA transiently refuses connections under load, and aria2's resume (`-c`) continues a partial file from where it stopped instead of restarting, so a transfer that reached 97% before a refusal finishes on the next attempt rather than failing the run. Each file is verified against ENA's published **MD5** after download, so a truncated or corrupted transfer is caught rather than silently used — the checksum result is reported in the results report (a data-integrity guarantee). aria2 was added to the pipeline environments.
- **Suggested condition from metadata.** The fetched `condition` column is pre-filled with a suggested experimental group (a GEO characteristic such as genotype/treatment, or a sample-title group) instead of `unknown`, chosen by a heuristic that skips donor/technical covariates; you confirm or edit it. ASCII-clean labels.
- **Edit WSL2 memory / CPU limits from the app (Windows).** A button on the Resources tab writes the WSL2 caps in `%UserProfile%\.wslconfig` and can restart WSL to apply them, so raising the RAM cap for STAR on large genomes no longer needs hand-editing the file.
- **Plain-text paste.** The SRA/ENA accession box and the gene-of-interest box now paste as plain text, stripping any source formatting.

### Changed

- **Pipeline environments** gained the new tools: `aria2`, `edger`, `trim-galore`, `cutadapt`, `pigz`, `trimmomatic`, `fastq-screen`, `bowtie2`, `ribodetector`, `gsva`, `rseqc`, and the UCSC `gtfToGenePred`/`genePredToBed` utilities. The pinned lock (`bulkseq.lock.yaml`) was regenerated; RiboDetector installs its CPU ONNX runtime (no CUDA). `r-base` is pinned to 4.5.2 in `bulkseq_full.yaml`: an environment update that bumps R leaves the compiled Bioconductor packages binary-incompatible (clusterProfiler and others fail to load), so it must not float, and the clusterProfiler floor blocks conda-forge's obsolete 3.x build from being selected.
- **Runtime estimate recalibrated** against real per-step benchmark data from five completed runs: alignment minutes-per-gigabase were roughly halved (the previous hand-set value over-charged alignment), QC/quant were tightened, and the range was widened on the high side. The SRA/ENA download is treated as a separate, network-dependent line item ("minutes to hours") rather than folded into the point estimate, because measured download time varied ~80× independent of data size.
- **Overview figure** (`figure1_overview`) was redrawn to show all three DE engines, the alternative preprocessing tools, single-end input, and the existing DESeq2-results-upload path that feeds enrichment, PPI, and figures without recomputing DE.
- **Validation** was extended with a human dataset (airway smooth muscle ± dexamethasone, GRCh38/Ensembl via the Salmon route): the run recovers the canonical glucocorticoid signature (FKBP5, ZBTB16, KLF15, SPARCL1 up; VCAM1 down), confirming the human/`org.Hs.eg.db` path end to end.
- **Documentation** now explains how to raise the WSL2 memory cap on Windows via `%UserProfile%\.wslconfig` (`[wsl2] memory=`), linking Microsoft's `.wslconfig` reference, since that VM cap (not the Windows host total) bounds memory-heavy steps such as STAR indexing and DESeq2.
- **Windows installer** detects an existing BulkSeq Studio install and offers to update or uninstall before continuing, instead of installing over the top. Updating first removes the old version completely (runs its uninstaller and deletes any leftover install directory) and then installs the new version fresh, so no stale files carry over. The setup wizard is branded with the BulkSeq Studio logo in place of the default Inno Setup artwork.
- **Linux AppImage** now embeds zsync update information and ships a companion `.AppImage.zsync` asset, so `AppImageUpdate BulkSeqStudio-<version>-x86_64.AppImage` upgrades in place from the latest GitHub release.

### Fixed

- **Runtime estimate now reflects the machine.** The pre-run estimate previously read the cores and RAM saved in the project config, which stay fixed until the user re-runs Detect + Save, so the same project reported the same estimate on every machine. The estimate now detects the local WSL2 cores/RAM (matching what the run will actually use) and estimates against them, and RAM only inflates the estimate when it is genuinely tight for STAR on a large genome. The volume floor is applied only when the sequencing volume is unknown, so for a sized dataset the estimate scales with core count instead of collapsing to a constant.
- **Optional-route tools not found even when installed.** The contamination screen (FastQ Screen), RSeQC, the alternative trimmers (Trim Galore, Trimmomatic), and the alternative rRNA filters (SortMeRNA, RiboDetector) could fail at run start with "<tool> is not installed" although the tool was present in the environment. The environment `bin` is now put on PATH in the parent Snakemake process so every rule shell and R script step inherits it, independent of how the run is launched; this also hardens the default STAR/featureCounts/DESeq2 rules against the same PATH-propagation gap.
- **Optional QC no longer breaks a run on an out-of-date environment.** RSeQC and the contamination screen are additive QC steps that do not affect counts or differential expression. If their tools are missing from the environment (for example on a machine whose `bulkseq` env predates these tools), the workflow now skips that step with a warning at DAG-build time instead of failing the whole run, so the core alignment/counts/DESeq2/figures/enrichment outputs still complete. Result-affecting choices (aligner, trimmer, rRNA tool, DE engine) still fail fast if their tool is missing.
- **RNA-seq GEO series (GSE) can now be fetched.** Pasting an RNA-seq GSE into the SRA/ENA box previously failed with "no linked SRA data" when the series linked its runs only through a BioProject (no explicit SRA relation). The resolver now falls back to the BioProject accession (which ENA accepts), so e.g. GSE280426 resolves to its runs; a genuine microarray series still gets a clear "use the GEO microarray fetch" message.
- **A metadata symbol no longer breaks a fetch.** A non-ASCII character in fetched metadata (e.g. a Greek delta in a GEO genotype) could raise `UnicodeEncodeError` on a Windows cp1252 console and, in the frozen build, silently swallow the error dialog. Stdout/stderr are now reconfigured to UTF-8 at startup, the excepthook is guarded, and metadata files are read/written as UTF-8.
- **Runtime timing phase mapping.** The per-phase runtime rollup now recognises the Salmon, Trim Galore / Trimmomatic, SortMeRNA / RiboDetector, and stats/network steps instead of bucketing them under "Other".
- **SortMeRNA version string.** The software-versions report now records the SortMeRNA version rather than its startup banner line.
- **Enrichment ridgeplot showed no ridges.** Long GO term labels (e.g. "maturation of SSU-rRNA from tricistronic rRNA transcript…") consumed the panel width and squashed every density ridge into an invisible sliver. The labels are now wrapped (the same wrap the dotplots use), so the fold-change distributions render fully.
- **Figure style now applies to the heatmaps and GSEA plot.** The sample-correlation (Pearson/Spearman) and sample-distance heatmaps (both `pheatmap`) and the GSEA running-score plot did not pick up the configured **font family**, so they looked inconsistent with the ggplot figures. The font is now propagated to all of them. (Note: the font must be installed in the WSL2/Linux pipeline environment; a font that is absent there — e.g. "Times New Roman" on a stock WSL — falls back to a default for every figure alike.)
- **Software & provenance versions cleaned.** Tool versions in the report showed full paths and banners (e.g. HISAT2 as `/home/.../hisat2-align-s version 2.2.2`); they are now reduced to the version number, and the Tools / R-Bioconductor tables gained a Name/Version header.
- **Report shows the fold-change threshold.** The results report's design card and the run summary now list the `|log2FC|` threshold alongside the FDR alpha.

### Changed

- **Results report polish.** The up- and down-regulated gene tables now show the **top 50** per direction (was 15) and are **sortable** (click a column header). The runtime panel lists the **machine the run executed on** (CPU model, cores/threads, RAM, OS) for reproducibility. The redundant "self-contained…" subtitle line was removed from the header.

### Removed

- **htseq-count** was dropped as a quantifier option; featureCounts, STAR gene counts, and Salmon/tximport cover the same ground.

## 0.15.2 — 2026-06-29

### Fixed

- **gffread / salmon / hisat2 not found in Snakemake shell rules.** The `make_transcriptome`, `salmon_index`, and `hisat2_index` rules now explicitly prepend `${MAMBA_ROOT_PREFIX}/envs/bulkseq/bin` to PATH at the start of their shell commands. Snakemake's subprocess environment does not reliably inherit the micromamba-activated PATH in all configurations; this makes the tool lookup independent of that inheritance, fixing the "gffread is not installed" error even when the binary is present in the env.

## 0.15.1 — 2026-06-29

### Fixed

- **Workflow Settings tab scaling.** Form fields (dropdowns, spinboxes, line edits) now expand to fill the full available width at any window or monitor size. Previously the Qt default field-growth policy (`ExpandingFieldsGrow`) left Preferred-policy widgets like QComboBox at their minimum hint, with empty space to the right on wide windows.
- **Setup readiness: Salmon/HISAT2 route tools warning.** When `salmon`, `gffread`, or `hisat2` are absent from the WSL bulkseq env, the Setup tab's recommended actions now explicitly call this out and direct the user to Install/Repair Core WSL Env. Previously only the STAR-route core tools triggered a repair prompt, so a machine missing gffread (needed by the Salmon route's transcriptome step) would show "Setup is ready" while Salmon runs would fail.

## 0.15.0 — 2026-06-26

### Added

- **STAR gene-counts quantifier.** With the STAR aligner the Quantifier control is now a real choice: `STAR_GeneCounts` takes gene counts from STAR's own `--quantMode GeneCounts` output (no extra counting pass), strand-matched to the run's inferred strandedness, instead of running featureCounts. The counts converge on the same matrix the rest of the pipeline expects — validated at Pearson r ≈ 0.998 (unstranded) to 1.000 (stranded) against featureCounts on the same BAMs. featureCounts remains the default; HISAT2 uses featureCounts and Salmon uses tximport.
- **Custom gene-set enrichment.** Supply your own gene sets — a GMT and/or an id→term annotation table, with an optional background list for the over-representation universe — to run a clusterProfiler ORA + GSEA alongside the built-in GO/KEGG, producing custom ORA/GSEA tables and a dotplot. It is organism-agnostic (no Bioconductor OrgDb needed), so it works where the built-in GO route is skipped (e.g. most fungi). The gene IDs must use the run's identifier format; a namespace mismatch is flagged (`REVIEW_REQUIRED`) rather than returned as a silent empty result. The built-in GO/KEGG enrichment is unchanged.

## 0.14.2 — 2026-06-26

### Changed

- **Genes of interest: clearer identifier guidance and a mismatch flag.** The focused-gene analysis (a z-scored heatmap, per-condition expression plots, a counts table, and — when PPI seeding is set to the gene list — a STRING network) matches the gene IDs you paste against the run's genes by locus tag, Ensembl/RefSeq ID, or symbol. When few or none match — usually because the IDs are in a different format than the run uses (for example gene symbols pasted into a locus-tag run) — the genes-of-interest report now leads with a clear warning and shows examples of the run's actual ID format so the list can be corrected. The Genes of Interest tab spells out the format requirement.

## 0.14.1 — 2026-06-26

### Changed

- **Removed dead configuration scaffolding.** Config fields that no rule or app code read have been removed, so the configuration no longer advertises behavior the pipeline does not perform: `workflow.repair_pairs` (BBMap repair, never implemented), `workflow.differential_expression` (the edgeR / limma-voom values were never wired — DESeq2 is selected by input mode), `sortmerna.enabled` (rRNA filtering is gated on `workflow.rrna_filtering`), and the STAR overrides `outSAMtype` / `quantMode` / `sjdb_overhang` / `genomeSAindexNbases` (the indexing rule computes these itself). Existing project configs that still carry these keys load unchanged — the stale keys are ignored.
- Removed the orphaned `STAR` block in `tool_defaults.yaml` and a progress-label entry for a "repair" rule that does not exist, and updated the README feature list to state that SortMeRNA rRNA filtering is implemented (it was previously listed as scaffolded).

## 0.14.0 — 2026-06-26

A full audit of the GUI controls and analysis scripts found no scientific-validity issues (the DESeq2 baseline reproduces exactly), and turned up two GUI controls that looked active but did nothing. Both now work.

### Added

- **Skip-trimming toggle.** The "fastp trimming" checkbox is now honored: unchecking it skips fastp entirely and sends the raw reads straight to the aligner (and to rRNA filtering, if that is on). Previously the box only changed the runtime estimate while fastp always ran. FastQC-before/after and the MultiQC inputs follow the same gating. Leave it on unless your reads are already trimmed.
- **GFF3 annotation support.** The annotation Format selector (gtf / gff3) is now honored: a GFF3 annotation is converted to GTF with gffread before indexing and counting, so STAR/HISAT2/Salmon and featureCounts get the GTF they expect. Previously selecting gff3 did nothing and a real GFF3 file was silently parsed as GTF, which produces wrong or empty counts. The GTF path is unchanged.

## 0.13.0 — 2026-06-26

### Added

- **rRNA filtering with SortMeRNA.** The "rRNA filtering" workflow option is now implemented (previously a no-op checkbox). When enabled, trimmed reads are filtered against the SortMeRNA rRNA database before alignment, on all three aligner routes (STAR, HISAT2, Salmon): the reference is downloaded and indexed once, each sample is then filtered in its own working directory, and the non-rRNA reads feed the aligner. The per-sample SortMeRNA log (rRNA %) is added to the MultiQC report, and `sortmerna` is now part of the core environment. A custom reference can be set via `sortmerna.database` (a local FASTA, a FASTA URL, or a database tarball URL); the default is `smr_v4.3_default_db`.

### Fixed

- **Rule guards added in 0.12.2 could abort their own rules.** The `command -v … || { … }` guards in `make_transcriptome`, `salmon_index`, and `hisat2_index` used unescaped braces, which Snakemake parses as format fields, raising a `NameError` and stopping the Salmon/HISAT2 routes even when the tool was present. The braces are now escaped. This slipped through in 0.12.2 because the guards were checked with `bash -n` after variable substitution rather than through Snakemake's own formatting.

## 0.12.3 — 2026-06-26

### Fixed

- **DESeq2 log-fold-change shrinkage could fail with a missing-package error.** `run_deseq2.R` falls back to `lfcShrink(type="ashr")` for contrasts apeglm cannot shrink, and `ashr` is also selectable via `deseq2.shrinkage_method`, but the `ashr` R package was in no environment profile. A config that requested ashr (or a default apeglm run that hit the contrast fallback) aborted after the model fit with a missing-package error. `r-ashr` is now in the full environment and the pinned lock, and `deseq2.shrinkage_method` is restricted to `apeglm`, `ashr`, or `normal` so an unsupported value is rejected when the config loads rather than mid-run. Default (apeglm) runs are unchanged.
- **GO enrichment for yeast, Arabidopsis, C. elegans, and zebrafish fell back to g:Profiler.** The enrichment step maps these organisms to the Bioconductor OrgDbs `org.Sc.sgd.db`, `org.At.tair.db`, `org.Ce.eg.db`, and `org.Dr.eg.db`, but those packages were not installed, so the native clusterProfiler GO route (GO over-representation, GO GSEA, and disease ontology) was skipped and the run quietly used the g:Profiler over-representation fallback instead. The four OrgDbs are now in the full environment and the lock, restoring the full GO route for these organisms.

## 0.12.2 — 2026-06-26

### Fixed

- **Salmon and HISAT2 aligner routes failed on a core-only environment.** The `bulkseq_core.yaml` profile installed by "Install / repair core environment" did not include `gffread`, `salmon`, or `hisat2`; those tools were only in the full R/DESeq2 profile. Selecting the Salmon or HISAT2 aligner with a core (or pre-0.11.0) environment ran through trimming and QC, then died mid-run with `exit status 127` (command not found) at `make_transcriptome`, `salmon_index`, or `hisat2_index`. The three tools are now part of the core profile, so every aligner route works with the core environment. Existing environments pick them up by clicking "Install / repair core environment" again (an additive `micromamba env update`).
- **Check Environment did not probe the alternative-aligner tools.** `gffread`, `salmon`, and `hisat2` were in none of the readiness probe lists, so a stale environment reported as ready and the problem only surfaced at run time. They are now probed and shown. The "core ready" gate still tracks the default STAR route, so a working STAR setup is not reported as incomplete.
- **Clearer failure when an aligner tool is missing.** `make_transcriptome`, `salmon_index`, and `hisat2_index` now check for their tool first and exit with a message pointing to Setup, instead of a raw `exit status 127` partway through the run.

## 0.12.1 — 2026-06-25

### Added

- **Linux AppImage.** A self-contained `BulkSeqStudio-x86_64.AppImage` (PySide6 and QtWebEngine
  bundled) is now a release asset, so Linux users can download one file, mark it executable, and run
  the full GUI without installing Python or pip. The portable tar.gz and the from-source path remain
  available. Built on Ubuntu 24.04 (glibc 2.39), so it needs glibc 2.39 or newer.

### Fixed

- **Check Environment on Linux.** The readiness check and its dialog were WSL-only: on a native Linux
  machine with the pipeline tools installed they still reported "WSL2 is not available" and "1 of 4
  ready". The check now has a native branch — it reads the local PATH for snakemake, STAR,
  featureCounts, samtools, fastp, FastQC, MultiQC and Rscript, hides the WSL2 card, and reports
  readiness against the applicable cards (a provisioned machine reads "3 of 3 ready"). The Windows/WSL
  path is unchanged.
- **Save Workflow Settings skipped its own validation.** The button's `clicked` signal passed a
  boolean that was bound to the slot's `validate` parameter, so saving always ran with validation off
  and an invalid contrast (numerator equal to denominator, or a contrast factor that is not a metadata
  column) was written without warning. The button path now validates as intended.
- **Stale contrast dropdown lists on project load.** The numerator / denominator / reference-level
  dropdown option lists were seeded from the previously open project's conditions until the user
  clicked "Refresh conditions from metadata"; they are now re-seeded after the new project's samples
  load. Selected values were already restored correctly.
- **Simple GUI run-state and launch handling.** Loading or browsing to another project during an
  active run is now blocked (it could start a second concurrent run and orphan the first), and a
  failure to launch Snakemake (PATH or permissions) now reports the error and resets the buttons
  instead of leaving the interface stuck.

## 0.12.0 — 2026-06-25

### Added

- **Simple cross-platform GUI (Linux/macOS).** A new lightweight interface (`app/simple_gui.py`,
  launched with `python -m app.simple_gui`) runs the Snakemake pipeline directly in the local
  environment without WSL2, the natural mode on Linux and macOS. It loads an existing project, shows a
  summary, and runs / dry-runs / unlocks the pipeline while streaming the log, reusing the same
  configuration model and Snakemake runner as the full app. Validated on Linux (PySide6 6.11.1) and
  Windows: it constructs, loads a real config, and builds a native `snakemake` command (no WSL wrapper).
- **Cross-platform full GUI.** The full GUI now defaults to native (non-WSL) execution and hides the
  "Use WSL2" toggle on Linux and macOS. Functionality and UI changes target both Linux and Windows
  from this release onward.

### Fixed

- **Config template hardening.** The `created_at` field in the bundled `default_config.yaml` template
  was an unquoted date, which a raw `yaml.safe_load` parses as a `datetime.date`. Snakemake's
  configuration JSON header (`json.dumps(config)`) cannot serialise a date, so feeding the raw
  template to the workflow aborted the run before any rule. The value is now quoted
  (`created_at: "2026-06-19"`) so it loads as a string. The GUI was unaffected (it already writes
  `created_at` as an ISO string); this only hardens the template against direct, non-GUI use.

## 0.11.1 — 2026-06-24

### Added

- **Mitochondrial / chloroplast (organellar) gene handling.** A new Workflow-tab choice — **keep**
  (default), **discard**, or **analyse separately** — controls organellar genes, which can dominate
  library size and skew DESeq2 size-factor normalization. *Discard* removes them from the count
  matrix before the differential test; *separate* runs the main DE on nuclear genes only and writes
  `results/organellar/organellar_counts.txt` plus a per-sample organellar-fraction table
  (`organellar_summary.tsv`, mitochondrial and plastid broken out). Organellar contigs are detected
  automatically from the reference genome FASTA headers (mitochondrion / chloroplast / plastid, plus
  short contig names like `MT` / `Pt`); genes are mapped to them via the featureCounts Chr column or
  the GTF (Salmon), so it works for plants and animals with no curated gene list. Applies to the
  STAR/HISAT2/Salmon alignment routes; `keep` leaves the counts flow unchanged. Validated on rice
  (234 organellar genes, 80 mitochondrial and 154 chloroplast, correctly separated) and on the
  Drosophila pasilla set through Snakemake (38 mitochondrial genes removed, 0.18–0.41% of reads per
  sample), with the main DE run on the remaining nuclear genes.
- **Export tools & references and study design from a run.** When a run finishes, the Run Monitor
  enables two buttons. *Export Tools & References* saves a text file with the tool versions
  (including HISAT2, Salmon, gffread) and R/Bioconductor package versions (DESeq2, clusterProfiler,
  STRINGdb, msigdbr, and more), the reference genome and annotation (organism, source URLs, MD5),
  and the enrichment database codes (KEGG, STRING, g:Profiler, OrgDb). *Export Study Design* saves
  the samples, conditions, layout, DESeq2 design formula, and contrasts. Both files are written by
  the pipeline into `results/reports/` (`tools_references.txt`, `study_design.txt`); the buttons
  save a copy to a location you choose.

### Documentation

- README rewritten for the current tool (three aligners, organellar handling, the two exports, the
  input modes) and all screenshots retaken, including a Run Monitor view of the export buttons.

## 0.11.0 — 2026-06-24

### Added

- **HISAT2 and Salmon aligners (two new routes), in addition to STAR.** The Workflow tab now
  offers three aligners, all validated end to end through DESeq2, enrichment and the PPI network:
  - **STAR → featureCounts** (default, unchanged).
  - **HISAT2 → featureCounts** — a graph aligner with a much smaller index and far lower RAM than
    STAR, so it is viable for large crop genomes that overflow STAR. Produces sorted BAMs like STAR.
  - **Salmon → tximport** — alignment-free selective-alignment transcriptome quantification, the
    lowest memory of the three; the transcriptome is built automatically from the reference
    genome + GTF (gffread), and `tximport` (lengthScaledTPM) collapses transcript counts to the
    gene level. No BAMs.

  All three produce a gene-level count matrix in the same format and run the identical downstream
  (DESeq2, enrichment, PPI, figures); because the aligners assign reads differently, results are
  highly concordant rather than bit-identical (rice: Salmon 12,609 DEGs vs STAR 12,171). The
  quantifier is chosen automatically from the aligner
  (featureCounts for STAR/HISAT2, tximport for Salmon) and shown read-only, so the two cannot be
  mis-paired. A new **"Choosing an aligner"** section in the README gives plain-language "use X
  when Y" guidance.
- **DESeq2-results upload, count-matrix, and GEO-microarray input routes** are documented in the
  README alongside the three aligners (the routes themselves shipped earlier).

### Fixed

- **HISAT2 now auto-detects library strandedness, like STAR.** STAR derives strandedness from its
  ReadsPerGene table and Salmon uses `salmon quant -l A`, but the HISAT2 route had used the static
  `featurecounts.strandedness` config value (default 0 = unstranded). A stranded library aligned
  with HISAT2 was therefore counted as unstranded, miscounting genes with antisense overlap (e.g.
  the reverse-stranded *F. graminearum* set gave 6,298 DEGs at `-s 0` vs 5,836 at the correct
  `-s 2`). HISAT2 now infers strandedness by counting the first sample with featureCounts in
  forward (`-s 1`) and reverse (`-s 2`) modes (paired libraries counted with `-p`) and applying the
  same ratio thresholds as the STAR path, so all three aligners auto-detect strandedness.
- **Salmon pinned to the stable 1.10.3.** The environment had resolved to salmon 2.1.1 (the new
  Rust/piscem rewrite), which crashed with an internal panic (`index out of bounds`) part-way
  through quantifying some samples and had deprecated `--validateMappings`/`--gcBias`. Pinned to
  the mature, widely-cited 1.10.3 C++ build, which is stable across the validation datasets.
- **Robust transcriptome build for the Salmon route across diverse NCBI RefSeq GTFs.** Building
  the transcriptome with gffread previously failed on several real annotations; all are now
  handled: gene-feature lines (empty `transcript_id`) and unknown-strand `?` records
  (trans-spliced organelle genes, e.g. chloroplast *rps12*) are dropped; semicolons embedded
  inside quoted attribute values (gene symbols such as `"CYCB1;1"` in soybean/tomato/potato) are
  neutralized so gffread does not mis-read them as the attribute separator; and duplicate
  transcript names (gffread emits non-unique `unassigned_transcript_N` auto-names for unnamed
  organellar/tRNA records, which the salmon indexer rejects) are de-duplicated, keeping the FASTA
  and tx2gene table in sync. The gffread transcriptome parse was smoke-tested across all seven
  bundled crop GTFs.

### Validation

- **HISAT2** verified end to end on the Drosophila pasilla benchmark (Ensembl; unstranded —
  strandedness auto-detected as 0) and the reverse-stranded *F. graminearum* heat-shock dataset
  (NCBI RefSeq; strandedness auto-detected as 2): fghs 5,812 DEGs (concordant with STAR's 5,836),
  82-node PPI; pasilla 447 DEGs, 61-node PPI; both through KEGG/GO enrichment and figures.
- **Salmon** (1.10.3) verified end to end on the pasilla benchmark (Ensembl) and the rice salt-
  stress benchmark (NCBI RefSeq crop): pasilla 24,278 genes / 530 DEGs / 57-node PPI / 22 figures;
  rice 33,844 genes / 12,609 DEGs (concordant with the STAR route's 12,171) / 22 KEGG-ORA +
  31 KEGG-GSEA terms (osa) / 56-node STRING PPI / 22 figures.

## 0.10.1 — 2026-06-24

### Added

- **Seven more crop reference presets (NCBI RefSeq).** Maize (*Zea mays* B73, Zm-B73-NAM-5.0),
  bread wheat (*Triticum aestivum* Chinese Spring, IWGSC CS RefSeq v2.1), soybean (*Glycine max*
  Williams 82, v4.0), barley (*Hordeum vulgare* Morex, MorexV3), sorghum (*Sorghum bicolor*
  BTx623, NCBIv3), tomato (*Solanum lycopersicum* Heinz 1706, SLM_r2.1) and potato (*Solanum
  tuberosum* DM1-3 516 R44, SolTub_3.0). All sourced from NCBI RefSeq so the gene_id
  (`LOC<GeneID>`) keys KEGG and STRING via the same LOC strip as rice; GO comes from g:Profiler
  (crops have no Bioconductor OrgDb). The KEGG / g:Profiler / STRING codes and the genome + GTF
  URLs were verified against the live KEGG, g:Profiler, STRING and NCBI Datasets services, and
  the `LOC<GeneID>` gene-id convention was confirmed on the maize and tomato annotations.
  Wheat (~14.5 Gb) and barley (~4.5 Gb) are flagged in their notes as not STAR-feasible under
  the ~40 GB WSL2 cap (use count-matrix mode or a high-memory node).

## 0.10.0 — 2026-06-24

### Added

- **Upload your own DESeq2 results (new input mode).** An "Upload DESeq2 Results" button on the Input
  tab takes a ready DESeq2 results table (CSV/TSV with at least `gene_id`, `log2FoldChange`, `padj`;
  common synonyms accepted) and runs the downstream analysis directly — functional enrichment
  (GO/KEGG/GSEA), the volcano / MA / p-value figures, and the STRING PPI network — skipping alignment,
  counts and DESeq2. Select the organism on the Reference Manager tab to resolve the enrichment/PPI
  identifiers. Outputs that need per-sample counts (PCA, sample-distance and expression heatmaps,
  sample correlation, the Wilcoxon diagnostic, genes-of-interest) are skipped with labelled
  placeholders. The accepted table format is documented in the README. Validated by reproducing the
  rice salt-stress enrichment + PPI from its results table alone (identical GO/KEGG/PPI to the full run).
- **Save Cytoscape files button on the PPI tab.** Exports the network interchange files (GraphML, SIF,
  cytoscape.js JSON and the node/edge/hub tables, for both the STRING PPI and the enrichment networks)
  to a folder you choose. GraphML imports into Cytoscape with all node attributes (module, degree,
  betweenness, log2FC).

## 0.9.0 — 2026-06-24

### Added

- **Rice (Oryza sativa) reference preset and a salt-stress benchmark.** Added the Japonica
  IRGSP-1.0 NCBI RefSeq preset and a bundled benchmark, `rice_cy1000_salt_paired` — a
  six-sample paired-end subset (three control, three 5-day salt) of the super-hybrid rice
  CY1000 experiment (DDBJ PRJDB38133). Validated end to end: ~87–92% uniquely mapped per
  sample, 12,171 DE genes (padj < 0.05), KEGG ORA/GSEA + g:Profiler GO enrichment, and a
  58-node STRING PPI network. The enriched terms reproduce the canonical rice salt-stress
  response (ROS detoxification and glutathione metabolism, ABA / plant-hormone signalling,
  ion and amino-acid transport, with photosynthesis and primary carbon metabolism
  down-regulated). This is the first crop preset; it establishes the NCBI-RefSeq crop route.

### Fixed

- **g:Profiler enrichment no longer fails on result serialization.** Writing the gost result
  table failed with "unimplemented type 'list' in 'EncodeElement'" because g:Profiler returns
  list-valued columns (e.g. `parents`); the error aborted the whole route, leaving GO *and*
  KEGG empty for every organism without a Bioconductor OrgDb. Non-atomic columns are now
  dropped before the CSV is written (the full table is kept for the figures).
- **NCBI RefSeq crop gene ids map to KEGG and STRING.** RefSeq gene ids are `LOC<GeneID>`
  (e.g. `LOC4326813`), while KEGG (`osa:4326813`) and STRING key on the bare NCBI GeneID. A
  shape-gated `LOC`-prefix strip in the enrichment and STRING-network steps maps them
  correctly, without touching MSU-style `LOC_Os` locus tags.
- **Benchmark loader accepts datasets without GEO accessions.** DDBJ DRR runs have no GEO /
  experiment accession; those fields and `base_count` are now optional when scaffolding a
  benchmark project.

## 0.8.4 — 2026-06-23

### Fixed

- **Workflow fixes now reach existing projects after an app update.** A project keeps
  its own copy of `workflow/`, copied once when the project is created, and runs
  Snakemake against that copy. So a workflow fix shipped in a new app version (such as
  the 0.8.3 enrichment dotplot fallback) did not appear in a project made by an earlier
  version, even after updating the app. Before each run or figure regeneration, the app
  now compares the project's recorded `workflow_version` against the installed version
  and re-copies the bundled `workflow/` when the project's copy is older, recording the
  new version. A line in the run log notes when this happens. The check is a no-op when
  the project is already current, and a failed copy never blocks the run. After
  installing this version, open an existing project and click "Regenerate figures" (or
  start a run) to pick up the 0.8.3 enrichment fix without recreating the project.

## 0.8.3 — 2026-06-23

### Fixed

- **Enrichment dotplots no longer look empty when only one direction is enriched.**
  The GO over-representation dotplot showed the combined (up + down) result; on small
  designs the combined hypergeometric test can return no terms while the up- or
  down-regulated set alone does. The bundled pasilla benchmark is one such case: zero
  combined GO BP terms but six from the up-regulated genes, so the figure rendered an
  empty placeholder despite real enrichment existing. The dotplot now falls back to the
  up- (then down-) regulated terms when the combined set is empty and adds a caption
  stating which set is shown. The placeholder text is split by cause: "No GO BP terms
  passed the significance cutoff" (the analysis ran), "no annotation database (OrgDb)
  for this organism", and "analysis was skipped or did not complete", replacing the
  single ambiguous "organism unmapped or nothing significant" wording; the KEGG
  placeholder is split the same way. The KEGG organism code is now stored with the
  enrichment objects so the figures can tell "no KEGG code" from "nothing significant".
  Figures regenerated from objects written by earlier versions still render, using the
  previous KEGG wording. GSEA, ridgeline, gene-concept and term-similarity figures are
  unchanged.

## 0.8.2 — 2026-06-23

### Fixed

- **First-time WSL setup no longer dead-ends asking for a sudo password.** On a clean
  WSL distribution that lacked `curl` or `bzip2`, the setup script ran `sudo apt-get`
  to install them, but the GUI runs the installer with no terminal, so sudo had
  nowhere to read a password and the install failed before micromamba was ever
  installed. The bootstrap now downloads and unpacks micromamba with the `python3`
  standard library (present on a default Ubuntu WSL), so the normal path needs no
  system packages and no sudo. `curl`/`wget`+`bzip2` and, only with already-passwordless
  sudo, `apt` remain as fallbacks for minimal distributions; if none apply the script
  prints the exact command to run by hand instead of failing silently. The setup
  screen wording dropped the "may ask for your WSL sudo password" note and now points
  to the log's recovery instructions when an install exits non-zero. Verified by running
  the real script in a clean WSL HOME: micromamba and the full core environment
  (Snakemake, STAR, featureCounts, samtools, fastp, FastQC, MultiQC) install end-to-end
  with no sudo.

## 0.8.1 — 2026-06-22

### Added

- **Second bundled benchmark — *Saccharomyces cerevisiae* WT vs *ume6Δ*** (PRJNA630199 /
  SRP260000, R64-1-1): a small, fast-genome paired-end RNA-seq benchmark on a different
  organism than the Drosophila pasilla set, exercising the g:Profiler + KEGG enrichment
  route. *Create Benchmark Project* now shows a picker when more than one benchmark is
  bundled, and each benchmark's contrast, reference level and read layout are read from
  its dataset entry (previously hardcoded to pasilla). Verified: the project scaffolds
  with the organism's enrichment IDs resolved and its full pipeline DAG resolves end-to-end.

## 0.8.0 — 2026-06-22

An interface and reliability release. A multi-perspective GUI audit (debugger,
visual, newbie and professional lenses) produced 47 findings; the confirmed ones
were fixed after verifying each against the code, and the aesthetic layer was
reworked. The interactive PPI viewer state-sync was audited and left unchanged —
the reported "desync" was a false positive; the 0.6.1 display-only fix is intact.

### Fixed (reliability)

- **Closing the window during a run no longer crashes.** `closeEvent` now stops the
  pipeline and waits for the runner thread instead of letting Qt destroy a live
  thread (and orphan the WSL process tree).
- **Opening another project mid-run is blocked,** and opening a project now clears
  the previous project's log, status, figures, table and network instead of leaving
  them on screen (cross-project state bleed).
- **The run-approval tick (REVIEW_REQUIRED) resets when you open a project,** so an
  approval from one project can no longer let an unreviewed run start in another.
- **"Regenerate figures" no longer fails with MissingInputException.** Optional figure
  targets (enrichment, PPI, genes-of-interest) are forced only when their input files
  exist on disk, not merely when their config flag is on.
- **The Outputs figure picker keeps your current selection** across a refresh / post-run
  rescan instead of jumping back to the first figure, and shows a placeholder when a
  project has no figures yet.
- **SRA metadata fetch and report generation run off the UI thread,** so large studies
  and WSL tool-version probes no longer freeze the window.
- Pixel figure dimensions stay physically consistent when DPI changes; malformed output
  CSVs no longer crash the table preview; the low-mapping STAR guardrail fires again on
  Snakemake 9 log output; project names with filesystem-unsafe characters are rejected.

### Fixed (the enrichment trap)

- **Count-matrix and microarray modes now tell you to pick an organism.** The Reference
  Manager banner is an amber callout explaining that selecting an organism enables
  GO/KEGG enrichment and the STRING PPI network; the count-matrix import message and an
  inline Workflow-Settings note say the same. A run with enrichment enabled and no
  organism configured asks for confirmation and is flagged REVIEW_REQUIRED in the checks.
- **HISAT2 / Salmon (aligner) and STAR_GeneCounts / Salmon_tximport (quantifier) are
  disabled** in their dropdowns — only the STAR + featureCounts route is implemented, so
  they no longer silently dead-end or no-op a run.

### Changed (interface)

- **WCAG-AA contrast.** Light table-header text, disabled input/button text, the warning
  accent, and the dark disabled-primary text were darkened/lightened to meet 4.5:1
  (verified by computation).
- **Workflow Settings** is grouped into three cards (alignment & read processing /
  differential expression / outputs) instead of one flat 14-field list, with a primary,
  right-aligned Save.
- **One clear primary action per tab** (New Project, Use Selected Preset, Detect and
  Recommend, Estimate Runtime, Run checks, Start Run, Generate Reports, Load network).
- **The PPI controls read in plain language** — "Force-directed (fCoSE)", "log₂ fold
  change", "Node degree" — and Export PNG/SVG stay disabled until a network is loaded.
- Empty panels carry placeholder guidance; the dark-mode figure canvas is a softer grey
  so white figures don't glare; a "what's next" message points to Outputs/PPI after a run.

### Added

- Keyboard shortcuts: Ctrl+O (open project), F5 (dry run), F9 (start run).
- A recent-projects picker on the Project tab.

## 0.7.2 — 2026-06-22

### Fixed

- **The Outputs figure list now refreshes automatically when a run finishes.** A
  completed pipeline run or **Regenerate figures** previously left the figure dropdown
  stale — the (static) table list still updated, but newly written figures, including
  the enrichment dotplots, only appeared after a manual **Refresh figures**. The run-
  completion handler now re-scans `results/figures/` on success.

## 0.7.1 — 2026-06-22

### Changed

- **The PPI network figure now defaults to a force-directed layout** (Fruchterman-
  Reingold, `fr`) instead of stress majorization, so high-degree **hub proteins are
  visually prominent** (pulled to the centre, drawn large by degree) rather than packed
  into a strip. The interactive PPI viewer was already force-directed (fcose).
- Refreshed the documentation screenshots (de-squeezed volcano, KEGG pathway
  enrichment, and the PPI hub network).

## 0.7.0 — 2026-06-22

Enrichment now works for every catalogued organism, a g:Profiler backend adds
GO/Reactome for species without a Bioconductor OrgDb, and the figure set was
reworked for legibility. Validated on *F. graminearum* (GSE78885 heat-shock and an
FgEXOSC1 RNA-seq set), Drosophila pasilla, and *S. cerevisiae*.

### Added

- **Per-organism enrichment + PPI identifiers for all 22 reference presets.** Each
  catalogue entry now carries a KEGG organism code, a STRING-valid taxon, a
  Bioconductor OrgDb (where one exists), and a g:Profiler organism. Selecting a
  preset, a GEO organism, or a benchmark now populates `enrichment.*` and
  `ppi.taxon` automatically, so KEGG ORA + GSEA run for any organism with a KEGG
  code — including the fungi and bacteria that previously produced no enrichment.
- **g:Profiler GO backend (`gprofiler2`).** Organisms with no Bioconductor OrgDb
  (*S. cerevisiae*, *S. pombe*, *Aspergillus*, *Neurospora*, *Candida*,
  *Magnaporthe*, …) now get GO:BP / KEGG / Reactome over-representation via
  g:Profiler on a tested-gene background. clusterProfiler stays the default where an
  OrgDb is installed.

### Fixed

- **Four "supported" organisms silently produced zero enrichment.** Arabidopsis,
  yeast, worm and zebrafish mapped to Bioconductor OrgDbs that are not in the
  environment, so `library()` failed and the KEGG branch was never reached. The GO
  route now falls through OrgDb → g:Profiler → KEGG, recovering enrichment for them.
- **Wrong STRING taxids.** *F. graminearum* PH-1 used species taxid 5518 (a 404 in
  STRING v12); it now uses the strain taxid 229533. *S. pombe* uses 284812. Species
  with no STRING v12 entry degrade to an honest empty-network warning.
- **S. pombe gene ids were corrupted** by the version-strip (`SPOM_SPAC212.11` →
  `SPOM_SPAC212`); the strip is now restricted to Ensembl-style version suffixes.
- **Silent empty enrichment is now loud.** When ~0 of N gene ids map (wrong keytype
  or KEGG code), the check reports `REVIEW_REQUIRED` instead of an empty `PASS`.

### Changed (figures)

- **Volcano de-squeeze.** The y-axis caps at the bulk's range; extreme / `padj == 0`
  genes are clamped to the cap and drawn as hollow boundary markers with the axis
  labelled "(axis capped)", so the DEG cloud fills the panel instead of being crushed
  under a few ultra-significant genes. Points gain density-readable size/alpha and
  labels get leader lines.
- **One palette, three honest roles** (categorical / sequential / diverging) shared
  by every figure via a new `figure_style.R`. Z-score heatmaps use a zero-centred
  diverging ramp with symmetric breaks; distance and correlation use sequential.
- **Per-figure rework:** MA density colouring with a significance legend; dispersion
  and Cook's re-expressed as themed ggplots; PCA aspect no longer squeezes a dominant
  PC1; KEGG/GO dot-plots show wrapped pathway names on the shared palette; the GSEA
  ridgeplot renders again (built from leading-edge fold changes); Wilcoxon
  concordance is a 2-D density rather than a black smear; the PPI figure gains a
  stress layout, a node-degree legend and repelled hub labels.
- **Outputs preview re-fits** on resize/show, fixing the squeezed thumbnail.
- ~20 new `figures_style` settings expose the volcano cap, palette roles, heatmap
  scaling and enrichment category counts; all default to the upgraded behaviour.

## 0.6.2 — 2026-06-22

### Fixed

- **GEO series (GSE…) accessions in the SRA box now work.** ENA's API rejects GEO
  accessions (HTTP 400), so the metadata fetch now auto-resolves a `GSE…` to its
  linked SRA study (e.g. GSE78885 → SRP071140) before querying ENA. A microarray
  series (no SRA link) gives a clear message pointing to *Fetch a GEO microarray
  series*, and an unrecognised accession gives an actionable error instead of a raw
  `HTTP Error 400`.

## 0.6.1 — 2026-06-22

Bug-fix release from a deep debug sweep of the 0.6.0 network/stats and
interactive-PPI work. Every fix below was verified on the benchmark projects
(Drosophila pasilla, mouse GSE5583, *Fusarium graminearum* GSE78885) before release.

### Fixed

- **PPI network for symbol-less genomes (e.g. *Fusarium*).** The STRING seed was
  built only from gene symbols, so locus-tag annotations (FGSG_* etc.) produced an
  empty network. It now falls back to `gene_id` when symbols are absent, and the
  log2FC node colouring joins on the same identifier. *F. graminearum* GSE78885 now
  builds an 82-node / 158-edge network (STRING taxid 229533).
- **PPI degrade now reports `WARNING`, not `PASS`,** so an empty/dropped network is
  visible in the run-health rollup instead of being masked.
- **Empty network exports are now valid GraphML** (a minimal well-formed file)
  rather than a 0-byte file that fails to import in Cytoscape/igraph.
- **Sample-correlation no longer aborts the run on NA** intensities (microarray):
  pairwise-complete correlation, NA-safe clustering, and degrade-to-placeholder.
- **Microarray enrichment** now defaults the bitr keytype to `SYMBOL` in the
  workflow (not only the GUI), so a mapped-OrgDb microarray run no longer silently
  returns zero GO/KEGG/GSEA terms from a scripted config.
- **Set-overlap (MSigDB Hallmark)** falls back to `gene_id` in count-matrix mode,
  where there is no GTF and symbols are all NA.
- **Interactive PPI export background is selectable (White / Transparent)** and the
  export always uses dark labels so they stay legible regardless of the app theme.
- **Label auto-hide** (above ~220 nodes) is now display-only and no longer mutates
  the labels preference, so smaller networks viewed afterwards keep their labels and
  the Qt checkbox stays in sync. The current layout is also remembered across loads.
- **Frozen self-test fails cleanly** (sentinel + exit code) when the bundled
  `viewer.html` is missing, instead of hanging on a modal dialog.
- **Regenerate figures** now restyles the 0.6.0 style-aware figures
  (sample-correlation, Wilcoxon, set-overlap, enrichment, PPI), not just the core
  DESeq2 figures.
- **PPI graph assembler hardening:** confidence floor derived from the true minimum
  edge weight (never a 1.0 sentinel that hides every edge), and per-symbol dedup no
  longer lets a NaN-baseMean row win over a valid one.

## 0.6.0 — 2026-06-21

- Dedicated interactive **PPI Network** tab (cytoscape.js in QtWebEngine): hover for
  per-protein detail, customise layout/colour/size/confidence, export PNG/SVG.
- **KEGG** pathway ORA + GSEA, working for any organism with a KEGG code even
  without a Bioconductor OrgDb (fungi, bacteria).
- STRING PPI network + Cytoscape export (GraphML/SIF/cytoscape.js JSON).
- Sample-to-sample correlation (Pearson + Spearman), Wilcoxon concordance, TOST
  equivalence, MSigDB Hallmark set-overlap, disease-ontology enrichment.
- Eight figure palettes.
