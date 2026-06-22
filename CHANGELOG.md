# Changelog

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
