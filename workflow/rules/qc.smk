rule fastqc_pre_trim:
    input:
        ["checks/00_project_setup.json"] + RAW_FASTQS
    output:
        "results/qc/pre_trim_fastqc.done"
    benchmark:
        "benchmarks/fastqc_pre_trim.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'FastQC pre-trim placeholder. Install FastQC/MultiQC for production runs.'"

rule multiqc_pre_trim:
    input:
        "results/qc/pre_trim_fastqc.done"
    output:
        "results/qc/multiqc_pre_trim.done"
    benchmark:
        "benchmarks/multiqc_pre_trim.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'MultiQC pre-trim placeholder.'"
