# Quality control: FastQC before and after trimming, MultiQC aggregation
# (protocol sections 6.3, 6.5, 6.13).


rule fastqc_raw:
    input:
        lambda wc: [f for f in (raw_fastq(wc.sample, 1), raw_fastq(wc.sample, 2)) if f],
    output:
        directory("results/qc/fastqc_raw/{sample}"),
    threads:
        rule_threads("fastqc", 1)
    benchmark:
        "benchmarks/fastqc_raw_{sample}.tsv"
    log:
        "logs/fastqc_raw_{sample}.log",
    shell:
        "mkdir -p {output} && fastqc -o {output} -t {threads} {input} > {log} 2>&1"


rule fastqc_trim:
    input:
        lambda wc: trimmed_fastqs(wc.sample),
    output:
        directory("results/qc/fastqc_trim/{sample}"),
    threads:
        rule_threads("fastqc", 1)
    benchmark:
        "benchmarks/fastqc_trim_{sample}.tsv"
    log:
        "logs/fastqc_trim_{sample}.log",
    shell:
        "mkdir -p {output} && fastqc -o {output} -t {threads} {input} > {log} 2>&1"


rule multiqc:
    input:
        expand("results/qc/fastqc_raw/{sample}", sample=SAMPLES),
        expand("results/qc/fastqc_trim/{sample}", sample=SAMPLES),
        expand("results/qc/fastp/{sample}.json", sample=SAMPLES),
        expand("results/aligned/{sample}_Log.final.out", sample=SAMPLES),
        "results/counts/counts.txt.summary",
    output:
        "results/qc/multiqc/multiqc_report.html",
    benchmark:
        "benchmarks/multiqc.tsv"
    log:
        "logs/multiqc.log",
    shell:
        "multiqc results/qc results/aligned results/counts "
        "-o results/qc/multiqc -n multiqc_report -f > {log} 2>&1"
