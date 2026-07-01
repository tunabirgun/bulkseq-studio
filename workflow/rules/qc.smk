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


# SortMeRNA per-sample logs (rRNA %) feed the MultiQC sortmerna module when filtering is on.
# RiboDetector has no MultiQC module (its log still lands in results/qc/ribodetector/), so it
# contributes nothing to require here; it runs transitively as the aligner's input.
def _multiqc_rrna_inputs():
    if RRNA_FILTER and RRNA_TOOL == "sortmerna":
        return expand("results/qc/sortmerna/{sample}.log", sample=SAMPLES)
    return []


# QC inputs depend on which steps run: FastQC-pre (raw reads), FastQC-post (trimmed reads),
# and fastp's JSON only exist when those steps are enabled. Without this gate, MultiQC would
# require outputs that are never produced when trimming / FastQC is turned off.
def _multiqc_qc_inputs():
    items = []
    if FASTQC_PRE:
        items += expand("results/qc/fastqc_raw/{sample}", sample=SAMPLES)
    if FASTQC_POST:
        items += expand("results/qc/fastqc_trim/{sample}", sample=SAMPLES)
    # fastp's JSON only exists on the fastp trimmer. Trim Galore / Trimmomatic write their own
    # QC reports into results/qc/{trim_galore,trimmomatic}/, which MultiQC scans (they are
    # produced transitively before MultiQC via the trimmed reads the aligner consumes).
    if TRIMMING and TRIMMER == "fastp":
        items += expand("results/qc/fastp/{sample}.json", sample=SAMPLES)
    # FastQ Screen contamination report (native MultiQC module) when the screen is on.
    if CONTAM_SCREEN:
        items += expand("results/qc/fastq_screen/{sample}_screen.txt", sample=SAMPLES)
    # RSeQC extended alignment QC (native MultiQC modules) when enabled.
    if RSEQC_ON:
        items += expand("results/qc/rseqc/{sample}.read_distribution.txt", sample=SAMPLES)
        items += expand("results/qc/rseqc/{sample}.geneBodyCoverage.txt", sample=SAMPLES)
    return items


_MQC_SCAN = "results/qc results/salmon results/counts" if USE_SALMON else "results/qc results/aligned results/counts"


rule multiqc:
    input:
        _multiqc_qc_inputs(),
        _multiqc_align_inputs(),
        _multiqc_rrna_inputs(),
        COUNTS_SUMMARY,
    output:
        "results/qc/multiqc/multiqc_report.html",
    benchmark:
        "benchmarks/multiqc.tsv"
    log:
        "logs/multiqc.log",
    shell:
        "multiqc " + _MQC_SCAN + " -o results/qc/multiqc -n multiqc_report -f > {log} 2>&1"


# Contamination screening (FastQ Screen): optional QC that aligns a read subsample against a
# panel of reference genomes and reports the % hitting each. The genome panel (pre-built
# bowtie2 indexes + fastq_screen.conf) is fetched on demand into references/fastq_screen_db,
# mirroring the SortMeRNA on-demand db pattern. It is a QC report, not a filter.
if CONTAM_SCREEN:

    _FS_GUARD = (
        "command -v fastq_screen >/dev/null 2>&1 || {{ echo 'fastq_screen is not installed in "
        "the bulkseq environment; contamination screening needs it. In the app open Setup and "
        "click Install / repair the environment, then re-run.' >&2; exit 1; }}; "
    )
    _FS_CONF = "references/fastq_screen_db/FastQ_Screen_Genomes/fastq_screen.conf"
    _CONTAM = config.get("contamination", {})

    rule fastq_screen_db:
        output:
            conf=_FS_CONF,
        params:
            outdir="references/fastq_screen_db",
        log:
            "logs/fastq_screen_db.log",
        shell:
            _FS_GUARD +
            "mkdir -p {params.outdir} && "
            "fastq_screen --get_genomes --outdir {params.outdir} > {log} 2>&1"

    rule fastq_screen:
        input:
            r1=lambda wc: aligner_read(wc.sample, 1),
            conf=_FS_CONF,
        output:
            txt="results/qc/fastq_screen/{sample}_screen.txt",
        params:
            subset=_CONTAM.get("subset", 100000),
        threads:
            rule_threads("fastq_screen", 4)
        benchmark:
            "benchmarks/fastq_screen_{sample}.tsv"
        log:
            "logs/fastq_screen_{sample}.log",
        shell:
            # fastq_screen names outputs by the input basename; rename to {sample}_screen.* so the
            # MultiQC sample names are clean and the declared output is deterministic.
            _FS_GUARD +
            "mkdir -p results/qc/fastq_screen && "
            "BASE=$(basename {input.r1:q}); BASE=${{BASE%.fastq.gz}}; BASE=${{BASE%.fq.gz}} && "
            "fastq_screen --aligner bowtie2 --conf {input.conf:q} --subset {params.subset} "
            "--threads {threads} --outdir results/qc/fastq_screen --force {input.r1:q} > {log} 2>&1 && "
            "mv results/qc/fastq_screen/${{BASE}}_screen.txt {output.txt:q} && "
            "(mv results/qc/fastq_screen/${{BASE}}_screen.html results/qc/fastq_screen/{wildcards.sample}_screen.html 2>/dev/null || true) && "
            "(mv results/qc/fastq_screen/${{BASE}}_screen.png results/qc/fastq_screen/{wildcards.sample}_screen.png 2>/dev/null || true)"


# RSeQC extended alignment QC (optional): read genomic-context distribution + 5'->3' gene-body
# coverage. Needs a genome BAM (gated off the Salmon route) and a BED12 gene model built from
# the GTF. Native MultiQC modules pick up the outputs from results/qc/rseqc.
if RSEQC_ON:

    _RSEQC_GUARD = (
        "command -v read_distribution.py >/dev/null 2>&1 || {{ echo 'rseqc is not installed in "
        "the bulkseq environment; extended alignment QC needs it. In the app open Setup and click "
        "Install / repair the environment, then re-run.' >&2; exit 1; }}; "
    )

    rule make_bed12:
        input:
            gtf=ANNOTATION_GTF,
        output:
            bed="references/annotation.bed12",
        log:
            "logs/make_bed12.log",
        shell:
            _RSEQC_GUARD +
            "gtfToGenePred {input.gtf:q} references/annotation.genePred 2> {log} && "
            "genePredToBed references/annotation.genePred {output.bed:q} 2>> {log}"

    rule rseqc_read_distribution:
        input:
            bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
            bai="results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai",
            bed="references/annotation.bed12",
        output:
            "results/qc/rseqc/{sample}.read_distribution.txt",
        benchmark:
            "benchmarks/rseqc_read_distribution_{sample}.tsv"
        log:
            "logs/rseqc_read_distribution_{sample}.log",
        shell:
            _RSEQC_GUARD +
            "mkdir -p results/qc/rseqc && "
            "read_distribution.py -i {input.bam:q} -r {input.bed:q} > {output:q} 2> {log}"

    rule rseqc_genebody_coverage:
        input:
            bam="results/aligned/{sample}_Aligned.sortedByCoord.out.bam",
            bai="results/aligned/{sample}_Aligned.sortedByCoord.out.bam.bai",
            bed="references/annotation.bed12",
        output:
            "results/qc/rseqc/{sample}.geneBodyCoverage.txt",
        params:
            prefix=lambda wc: f"results/qc/rseqc/{wc.sample}",
        benchmark:
            "benchmarks/rseqc_genebody_{sample}.tsv"
        log:
            "logs/rseqc_genebody_{sample}.log",
        shell:
            _RSEQC_GUARD +
            "mkdir -p results/qc/rseqc && "
            "geneBody_coverage.py -i {input.bam:q} -r {input.bed:q} -o {params.prefix} > {log} 2>&1"
