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
    output:
        summary="results/enrichment/enrichment_summary.txt",
        go="results/enrichment/go_ora.csv",
        gsea="results/enrichment/gsea.csv",
        check="checks/10_enrichment_qc.json",
    params:
        orgdb=_ENR.get("orgdb", _MAPPED[0]),
        keytype=_ENR.get("keytype", _MAPPED[1]),
        kegg=_ENR.get("kegg_organism", _MAPPED[2]),
        alpha=config.get("deseq2", {}).get("alpha", 0.05),
    benchmark:
        "benchmarks/enrichment.tsv"
    log:
        "logs/enrichment.log",
    script:
        "../scripts/run_enrichment.R"
