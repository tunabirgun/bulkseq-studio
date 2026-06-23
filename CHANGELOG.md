# Changelog

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
