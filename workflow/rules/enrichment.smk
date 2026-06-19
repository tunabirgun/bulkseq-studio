# Functional enrichment (protocol section 8): GO/KEGG ORA and GSEA via
# clusterProfiler. Organism-specific; wrapped so failures degrade gracefully.

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
        orgdb=_ENR.get("orgdb", "org.Dm.eg.db"),
        keytype=_ENR.get("keytype", "FLYBASE"),
        kegg=_ENR.get("kegg_organism", "dme"),
        alpha=config.get("deseq2", {}).get("alpha", 0.05),
    benchmark:
        "benchmarks/enrichment.tsv"
    log:
        "logs/enrichment.log",
    script:
        "../scripts/run_enrichment.R"
