rule enrichment:
    input:
        "results/deseq2/deseq2_results.tsv"
    output:
        "results/enrichment/enrichment_results.tsv",
        "checks/10_enrichment_qc.json"
    benchmark:
        "benchmarks/enrichment.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output[0]} --message 'GO/KEGG/GSEA placeholder.' && python workflow/scripts/write_check.py --out {output[1]} --status REVIEW_REQUIRED --message 'Enrichment is scaffolded; configure organism-specific gene sets before interpretation.'"
