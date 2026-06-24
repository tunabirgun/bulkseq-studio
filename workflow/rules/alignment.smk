# Alignment to a sorted BAM (STAR or HISAT2), indexing, and strandedness inference
# (protocol sections 6.9, 6.11). Salmon mode skips this file entirely -- it quantifies
# straight from the trimmed FASTQ in quantification.smk (no BAM).

# STAR read-filtering knobs. Defaults equal STAR's own defaults.
_STAR = config.get("star", {})
_STAR_MULTIMAP = _STAR.get("multimap_nmax", 10)
_STAR_MISMATCH_NOVER = _STAR.get("mismatch_nover_read_lmax", 1.0)
_STAR_TWOPASS = "Basic" if _STAR.get("twopass_mode", False) else "None"
_STAR_EXTRA = _STAR.get("extra", "")


if not USE_SALMON:

    if USE_HISAT2:

        rule hisat2_align:
            input:
                idx=HISAT2_INDEX_DIR,
                r1="results/trimmed/{sample}_1.trim.fastq.gz",
                r2="results/trimmed/{sample}_2.trim.fastq.gz",
            output:
                bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
            threads:
                rule_threads("hisat2_align", 8)
            resources:
                mem_mb=rule_mem_mb("hisat2_align", 8),
            benchmark:
                "benchmarks/hisat2_align_{sample}.tsv"
            log:
                "logs/hisat2_align_{sample}.log",
            shell:
                # hisat2 -> SAM on stdout -> samtools sort to the STAR-style BAM name so
                # featureCounts and the whole downstream are unchanged. Alignment summary
                # (overall rate) is written next to the BAM for inspection.
                "hisat2 -p {threads} -x {input.idx}/genome -1 {input.r1:q} -2 {input.r2:q} "
                "--summary-file results/aligned/{wildcards.sample}_hisat2_summary.txt 2> {log} "
                "| samtools sort -@ {threads} -m 1G "
                "-T {resources.tmpdir}/sort_{wildcards.sample} -o {output.bam:q} - 2>> {log}"

    else:

        rule star_align:
            input:
                index=STAR_INDEX,
                fastqs=lambda wc: trimmed_fastqs(wc.sample),
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
            benchmark:
                "benchmarks/star_align_{sample}.tsv"
            log:
                "logs/star_align_{sample}.log",
            shell:
                "rm -rf {resources.tmpdir}/star_{wildcards.sample} && "
                "STAR --runMode alignReads --genomeDir {input.index} "
                "--readFilesIn {input.fastqs} --readFilesCommand zcat "
                "--outSAMtype BAM SortedByCoordinate --quantMode GeneCounts "
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

    if USE_HISAT2:

        # HISAT2 has no STAR-style ReadsPerGene table, so auto-detect strandedness by
        # counting the first sample's BAM with featureCounts in forward (-s 1) and reverse
        # (-s 2) modes and comparing assigned fragments (same ratio thresholds as the STAR
        # path). Paired libraries are counted with -p so fragments are not split across
        # strand buckets. This matches STAR's behavior, so a stranded library is not
        # silently miscounted as unstranded.
        rule infer_strandedness:
            input:
                bam="results/aligned/" + (FIRST_SAMPLE or "NA") + "_Aligned.sortedByCoord.out.bam",
                gtf=ANNOTATION_GTF,
            output:
                "results/aligned/strandedness.txt",
            params:
                paired="--paired" if ALL_PAIRED else "",
                feature=config.get("featurecounts", {}).get("feature_type", "exon"),
                attribute=config.get("featurecounts", {}).get("attribute_type", "gene_id"),
            threads:
                rule_threads("infer_strandedness", 4)
            log:
                "logs/infer_strandedness.log",
            shell:
                "python workflow/scripts/infer_strandedness_fc.py --bam {input.bam:q} "
                "--gtf {input.gtf:q} --out {output:q} --threads {threads} "
                "--tmpdir {resources.tmpdir:q} {params.paired} "
                "--feature {params.feature} --attribute {params.attribute} > {log} 2>&1"

    else:

        rule infer_strandedness:
            input:
                "results/aligned/" + (FIRST_SAMPLE or "NA") + "_ReadsPerGene.out.tab",
            output:
                "results/aligned/strandedness.txt",
            run:
                fwd = rev = 0
                with open(input[0], encoding="utf-8") as handle:
                    for line in handle:
                        if line.startswith("N_"):
                            continue
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) < 4:
                            continue
                        try:
                            fwd += int(parts[2])
                            rev += int(parts[3])
                        except ValueError:
                            continue
                total = fwd + rev
                ratio = (rev / total) if total else 0.5
                strand = 2 if ratio > 0.7 else (1 if ratio < 0.3 else 0)
                with open(output[0], "w", encoding="utf-8") as out:
                    out.write(f"{strand}\n")

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
