rule fastp_trim:
    input:
        "results/qc/multiqc_pre_trim.done"
    output:
        "results/qc/fastp.done"
    benchmark:
        "benchmarks/fastp.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'fastp trimming placeholder. Command parameters are recorded in config/config.yaml.'"

rule fastqc_post_trim:
    input:
        "results/qc/fastp.done"
    output:
        "results/qc/post_trim_fastqc.done"
    benchmark:
        "benchmarks/fastqc_post_trim.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'Post-trim FastQC placeholder.'"
