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


# The per-sample alignment artifact and the MultiQC scan dirs depend on the route:
# STAR has Log.final.out, HISAT2 has the sorted BAM (+ summary), Salmon has quant.sf.
def _multiqc_align_inputs():
    if USE_SALMON:
        return expand("results/salmon/{sample}/quant.sf", sample=SAMPLES)
    if USE_HISAT2:
        return expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam", sample=SAMPLES)
    return expand("results/aligned/{sample}_Log.final.out", sample=SAMPLES)


_MQC_SCAN = "results/qc results/salmon results/counts" if USE_SALMON else "results/qc results/aligned results/counts"


rule multiqc:
    input:
        expand("results/qc/fastqc_raw/{sample}", sample=SAMPLES),
        expand("results/qc/fastqc_trim/{sample}", sample=SAMPLES),
        expand("results/qc/fastp/{sample}.json", sample=SAMPLES),
        _multiqc_align_inputs(),
        COUNTS_SUMMARY,
    output:
        "results/qc/multiqc/multiqc_report.html",
    benchmark:
        "benchmarks/multiqc.tsv"
    log:
        "logs/multiqc.log",
    shell:
        "multiqc " + _MQC_SCAN + " -o results/qc/multiqc -n multiqc_report -f > {log} 2>&1"
