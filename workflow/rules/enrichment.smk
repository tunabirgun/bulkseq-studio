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
}
_ORG = str(config.get("reference", {}).get("organism_name", "")).lower()
_MAPPED = next((v for k, v in _ENRICH_MAP.items() if k in _ORG), ("", "", ""))
_ENR = config.get("enrichment", {})


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
        gsea="results/enrichment/gsea.csv",
        objects="results/enrichment/enrichment_objects.rds",
        check="checks/10_enrichment_qc.json",
    params:
        # `or` (not dict default) so an explicit null override from the config
        # falls back to the organism mapping instead of disabling enrichment.
        orgdb=_ENR.get("orgdb") or _MAPPED[0],
        keytype=_ENR.get("keytype") or _MAPPED[1],
        kegg=_ENR.get("kegg_organism") or _MAPPED[2],
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
    params:
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/enrichment_figures.tsv"
    log:
        "logs/enrichment_figures.log",
    script:
        "../scripts/make_enrichment_figures.R"
