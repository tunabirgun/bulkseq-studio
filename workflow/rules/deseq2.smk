rule deseq2:
    input:
        "results/counts/gene_counts.tsv",
        "config/samples.tsv"
    output:
        "results/deseq2/deseq2_results.tsv",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json"
    benchmark:
        "benchmarks/deseq2.tsv"
    shell:
        "python workflow/scripts/run_deseq2_placeholder.py --counts {input[0]} --samples {input[1]} --results {output[0]} --design-check {output[1]} --deseq-check {output[2]}"
