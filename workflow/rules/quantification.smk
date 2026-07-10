# Gene-level quantification (protocol section 6.12). Three routes, all converging on
# the canonical results/counts/counts.txt: featureCounts on BAMs (STAR/HISAT2), Salmon
# pseudo-alignment + tximport (no BAM), or ingest of a user-supplied counts matrix.

_FC = config.get("featurecounts", {})


if COUNT_MATRIX_MODE:

    # The user supplied a counts matrix: ingest it into the canonical counts.txt
    # (validates sample columns against samples.tsv) instead of aligning + counting.
    rule ingest_counts:
        input:
            matrix=COUNT_MATRIX,
            samples=config["input"]["samples"],
        output:
            counts=COUNTS_RAW,
            summary=COUNTS_SUMMARY,
        params:
            estimated=("--estimated-counts" if INPUT.get("estimated_counts") else ""),
        log:
            "logs/ingest_counts.log",
        shell:
            "python workflow/scripts/ingest_counts.py --matrix {input.matrix:q} "
            "--samples {input.samples:q} --out {output.counts:q} --summary {output.summary:q} "
            "{params.estimated} > {log:q} 2>&1"

elif USE_SALMON:

    # Salmon mapping-based quantification straight from the trimmed FASTQ (no BAM).
    if SINGLE_END:

        rule salmon_quant:
            input:
                index=SALMON_INDEX,
                r1=lambda wc: aligner_read(wc.sample, 1),
            output:
                quant="results/salmon/{sample}/quant.sf",
            threads:
                rule_threads("salmon_quant", 8)
            resources:
                mem_mb=rule_mem_mb("salmon_quant", 8),
            benchmark:
                "benchmarks/salmon_quant_{sample}.tsv"
            log:
                "logs/salmon_quant_{sample}.log",
            shell:
                "salmon quant -i {input.index:q} -l A -r {input.r1:q} "
                "-p {threads} --validateMappings "
                "-o results/salmon/{wildcards.sample} > {log} 2>&1"

    else:

        rule salmon_quant:
            input:
                index=SALMON_INDEX,
                r1=lambda wc: aligner_read(wc.sample, 1),
                r2=lambda wc: aligner_read(wc.sample, 2),
            output:
                quant="results/salmon/{sample}/quant.sf",
            threads:
                rule_threads("salmon_quant", 8)
            resources:
                mem_mb=rule_mem_mb("salmon_quant", 8),
            benchmark:
                "benchmarks/salmon_quant_{sample}.tsv"
            log:
                "logs/salmon_quant_{sample}.log",
            shell:
                "salmon quant -i {input.index:q} -l A -1 {input.r1:q} -2 {input.r2:q} "
                "-p {threads} --validateMappings --gcBias "
                "-o results/salmon/{wildcards.sample} > {log} 2>&1"

    # tximport (lengthScaledTPM) -> gene-level counts in featureCounts layout so DESeq2
    # and everything downstream are unchanged.
    rule salmon_tximport:
        input:
            quants=expand("results/salmon/{sample}/quant.sf", sample=SAMPLES),
            tx2gene="references/tx2gene.tsv",
        output:
            counts=COUNTS_RAW,
            summary=COUNTS_SUMMARY,
        log:
            "logs/salmon_tximport.log",
        script:
            "../scripts/salmon_tximport.R"

elif USE_STAR_GENECOUNTS:

    # Build the canonical counts.txt from STAR's --quantMode GeneCounts per-sample
    # ReadsPerGene.out.tab, picking the strand-matched column (0=unstranded, 1=forward,
    # 2=reverse) from results/aligned/strandedness.txt. No second pass -- STAR already
    # counted during alignment. featureCounts layout so DESeq2 and downstream are unchanged.
    # bais are an input only to keep samtools_index/flagstat in the DAG (MultiQC parity).
    rule star_genecounts:
        input:
            tabs=expand("results/aligned/{sample}_ReadsPerGene.out.tab", sample=SAMPLES),
            bais=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai", sample=SAMPLES),
            strand="results/aligned/strandedness.txt",
        output:
            counts=COUNTS_RAW,
            summary=COUNTS_SUMMARY,
        log:
            "logs/star_genecounts.log",
        shell:
            "S=$(cat {input.strand:q}); "
            "python workflow/scripts/build_star_genecounts.py --strand $S "
            "--out {output.counts:q} --summary {output.summary:q} {input.tabs:q} > {log:q} 2>&1"


else:

    rule featurecounts:
        input:
            bams=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam", sample=SAMPLES),
            bais=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai", sample=SAMPLES),
            gtf=ANNOTATION_GTF,
            strand="results/aligned/strandedness.txt",
        output:
            counts=COUNTS_RAW,
            summary=COUNTS_SUMMARY,
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


# Organellar (mitochondrial + chloroplast) gene handling. Only wired when the user
# chose discard/separate on an alignment route; otherwise the quant rules write
# counts.txt directly (COUNTS_RAW == COUNTS_FILE) and these rules do not exist.
if ORGANELLAR_FILTER and ORGANELLAR_MODE == "discard":

    rule filter_organellar:
        input:
            counts=COUNTS_RAW,
            genome=GENOME_FA,
            gtf=ANNOTATION_GTF,
        output:
            counts=COUNTS_FILE,
        log:
            "logs/filter_organellar.log",
        shell:
            "python workflow/scripts/filter_organellar.py --counts {input.counts:q} "
            "--genome {input.genome:q} --gtf {input.gtf:q} --mode discard "
            "--out-counts {output.counts:q} --log {log:q}"

elif ORGANELLAR_FILTER and ORGANELLAR_MODE == "separate":

    rule filter_organellar:
        input:
            counts=COUNTS_RAW,
            genome=GENOME_FA,
            gtf=ANNOTATION_GTF,
        output:
            counts=COUNTS_FILE,
            organellar="results/organellar/organellar_counts.txt",
            summary="results/organellar/organellar_summary.tsv",
        log:
            "logs/filter_organellar.log",
        shell:
            "python workflow/scripts/filter_organellar.py --counts {input.counts:q} "
            "--genome {input.genome:q} --gtf {input.gtf:q} --mode separate "
            "--out-counts {output.counts:q} --organellar-dir results/organellar --log {log:q}"


rule quantification_check:
    input:
        summary=COUNTS_SUMMARY,
    output:
        "checks/07_quantification_qc.json",
    benchmark:
        "benchmarks/07_quantification_qc.tsv"
    shell:
        "python workflow/scripts/summarize_quantification.py --summary {input.summary} --out {output}"
