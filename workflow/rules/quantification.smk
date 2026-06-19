rule featurecounts:
    input:
        bam="results/aligned/{sample}.Aligned.sortedByCoord.out.bam"
    output:
        "results/counts/{sample}.featureCounts.txt"
    benchmark:
        "benchmarks/featurecounts_{sample}.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'featureCounts placeholder for {wildcards.sample}.'"

rule merge_counts:
    input:
        expand("results/counts/{sample}.featureCounts.txt", sample=SAMPLES)
    output:
        "results/counts/gene_counts.tsv",
        "checks/07_quantification_qc.json"
    benchmark:
        "benchmarks/merge_counts.tsv"
    shell:
        "python workflow/scripts/make_counts_placeholder.py --samples config/samples.tsv --counts {output[0]} --check {output[1]}"
