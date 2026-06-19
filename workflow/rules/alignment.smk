rule star_align:
    input:
        "references/star_index.done",
        "results/qc/post_trim_fastqc.done"
    output:
        "results/aligned/{sample}.Aligned.sortedByCoord.out.bam"
    benchmark:
        "benchmarks/star_align_{sample}.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'STAR BAM placeholder for {wildcards.sample}.'"

rule summarize_alignment:
    input:
        expand("results/aligned/{sample}.Aligned.sortedByCoord.out.bam", sample=SAMPLES)
    output:
        "checks/06_alignment_qc.json"
    benchmark:
        "benchmarks/06_alignment_qc.tsv"
    shell:
        "python workflow/scripts/write_check.py --out {output} --status REVIEW_REQUIRED --message 'Alignment outputs are placeholders until STAR is enabled.'"
