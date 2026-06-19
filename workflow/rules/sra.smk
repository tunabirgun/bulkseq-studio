rule download_ena_fastqs:
    input:
        "config/samples.tsv"
    output:
        RAW_FASTQS
    benchmark:
        "benchmarks/download_ena_fastqs.tsv"
    shell:
        "python workflow/scripts/download_ena_fastqs.py --samples {input} --out-root ."
