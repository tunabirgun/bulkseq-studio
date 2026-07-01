# Ribosomal RNA filtering (optional post-trim, pre-alignment). SortMeRNA (reference-based,
# default) or RiboDetector (reference-free). Both paired-end and single-end are supported
# (SINGLE_END selects the variant); both emit the canonical filtered reads —
# results/rrna_filtered/{sample}_{1,2}.fastq.gz (paired) or {sample}.fastq.gz (single) — so
# the aligner input is identical. Rules exist only when rRNA filtering is enabled.

if RRNA_FILTER and RRNA_TOOL == "ribodetector":

    _RD_GUARD = (
        "command -v ribodetector_cpu >/dev/null 2>&1 || {{ echo 'ribodetector is not installed "
        "in the bulkseq environment; the RiboDetector rRNA filter needs it. In the app open Setup "
        "and click Install / repair the environment (full profile), then re-run.' >&2; exit 1; }}; "
    )
    _RD = config.get("ribodetector", {})
    _RD_LEN = ("LEN=$(zcat {input.r1:q} | head -n 400 | "
               "awk 'NR%4==2{{s+=length($0);n++}} END{{if(n>0) print int(s/n); else print 100}}') && ")

    if SINGLE_END:

        rule ribodetector_se:
            input:
                r1=lambda wc: _reads_pre_rrna(wc.sample, 1),
            output:
                r1="results/rrna_filtered/{sample}.fastq.gz",
                qclog="results/qc/ribodetector/{sample}.log",
            params:
                chunk=_RD.get("chunk_size", 256), ensure=_RD.get("ensure", "norrna"),
            threads: rule_threads("sortmerna", 4)
            resources: mem_mb=rule_mem_mb("sortmerna", 12),
            benchmark: "benchmarks/ribodetector_{sample}.tsv"
            log: "logs/ribodetector_{sample}.log",
            shell:
                _RD_GUARD +
                "mkdir -p results/rrna_filtered results/qc/ribodetector && " + _RD_LEN +
                "ribodetector_cpu -t {threads} -l $LEN -i {input.r1:q} -e {params.ensure} "
                "--chunk_size {params.chunk} -o {output.r1:q} > {output.qclog:q} 2>&1; "
                "cp {output.qclog:q} {log:q}"

    else:

        rule ribodetector_pe:
            input:
                r1=lambda wc: _reads_pre_rrna(wc.sample, 1),
                r2=lambda wc: _reads_pre_rrna(wc.sample, 2),
            output:
                r1="results/rrna_filtered/{sample}_1.fastq.gz",
                r2="results/rrna_filtered/{sample}_2.fastq.gz",
                qclog="results/qc/ribodetector/{sample}.log",
            params:
                chunk=_RD.get("chunk_size", 256), ensure=_RD.get("ensure", "norrna"),
            threads: rule_threads("sortmerna", 4)
            resources: mem_mb=rule_mem_mb("sortmerna", 12),
            benchmark: "benchmarks/ribodetector_{sample}.tsv"
            log: "logs/ribodetector_{sample}.log",
            shell:
                _RD_GUARD +
                "mkdir -p results/rrna_filtered results/qc/ribodetector && " + _RD_LEN +
                "ribodetector_cpu -t {threads} -l $LEN -i {input.r1:q} {input.r2:q} -e {params.ensure} "
                "--chunk_size {params.chunk} -o {output.r1:q} {output.r2:q} > {output.qclog:q} 2>&1; "
                "cp {output.qclog:q} {log:q}"

elif RRNA_FILTER:

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

    if SINGLE_END:

        rule sortmerna_se:
            input:
                r1=lambda wc: _reads_pre_rrna(wc.sample, 1),
                db=RRNA_DB, idx=RRNA_IDX,
            output:
                r1="results/rrna_filtered/{sample}.fastq.gz",
                qclog="results/qc/sortmerna/{sample}.log",
            params:
                wd=lambda wc: f"results/rrna/{wc.sample}",
            threads: rule_threads("sortmerna", 4)
            resources: mem_mb=rule_mem_mb("sortmerna", 12),
            benchmark: "benchmarks/sortmerna_{sample}.tsv"
            log: "logs/sortmerna_{sample}.log",
            shell:
                _SMR_GUARD +
                "rm -rf {params.wd} && mkdir -p {params.wd}/out results/rrna_filtered results/qc/sortmerna && "
                "sortmerna --ref {input.db:q} --idx-dir {input.idx:q} --workdir {params.wd} "
                "--aligned {params.wd}/out/aligned --other {params.wd}/out/other "
                "--reads {input.r1:q} "
                "--fastx -m {resources.mem_mb} --threads {threads} > {log} 2>&1 && "
                "mv {params.wd}/out/other.fq.gz {output.r1:q} && "
                "cp {params.wd}/out/aligned.log {output.qclog:q} && "
                "rm -rf {params.wd}"

    else:

        rule sortmerna_pe:
            input:
                r1=lambda wc: _reads_pre_rrna(wc.sample, 1),
                r2=lambda wc: _reads_pre_rrna(wc.sample, 2),
                db=RRNA_DB, idx=RRNA_IDX,
            output:
                r1="results/rrna_filtered/{sample}_1.fastq.gz",
                r2="results/rrna_filtered/{sample}_2.fastq.gz",
                qclog="results/qc/sortmerna/{sample}.log",
            params:
                paired=_SMR_PAIRED,
                wd=lambda wc: f"results/rrna/{wc.sample}",
            threads: rule_threads("sortmerna", 4)
            resources: mem_mb=rule_mem_mb("sortmerna", 12),
            benchmark: "benchmarks/sortmerna_{sample}.tsv"
            log: "logs/sortmerna_{sample}.log",
            shell:
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
