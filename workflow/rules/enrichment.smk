# Functional enrichment (protocol section 8): GO/KEGG ORA and GSEA via
# clusterProfiler. The Bioconductor OrgDb + keytype + KEGG code are derived from
# the organism so enrichment is never run against the wrong species' database;
# organisms without a mapping (e.g. most fungi) are skipped cleanly by the R
# script. config["enrichment"] overrides any field.

# (OrgDb, keytype matching the catalog's gene-id source, KEGG organism code)
_ENRICH_MAP = {
    "homo sapiens": ("org.Hs.eg.db", "ENSEMBL", "hsa"),
    "mus musculus": ("org.Mm.eg.db", "ENSEMBL", "mmu"),
    "drosophila melanogaster": ("org.Dm.eg.db", "FLYBASE", "dme"),
    "caenorhabditis elegans": ("org.Ce.eg.db", "ENSEMBL", "cel"),
    "danio rerio": ("org.Dr.eg.db", "ENSEMBL", "dre"),
    "saccharomyces cerevisiae": ("org.Sc.sgd.db", "ENSEMBL", "sce"),
    "arabidopsis thaliana": ("org.At.tair.db", "TAIR", "ath"),
    "rattus norvegicus": ("org.Rn.eg.db", "ENSEMBL", "rno"),
    "gallus gallus": ("org.Gg.eg.db", "ENSEMBL", "gga"),
    "sus scrofa": ("org.Ss.eg.db", "ENSEMBL", "ssc"),
    "bos taurus": ("org.Bt.eg.db", "ENSEMBL", "bta"),
}
_ORG = str(config.get("reference", {}).get("organism_name", "")).lower()
_MAPPED = next((v for k, v in _ENRICH_MAP.items() if k in _ORG), ("", "", ""))
_ENR = config.get("enrichment", {})

# KEGG gene-key form per organism code: "geneid" where the organism's KEGG entries key on
# the bare NCBI GeneID, "locus_tag" where they key on the native locus tag. It decides
# whether run_enrichment.R bridges the gene ids before querying KEGG, and bridging the
# wrong way returns empty tables rather than an error. Measured 2026-09-13 from
# link/<org>/pathway as a majority over the organism's full key list, with list/<org> as a
# second source. app/data/reference_catalog.yaml is the source of truth; the table is
# duplicated here because a project receives the workflow tree without app/data, and
# tests/test_enrichment_mapping.py fails when the two disagree.
_KEGG_KEY_FORM = {
    "afm": "locus_tag",
    "ang": "locus_tag",
    "ani": "locus_tag",
    "ath": "locus_tag",
    "bfu": "locus_tag",
    "bsu": "locus_tag",
    "bta": "geneid",
    "cal": "locus_tag",
    "cel": "locus_tag",
    "cng": "locus_tag",
    "dme": "locus_tag",
    "dre": "geneid",
    "eco": "locus_tag",
    "fgr": "locus_tag",
    "fox": "locus_tag",
    "fpu": "locus_tag",
    "fvr": "locus_tag",
    "gga": "geneid",
    "ghi": "geneid",
    "gmx": "geneid",
    "hsa": "geneid",
    "hvg": "geneid",
    "mgr": "locus_tag",
    "mmu": "geneid",
    "mtr": "geneid",
    "mtu": "locus_tag",
    "ncr": "locus_tag",
    "nta": "geneid",
    "osa": "geneid",
    "pae": "locus_tag",
    "pfa": "locus_tag",
    "rno": "geneid",
    "sao": "locus_tag",
    "sbi": "geneid",
    "sce": "locus_tag",
    "sly": "geneid",
    "sot": "geneid",
    "spo": "geneid",
    "ssc": "geneid",
    "ssl": "locus_tag",
    "taes": "geneid",
    "uma": "locus_tag",
    "vvi": "geneid",
    "zma": "geneid",
    "ztr": "locus_tag",
}
_KEGG_CODE = _ENR.get("kegg_organism") or _MAPPED[2]
# Named in check 10 when the route cannot supply the ids the organism's KEGG entries key on.
_DE_ROUTE = (
    "imported DE results input route" if DE_RESULTS_MODE
    else "microarray input route" if MICROARRAY_MODE
    else "count matrix input route" if COUNT_MATRIX_MODE
    else f"{DE_ENGINE} engine"
)


rule enrichment:
    input:
        results="results/deseq2/deseq2_results.csv",
        up="results/deseq2/upregulated_genes.csv",
        down="results/deseq2/downregulated_genes.csv",
    output:
        summary="results/enrichment/enrichment_summary.txt",
        go="results/enrichment/go_ora_all.csv",
        go_up="results/enrichment/go_ora_up.csv",
        go_down="results/enrichment/go_ora_down.csv",
        go_bp="results/enrichment/go_ora_BP.csv",
        go_mf="results/enrichment/go_ora_MF.csv",
        go_cc="results/enrichment/go_ora_CC.csv",
        gsea="results/enrichment/gsea.csv",
        kegg="results/enrichment/kegg_ora.csv",
        kegg_gsea="results/enrichment/kegg_gsea.csv",
        objects="results/enrichment/enrichment_objects.rds",
        id_map="results/enrichment/id_map.csv",
        check="checks/10_enrichment_qc.json",
    params:
        # `or` (not dict default) so an explicit null override from the config
        # falls back to the organism mapping instead of disabling enrichment.
        orgdb=_ENR.get("orgdb") or _MAPPED[0],
        # Microarray ingestion keys the canonical results by gene SYMBOL (GPL probe
        # annotation), so default the bitr keytype to SYMBOL in microarray mode
        # instead of the organism's RNA-seq default (e.g. ENSEMBL), which would map
        # nothing. The GUI also sets this, but a scripted/hand-edited config might not.
        keytype=_ENR.get("keytype") or ("SYMBOL" if MICROARRAY_MODE else _MAPPED[1]),
        kegg_keytype=_ENR.get("kegg_keytype") or "",
        kegg=_KEGG_CODE,
        # The measured key form, so the script does not spend a KEGG round trip on an
        # organism whose form is already known; an unlisted organism is probed live.
        kegg_key_form=_ENR.get("kegg_key_form") or _KEGG_KEY_FORM.get(_KEGG_CODE, ""),
        de_route=_DE_ROUTE,
        # backend selects the GO route: 'clusterprofiler' = auto OrgDb->gprofiler->none;
        # 'gprofiler' forces the g:Profiler GO route. gprofiler_organism is the
        # g:Profiler organism id (e.g. hsapiens, anidulans), distinct from the KEGG code.
        backend=_ENR.get("backend", "clusterprofiler"),
        gprofiler_organism=_ENR.get("gprofiler_organism") or "",
        # Independent expected species name for fail-closed validation against
        # clusterProfiler's current KEGG species catalog. Missing legacy taxon ids
        # are resolved from the exact official KEGG GENOME organism record.
        organism_name=config.get("reference", {}).get("organism_name") or "",
        # A catalog species taxon is independent of strain-specific OrgDb metadata.
        taxon_id=_ENR.get("taxon_id") or "",
        alpha=config.get("deseq2", {}).get("alpha", 0.05),
    benchmark:
        "benchmarks/enrichment.tsv"
    log:
        "logs/enrichment.log",
    script:
        "../scripts/run_enrichment.R"


# Enrichment visualisations from the persisted clusterProfiler objects. Renders
# dotplot / GSEA running-score + ridgeplot / cnetplot / emapplot as PNG+SVG into
# results/figures/ (placeholders when enrichment was skipped or empty). Kept a
# separate rule so it can be regenerated without re-running enrichment.
rule enrichment_figures:
    input:
        objects="results/enrichment/enrichment_objects.rds",
    output:
        dotplot_png="results/figures/enrichment_dotplot.png",
        dotplot_svg="results/figures/enrichment_dotplot.svg",
        gsea_png="results/figures/enrichment_gsea.png",
        gsea_svg="results/figures/enrichment_gsea.svg",
        ridge_png="results/figures/enrichment_ridgeplot.png",
        ridge_svg="results/figures/enrichment_ridgeplot.svg",
        cnet_png="results/figures/enrichment_cnetplot.png",
        cnet_svg="results/figures/enrichment_cnetplot.svg",
        emap_png="results/figures/enrichment_emapplot.png",
        emap_svg="results/figures/enrichment_emapplot.svg",
        do_dotplot_png="results/figures/enrichment_do_dotplot.png",
        do_dotplot_svg="results/figures/enrichment_do_dotplot.svg",
        kegg_dotplot_png="results/figures/enrichment_kegg_dotplot.png",
        kegg_dotplot_svg="results/figures/enrichment_kegg_dotplot.svg",
        kegg_gsea_png="results/figures/enrichment_kegg_gsea.png",
        kegg_gsea_svg="results/figures/enrichment_kegg_gsea.svg",
        go_bp_png="results/figures/enrichment_go_BP_dotplot.png",
        go_bp_svg="results/figures/enrichment_go_BP_dotplot.svg",
        go_mf_png="results/figures/enrichment_go_MF_dotplot.png",
        go_mf_svg="results/figures/enrichment_go_MF_dotplot.svg",
        go_cc_png="results/figures/enrichment_go_CC_dotplot.png",
        go_cc_svg="results/figures/enrichment_go_CC_dotplot.svg",
    params:
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/enrichment_figures.tsv"
    log:
        "logs/enrichment_figures.log",
    script:
        "../scripts/make_enrichment_figures.R"


# Cytoscape-compatible export of the enrichment networks (term-similarity +
# gene-concept) as GraphML / SIF / cytoscape.js JSON + node/edge CSVs.
rule network_enrichment:
    input:
        objects="results/enrichment/enrichment_objects.rds",
    output:
        emap_graphml="results/networks/enrichment_emap.graphml",
        emap_sif="results/networks/enrichment_emap.sif",
        emap_cyjs="results/networks/enrichment_emap.cyjs",
        emap_nodes="results/networks/enrichment_emap_nodes.csv",
        emap_edges="results/networks/enrichment_emap_edges.csv",
        genemap_graphml="results/networks/enrichment_genemap.graphml",
        genemap_sif="results/networks/enrichment_genemap.sif",
        genemap_cyjs="results/networks/enrichment_genemap.cyjs",
        genemap_nodes="results/networks/enrichment_genemap_nodes.csv",
        genemap_edges="results/networks/enrichment_genemap_edges.csv",
    benchmark:
        "benchmarks/network_enrichment.tsv"
    log:
        "logs/network_enrichment.log",
    script:
        "../scripts/export_network.R"


# Custom gene-set enrichment (optional): clusterProfiler ORA + GSEA against a user-supplied
# GMT and/or id->term annotation table, via TERM2GENE. Gated on the gene_sets config, so when
# neither is set these rules are not defined and run_enrichment.R's outputs are untouched.
# Organism-agnostic (no OrgDb/KEGG needed), so it works for organisms run_enrichment.R skips.
# Separate dotplot rule so it can be restyled without re-running enrichment.
_CUSTOM_GMT = config.get("gene_sets", {}).get("custom_gene_sets")
_CUSTOM_ANNOT = config.get("gene_sets", {}).get("functional_annotation_table")
_CUSTOM_BG = config.get("gene_sets", {}).get("background_gene_list")

if _CUSTOM_GMT or _CUSTOM_ANNOT:

    rule custom_enrichment:
        input:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            # File inputs only when set, so editing them is a rerun trigger and a missing
            # path fails at DAG build rather than mid-script.
            **({"gmt": _CUSTOM_GMT} if _CUSTOM_GMT else {}),
            **({"annot": _CUSTOM_ANNOT} if _CUSTOM_ANNOT else {}),
            **({"background": _CUSTOM_BG} if _CUSTOM_BG else {}),
        output:
            ora="results/enrichment/custom_ora.csv",
            gsea="results/enrichment/custom_gsea.csv",
            summary="results/enrichment/custom_enrichment_summary.txt",
            objects="results/enrichment/custom_enrichment_objects.rds",
            check="checks/11_custom_enrichment_qc.json",
        params:
            keytype=_ENR.get("keytype") or ("SYMBOL" if MICROARRAY_MODE else _MAPPED[1]),
            gmt=_CUSTOM_GMT or "",
            annot=_CUSTOM_ANNOT or "",
            background=_CUSTOM_BG or "",
            alpha=config.get("deseq2", {}).get("alpha", 0.05),
        benchmark:
            "benchmarks/custom_enrichment.tsv"
        log:
            "logs/custom_enrichment.log",
        script:
            "../scripts/run_custom_enrichment.R"

    rule custom_enrichment_figure:
        input:
            objects="results/enrichment/custom_enrichment_objects.rds",
        output:
            dotplot_png="results/figures/custom_enrichment_dotplot.png",
            dotplot_svg="results/figures/custom_enrichment_dotplot.svg",
        params:
            style=config.get("figures_style", {}),
        benchmark:
            "benchmarks/custom_enrichment_figure.tsv"
        log:
            "logs/custom_enrichment_figure.log",
        script:
            "../scripts/make_custom_enrichment_figure.R"


# GSVA sample-level gene-set activity (optional). Organism-safe: scores samples against the
# user's custom gene sets only, so it works for non-model organisms. Reads the normalized
# expression matrix; writes a pathway x sample score matrix + a heatmap. Descriptive, not a test.
if GSVA_ON:

    rule gsva:
        input:
            # The log-scale homoscedastic matrix (VST for DESeq2, logCPM for voom/edgeR, log2
            # intensity for microarray) — NOT the linear DESeq2 normalized counts. GSVA's
            # kcdf="Gaussian" assumes continuous log-scale input, so linear counts gave an
            # invalid per-gene expression statistic on the default DESeq2 route.
            normalized="results/export/normalized_expression_matrix.csv",
            gmt=config.get("gene_sets", {}).get("custom_gene_sets"),
            samples=config["input"]["samples"],
        output:
            scores="results/gsva/gsva_scores.csv",
            heatmap_png="results/figures/gsva_heatmap.png",
            heatmap_svg="results/figures/gsva_heatmap.svg",
        params:
            style=config.get("figures_style", {}),
        benchmark:
            "benchmarks/gsva.tsv"
        log:
            "logs/gsva.log",
        script:
            "../scripts/run_gsva.R"
