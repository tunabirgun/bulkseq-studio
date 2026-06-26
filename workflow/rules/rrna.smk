# Ribosomal RNA filtering with SortMeRNA (protocol: optional post-trim, pre-alignment).
# Rules exist only when rRNA filtering is enabled (workflow.rrna_filtering -> RRNA_FILTER);
# otherwise the aligner/quantifier consumes the trimmed FASTQ directly (aligner_fastqs /
# aligner_read in the Snakefile). Paired-end only (single-end is rejected up front).
#
# Design (validated against sortmerna 4.3.7): the reference is indexed ONCE into the shared
# RRNA_IDX (sortmerna_index, --index 1), which every per-sample job reads via --idx-dir. Each
# sample gets its own --workdir (own kvdb/readb/out) so parallel jobs never share writable
# state. --paired_in flags a pair as rRNA if either mate aligns, so --other holds only
# fully-non-rRNA pairs; --out2 splits them into _fwd/_rev files. SortMeRNA writes .fq.gz
# (gz preserved from gz input), renamed to the {sample}_{1,2}.fastq.gz the aligner expects.

if RRNA_FILTER:

    _SMR = config.get("sortmerna", {})
    _SMR_PAIRED = "--paired_out" if str(_SMR.get("paired_mode", "paired_in")).lower() == "paired_out" else "--paired_in"
    _SMR_GUARD = (
        "command -v sortmerna >/dev/null 2>&1 || {{ echo 'sortmerna is not installed in the "
        "bulkseq environment; rRNA filtering needs it. In the app open Setup and click Install "
        "/ repair core environment (or update the env from workflow/envs/bulkseq_core.yaml), "
        "then re-run.' >&2; exit 1; }}; "
    )

    rule rrna_db:
        output:
            RRNA_DB,
        params:
            database=_SMR.get("database") or "",
        log:
            "logs/rrna_db.log",
        shell:
            "python workflow/scripts/fetch_rrna_db.py --out {output:q} --database '{params.database}' > {log} 2>&1"

    # Build the SortMeRNA index once; per-sample jobs share it read-only via --idx-dir.
    rule sortmerna_index:
        input:
            db=RRNA_DB,
        output:
            directory(RRNA_IDX),
        threads:
            rule_threads("sortmerna", 4)
        resources:
            mem_mb=rule_mem_mb("sortmerna", 12),
        benchmark:
            "benchmarks/sortmerna_index.tsv"
        log:
            "logs/sortmerna_index.log",
        shell:
            _SMR_GUARD +
            "mkdir -p {output:q} && "
            "sortmerna --ref {input.db:q} --idx-dir {output:q} --index 1 "
            "-m {resources.mem_mb} --threads {threads} > {log} 2>&1"

    rule sortmerna_pe:
        input:
            r1=lambda wc: _reads_pre_rrna(wc.sample, 1),
            r2=lambda wc: _reads_pre_rrna(wc.sample, 2),
            db=RRNA_DB,
            idx=RRNA_IDX,
        output:
            r1="results/rrna_filtered/{sample}_1.fastq.gz",
            r2="results/rrna_filtered/{sample}_2.fastq.gz",
            qclog="results/qc/sortmerna/{sample}.log",
        params:
            paired=_SMR_PAIRED,
            wd=lambda wc: f"results/rrna/{wc.sample}",
        threads:
            rule_threads("sortmerna", 4)
        resources:
            mem_mb=rule_mem_mb("sortmerna", 12),
        benchmark:
            "benchmarks/sortmerna_{sample}.tsv"
        log:
            "logs/sortmerna_{sample}.log",
        shell:
            # Fresh per-sample workdir each run (kvdb must be empty; sortmerna errors otherwise).
            _SMR_GUARD +
            "rm -rf {params.wd} && mkdir -p {params.wd}/out results/rrna_filtered results/qc/sortmerna && "
            "sortmerna --ref {input.db:q} --idx-dir {input.idx:q} --workdir {params.wd} "
            "--aligned {params.wd}/out/aligned --other {params.wd}/out/other "
            "--reads {input.r1:q} --reads {input.r2:q} "
            "--fastx {params.paired} --out2 -m {resources.mem_mb} --threads {threads} > {log} 2>&1 && "
            "mv {params.wd}/out/other_fwd.fq.gz {output.r1:q} && "
            "mv {params.wd}/out/other_rev.fq.gz {output.r2:q} && "
            "cp {params.wd}/out/aligned.log {output.qclog:q} && "
            "rm -rf {params.wd}"
