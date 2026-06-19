# Gene-level quantification with featureCounts using the inferred strandedness
# (protocol section 6.12). A single matrix over all BAMs.

_FC = config.get("featurecounts", {})


rule featurecounts:
    input:
        bams=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam", sample=SAMPLES),
        bais=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai", sample=SAMPLES),
        gtf=ANNOTATION_GTF,
        strand="results/aligned/strandedness.txt",
    output:
        counts="results/counts/counts.txt",
        summary="results/counts/counts.txt.summary",
    params:
        paired="-p --countReadPairs" if ALL_PAIRED else "",
        feature=_FC.get("feature_type", "exon"),
        attribute=_FC.get("attribute_type", "gene_id"),
    threads:
        rule_threads("featurecounts", 6)
    benchmark:
        "benchmarks/featurecounts.tsv"
    log:
        "logs/featurecounts.log",
    shell:
        "S=$(cat {input.strand}); "
        "featureCounts -a {input.gtf} -o {output.counts} -T {threads} "
        "--tmpDir {resources.tmpdir} "
        "{params.paired} -t {params.feature} -g {params.attribute} -s $S -Q 10 "
        "{input.bams} > {log} 2>&1"


rule quantification_check:
    input:
        summary="results/counts/counts.txt.summary",
    output:
        "checks/07_quantification_qc.json",
    benchmark:
        "benchmarks/07_quantification_qc.tsv"
    shell:
        "python workflow/scripts/summarize_quantification.py --summary {input.summary} --out {output}"
