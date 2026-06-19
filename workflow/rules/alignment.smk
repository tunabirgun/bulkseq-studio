# STAR alignment to a sorted BAM, indexing, and strandedness inference
# (protocol sections 6.9, 6.11).


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
    benchmark:
        "benchmarks/star_align_{sample}.tsv"
    log:
        "logs/star_align_{sample}.log",
    shell:
        "STAR --runMode alignReads --genomeDir {input.index} "
        "--readFilesIn {input.fastqs} --readFilesCommand zcat "
        "--outSAMtype BAM SortedByCoordinate --quantMode GeneCounts "
        "--runThreadN {threads} "
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


rule infer_strandedness:
    input:
        "results/aligned/" + (FIRST_SAMPLE or "NA") + "_ReadsPerGene.out.tab",
    output:
        "results/aligned/strandedness.txt",
    run:
        # STAR ReadsPerGene.out.tab columns: gene, unstranded, fwd(-s 1), rev(-s 2).
        # Compare fwd vs rev (not max-over-three, which always picks unstranded).
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
