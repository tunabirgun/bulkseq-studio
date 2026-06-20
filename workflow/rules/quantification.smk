# Gene-level quantification with featureCounts using the inferred strandedness
# (protocol section 6.12). A single matrix over all BAMs.

_FC = config.get("featurecounts", {})


if COUNT_MATRIX_MODE:

    # The user supplied a counts matrix: ingest it into the canonical counts.txt
    # (validates sample columns against samples.tsv) instead of aligning + counting.
    rule ingest_counts:
        input:
            matrix=COUNT_MATRIX,
            samples=config["input"]["samples"],
        output:
            counts="results/counts/counts.txt",
            summary="results/counts/counts.txt.summary",
        log:
            "logs/ingest_counts.log",
        shell:
            "python workflow/scripts/ingest_counts.py --matrix {input.matrix:q} "
            "--samples {input.samples:q} --out {output.counts:q} --summary {output.summary:q} > {log:q} 2>&1"

else:

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
            "S=$(cat {input.strand:q}); "
            "featureCounts -a {input.gtf:q} -o {output.counts:q} -T {threads} "
            "--tmpDir {resources.tmpdir:q} "
            "{params.paired} -t {params.feature} -g {params.attribute} -s $S -Q 10 "
            "{input.bams:q} > {log:q} 2>&1"


rule quantification_check:
    input:
        summary="results/counts/counts.txt.summary",
    output:
        "checks/07_quantification_qc.json",
    benchmark:
        "benchmarks/07_quantification_qc.tsv"
    shell:
        "python workflow/scripts/summarize_quantification.py --summary {input.summary} --out {output}"
