# Alignment to a sorted BAM (STAR or HISAT2), indexing, and strandedness inference
# (protocol sections 6.9, 6.11). Salmon mode skips this file entirely -- it quantifies
# straight from the trimmed FASTQ in quantification.smk (no BAM).

# STAR read-filtering knobs. Defaults equal STAR's own defaults.
_STAR = config.get("star", {})
_STAR_MULTIMAP = _STAR.get("multimap_nmax", 10)
_STAR_MISMATCH_NOVER = _STAR.get("mismatch_nover_read_lmax", 1.0)
_STAR_TWOPASS = "Basic" if _STAR.get("twopass_mode", False) else "None"
_STAR_EXTRA = _STAR.get("extra", "")


def _star_bam_sort_ram_bytes(_wildcards, resources):
    # STAR defaults this extra sorting allocation to the genome-index size, which can be
    # too small for the output BAM of a compact genome. Use half of the job's effective
    # Snakemake reservation so the loaded index and mapping overhead retain equal headroom.
    try:
        mem_mb = int(resources.mem_mb)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("star_align requires a positive mem_mb resource") from None
    if mem_mb <= 0:
        raise ValueError("star_align requires a positive mem_mb resource")
    return mem_mb * 1_000_000 // 2


# samtools sort's -m is PER SORT THREAD and `-@ N` adds N threads to the main one, so the
# hardcoded -m 1G it replaces reserved (threads + 1) GB -- 9 GB against this rule's 8 GB
# declaration at 8 threads, while hisat2 runs alongside in the same pipe. Divide half the job's
# reservation across those threads+1 so the sort stays inside half and hisat2 keeps the rest.
# The floor keeps a high thread count from handing samtools a uselessly small buffer; below it
# the sort spills to many small temp files instead of failing.
_HISAT2_SORT_MEM_FLOOR_MB = 256


def _hisat2_sort_mem_mb(_wildcards, threads, resources):
    try:
        mem_mb = int(resources.mem_mb)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("hisat2_align requires a positive mem_mb resource") from None
    if mem_mb <= 0:
        raise ValueError("hisat2_align requires a positive mem_mb resource")
    return f"{max(mem_mb // 2 // (max(int(threads), 1) + 1), _HISAT2_SORT_MEM_FLOOR_MB)}M"


if not USE_SALMON:

    if USE_HISAT2 and SINGLE_END:

        rule hisat2_align:
            input:
                idx=HISAT2_INDEX_DIR,
                r1=lambda wc: aligner_read(wc.sample, 1),
            output:
                bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
                summary="results/aligned/{sample}_hisat2_summary.txt",
            threads:
                rule_threads("hisat2_align", 8)
            resources:
                mem_mb=rule_mem_mb("hisat2_align", 8),
            params:
                sort_mem=_hisat2_sort_mem_mb,
                # The build prefix, not the containing directory: a prebuilt HISAT2 index
                # (config.reference.hisat2_index) can name a prefix other than "genome".
                prefix=HISAT2_INDEX,
            benchmark:
                "benchmarks/hisat2_align_{sample}.tsv"
            log:
                "logs/hisat2_align_{sample}.log",
            shell:
                "hisat2 -p {threads} -x {params.prefix:q} -U {input.r1:q} "
                "--summary-file results/aligned/{wildcards.sample}_hisat2_summary.txt 2> {log} "
                "| samtools sort -@ {threads} -m {params.sort_mem} "
                "-T {resources.tmpdir}/sort_{wildcards.sample} -o {output.bam:q} - 2>> {log}"

    elif USE_HISAT2:

        rule hisat2_align:
            input:
                idx=HISAT2_INDEX_DIR,
                r1=lambda wc: aligner_read(wc.sample, 1),
                r2=lambda wc: aligner_read(wc.sample, 2),
            output:
                bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
                summary="results/aligned/{sample}_hisat2_summary.txt",
            threads:
                rule_threads("hisat2_align", 8)
            resources:
                mem_mb=rule_mem_mb("hisat2_align", 8),
            params:
                sort_mem=_hisat2_sort_mem_mb,
                # The build prefix, not the containing directory: a prebuilt HISAT2 index
                # (config.reference.hisat2_index) can name a prefix other than "genome".
                prefix=HISAT2_INDEX,
            benchmark:
                "benchmarks/hisat2_align_{sample}.tsv"
            log:
                "logs/hisat2_align_{sample}.log",
            shell:
                # hisat2 -> SAM on stdout -> samtools sort to the STAR-style BAM name so
                # featureCounts and the whole downstream are unchanged. Alignment summary
                # (overall rate) is written next to the BAM for inspection.
                "hisat2 -p {threads} -x {params.prefix:q} -1 {input.r1:q} -2 {input.r2:q} "
                "--summary-file results/aligned/{wildcards.sample}_hisat2_summary.txt 2> {log} "
                "| samtools sort -@ {threads} -m {params.sort_mem} "
                "-T {resources.tmpdir}/sort_{wildcards.sample} -o {output.bam:q} - 2>> {log}"

    else:

        rule star_align:
            input:
                index=STAR_INDEX,
                fastqs=lambda wc: aligner_fastqs(wc.sample),
            output:
                bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
                reads_per_gene="results/aligned/{sample}_ReadsPerGene.out.tab",
                log_final="results/aligned/{sample}_Log.final.out",
            threads:
                rule_threads("star_align", 8)
            resources:
                mem_mb=rule_mem_mb("star_align", 24),
            params:
                multimap=_STAR_MULTIMAP,
                mismatch_nover=_STAR_MISMATCH_NOVER,
                twopass=_STAR_TWOPASS,
                extra=_STAR_EXTRA,
                bam_sort_ram=_star_bam_sort_ram_bytes,
            benchmark:
                "benchmarks/star_align_{sample}.tsv"
            log:
                "logs/star_align_{sample}.log",
            shell:
                "rm -rf {resources.tmpdir}/star_{wildcards.sample} && "
                "STAR --runMode alignReads --genomeDir {input.index} "
                "--readFilesIn {input.fastqs:q} --readFilesCommand zcat "
                "--outSAMtype BAM SortedByCoordinate --quantMode GeneCounts "
                "--limitBAMsortRAM {params.bam_sort_ram} "
                "--runThreadN {threads} "
                "--outFilterMultimapNmax {params.multimap} "
                "--outFilterMismatchNoverReadLmax {params.mismatch_nover} "
                "--twopassMode {params.twopass} {params.extra} "
                "--outTmpDir {resources.tmpdir}/star_{wildcards.sample} "
                "--outFileNamePrefix results/aligned/{wildcards.sample}_ > {log} 2>&1"

    rule samtools_index:
        input:
            bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
        output:
            bai="results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai",
            flagstat="results/aligned/{sample}.flagstat.txt",
        benchmark:
            "benchmarks/samtools_index_{sample}.tsv"
        shell:
            "samtools index {input.bam} && samtools flagstat {input.bam} > {output.flagstat}"

    # config: featurecounts.strandedness, if the user sets it, is a manual override read
    # only by make_run_summary.py/report provenance today -- it is not wired into any
    # alignment or quantification rule, so there is nothing here for inference to
    # override. If it is ever wired to skip inference, per-sample inference must be
    # skipped the same way the single-sample inference would have been.
    if USE_HISAT2:

        # HISAT2 has no STAR-style ReadsPerGene table, so auto-detect strandedness by
        # counting each sample's BAM with featureCounts in forward (-s 1) and reverse
        # (-s 2) modes and comparing assigned fragments (same ratio thresholds as the STAR
        # path), independently per sample so a mixed-strandedness multi-study run is not
        # miscounted from one sample's answer. Paired libraries are counted with -p so
        # fragments are not split across strand buckets.
        rule infer_strandedness:
            input:
                bams=expand("results/aligned/{sample}_Aligned.sortedByCoord.out.bam", sample=SAMPLES),
                gtf=ANNOTATION_GTF,
            output:
                legacy="results/aligned/strandedness.txt",
                per_sample="results/aligned/strandedness_per_sample.tsv",
            params:
                bam_args=lambda wc, input: " ".join(f"--bam {shlex.quote(b)}" for b in input.bams),
                paired="--paired" if ALL_PAIRED else "",
                feature=config.get("featurecounts", {}).get("feature_type", "exon"),
                attribute=config.get("featurecounts", {}).get("attribute_type", "gene_id"),
            threads:
                rule_threads("infer_strandedness", 4)
            log:
                "logs/infer_strandedness.log",
            shell:
                "python workflow/scripts/infer_strandedness_fc.py {params.bam_args} "
                "--gtf {input.gtf:q} --out {output.legacy:q} --per-sample-out {output.per_sample:q} "
                "--threads {threads} --tmpdir {resources.tmpdir:q} {params.paired} "
                "--feature {params.feature} --attribute {params.attribute} > {log} 2>&1"

    else:

        rule infer_strandedness:
            input:
                tabs=expand("results/aligned/{sample}_ReadsPerGene.out.tab", sample=SAMPLES),
            output:
                legacy="results/aligned/strandedness.txt",
                per_sample="results/aligned/strandedness_per_sample.tsv",
            shell:
                "python workflow/scripts/infer_strandedness_star.py --out {output.legacy:q} "
                "--per-sample-out {output.per_sample:q} {input.tabs:q}"

        rule alignment_check:
            input:
                logs=expand("results/aligned/{sample}_Log.final.out", sample=SAMPLES),
                flagstats=expand("results/aligned/{sample}.flagstat.txt", sample=SAMPLES),
            output:
                "checks/06_alignment_qc.json",
            benchmark:
                "benchmarks/06_alignment_qc.tsv"
            shell:
                "python workflow/scripts/summarize_alignment.py --logs {input.logs} --out {output}"
