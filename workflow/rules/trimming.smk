# Adapter and quality trimming (protocol section 6.4). Both paired-end and single-end are
# supported (SINGLE_END selects the variant). Three interchangeable trimmers all emit the
# canonical trimmed reads — results/trimmed/{sample}_{1,2}.trim.fastq.gz (paired) or
# results/trimmed/{sample}.trim.fastq.gz (single) — so the whole downstream is identical
# regardless of trimmer or layout. fastp is the default; Trim Galore and Trimmomatic are
# opt-in (workflow.trimmer -> TRIMMER). The quality (-q) and min-length settings from the
# fastp block apply to whichever trimmer runs.

_FASTP = config.get("fastp", {})
_TM = config.get("trimmomatic", {})
_Q = _FASTP.get("qualified_quality_phred", 15)
_MINLEN = _FASTP.get("length_required", 36)
_POLYG = "-g" if _FASTP.get("trim_poly_g", False) else ""
_POLYX = "-x" if _FASTP.get("trim_poly_x", False) else ""
_U = _FASTP.get("unqualified_percent_limit", 40)
_TM_SW = f"{_TM.get('sliding_window_size', 4)}:{_TM.get('sliding_window_quality', 15)}"
_TM_LEAD = _TM.get("leading", 3)
_TM_TRAIL = _TM.get("trailing", 3)

_PATH_PREPEND = "export PATH=\"${{MAMBA_ROOT_PREFIX:-$HOME/micromamba}}/envs/bulkseq/bin:${{PATH}}\" && "
_TG_GUARD = (
    # Put the env bin on PATH first (see reference.smk): the activated PATH is not
    # reliably inherited by the rule shell, so command -v would fail even when installed.
    _PATH_PREPEND +
    "command -v trim_galore >/dev/null 2>&1 || {{ echo 'trim_galore is not installed in the "
    "bulkseq environment; the Trim Galore trimmer needs it. In the app open Setup and click "
    "Install / repair the environment, then re-run.' >&2; exit 1; }}; "
)
_TM_GUARD = (
    _PATH_PREPEND +
    "command -v trimmomatic >/dev/null 2>&1 || {{ echo 'trimmomatic is not installed in the "
    "bulkseq environment; the Trimmomatic trimmer needs it. In the app open Setup and click "
    "Install / repair the environment, then re-run.' >&2; exit 1; }}; "
)
_ADAP_FIND = (
    "ADAP=$(find \"${{CONDA_PREFIX:-$MAMBA_ROOT_PREFIX/envs/bulkseq}}/share\" "
    "\"$MAMBA_ROOT_PREFIX/envs/bulkseq/share\" -name %s 2>/dev/null | head -1) && "
)


if TRIMMER == "trim-galore":

    if SINGLE_END:

        rule trim_galore_se:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
            output:
                r1="results/trimmed/{sample}.trim.fastq.gz",
            params:
                wd=lambda wc: f"results/trimmed/_tg_{wc.sample}",
                q=_Q, length=_MINLEN,
                cores=lambda wc, threads: min(threads, 4),
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/trim_galore_{sample}.tsv"
            log: "logs/trim_galore_{sample}.log",
            shell:
                _TG_GUARD +
                "rm -rf {params.wd} && mkdir -p {params.wd} results/qc/trim_galore && "
                "trim_galore --gzip --basename {wildcards.sample} --quality {params.q} "
                "--length {params.length} --cores {params.cores} --output_dir {params.wd} "
                "{input.r1} > {log} 2>&1 && "
                "mv {params.wd}/{wildcards.sample}_trimmed.fq.gz {output.r1} && "
                "(cp {params.wd}/*_trimming_report.txt results/qc/trim_galore/ 2>/dev/null || true) && "
                "rm -rf {params.wd}"

    else:

        rule trim_galore_pe:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
                r2=lambda wc: raw_fastq(wc.sample, 2),
            output:
                r1="results/trimmed/{sample}_1.trim.fastq.gz",
                r2="results/trimmed/{sample}_2.trim.fastq.gz",
            params:
                wd=lambda wc: f"results/trimmed/_tg_{wc.sample}",
                q=_Q, length=_MINLEN,
                cores=lambda wc, threads: min(threads, 4),
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/trim_galore_{sample}.tsv"
            log: "logs/trim_galore_{sample}.log",
            shell:
                _TG_GUARD +
                "rm -rf {params.wd} && mkdir -p {params.wd} results/qc/trim_galore && "
                "trim_galore --paired --gzip --basename {wildcards.sample} --quality {params.q} "
                "--length {params.length} --cores {params.cores} --output_dir {params.wd} "
                "{input.r1} {input.r2} > {log} 2>&1 && "
                "mv {params.wd}/{wildcards.sample}_val_1.fq.gz {output.r1} && "
                "mv {params.wd}/{wildcards.sample}_val_2.fq.gz {output.r2} && "
                "(cp {params.wd}/*_trimming_report.txt results/qc/trim_galore/ 2>/dev/null || true) && "
                "rm -rf {params.wd}"

elif TRIMMER == "trimmomatic":

    if SINGLE_END:

        rule trimmomatic_se:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
            output:
                r1="results/trimmed/{sample}.trim.fastq.gz",
            params:
                length=_MINLEN, sw=_TM_SW, leading=_TM_LEAD, trailing=_TM_TRAIL,
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/trimmomatic_{sample}.tsv"
            log: "logs/trimmomatic_{sample}.log",
            shell:
                _TM_GUARD + "mkdir -p results/qc/trimmomatic && " +
                (_ADAP_FIND % "TruSeq3-SE.fa") +
                "trimmomatic SE -threads {threads} {input.r1} {output.r1} "
                "ILLUMINACLIP:$ADAP:2:30:10 LEADING:{params.leading} TRAILING:{params.trailing} "
                "SLIDINGWINDOW:{params.sw} MINLEN:{params.length} "
                "> results/qc/trimmomatic/{wildcards.sample}.log 2>&1; "
                "cp results/qc/trimmomatic/{wildcards.sample}.log {log}"

    else:

        rule trimmomatic_pe:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
                r2=lambda wc: raw_fastq(wc.sample, 2),
            output:
                r1="results/trimmed/{sample}_1.trim.fastq.gz",
                r2="results/trimmed/{sample}_2.trim.fastq.gz",
            params:
                wd=lambda wc: f"results/trimmed/_tm_{wc.sample}",
                length=_MINLEN, sw=_TM_SW, leading=_TM_LEAD, trailing=_TM_TRAIL,
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/trimmomatic_{sample}.tsv"
            log: "logs/trimmomatic_{sample}.log",
            shell:
                _TM_GUARD +
                "rm -rf {params.wd} && mkdir -p {params.wd} results/qc/trimmomatic && " +
                (_ADAP_FIND % "TruSeq3-PE.fa") +
                "trimmomatic PE -threads {threads} {input.r1} {input.r2} "
                "{output.r1} {params.wd}/u1.fq.gz {output.r2} {params.wd}/u2.fq.gz "
                "ILLUMINACLIP:$ADAP:2:30:10 LEADING:{params.leading} TRAILING:{params.trailing} "
                "SLIDINGWINDOW:{params.sw} MINLEN:{params.length} "
                "> results/qc/trimmomatic/{wildcards.sample}.log 2>&1; "
                "cp results/qc/trimmomatic/{wildcards.sample}.log {log}; "
                "rm -rf {params.wd}"

else:

    if SINGLE_END:

        rule fastp_se:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
            output:
                r1="results/trimmed/{sample}.trim.fastq.gz",
                json="results/qc/fastp/{sample}.json",
                html="results/qc/fastp/{sample}.html",
            params:
                q=_Q, u=_U, length=_MINLEN, polyg=_POLYG, polyx=_POLYX,
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/fastp_{sample}.tsv"
            log: "logs/fastp_{sample}.log",
            shell:
                "fastp -i {input.r1} -o {output.r1} "
                "-q {params.q} -u {params.u} -l {params.length} {params.polyg} {params.polyx} "
                "-j {output.json} -h {output.html} "
                "--thread $(( {threads} < 16 ? {threads} : 16 )) > {log} 2>&1"

    else:

        rule fastp_pe:
            input:
                r1=lambda wc: raw_fastq(wc.sample, 1),
                r2=lambda wc: raw_fastq(wc.sample, 2),
            output:
                r1="results/trimmed/{sample}_1.trim.fastq.gz",
                r2="results/trimmed/{sample}_2.trim.fastq.gz",
                json="results/qc/fastp/{sample}.json",
                html="results/qc/fastp/{sample}.html",
            params:
                q=_Q, u=_U, length=_MINLEN, polyg=_POLYG, polyx=_POLYX,
            threads: rule_threads("fastp", 4)
            benchmark: "benchmarks/fastp_{sample}.tsv"
            log: "logs/fastp_{sample}.log",
            shell:
                "fastp -i {input.r1} -I {input.r2} -o {output.r1} -O {output.r2} "
                "--detect_adapter_for_pe -q {params.q} -u {params.u} -l {params.length} {params.polyg} {params.polyx} "
                "-j {output.json} -h {output.html} "
                "--thread $(( {threads} < 16 ? {threads} : 16 )) > {log} 2>&1"
