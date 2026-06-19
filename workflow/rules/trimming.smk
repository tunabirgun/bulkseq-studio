# Adapter and quality trimming with fastp (protocol section 6.4).
# Paired-end is the implemented route; single-end is a TODO (protocol 6.16).

_FASTP = config.get("fastp", {})


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
        q=_FASTP.get("qualified_quality_phred", 15),
        u=_FASTP.get("unqualified_percent_limit", 40),
        length=_FASTP.get("length_required", 36),
        polyg="-g" if _FASTP.get("trim_poly_g", False) else "",
    threads:
        rule_threads("fastp", 4)
    benchmark:
        "benchmarks/fastp_{sample}.tsv"
    log:
        "logs/fastp_{sample}.log",
    shell:
        "fastp -i {input.r1} -I {input.r2} -o {output.r1} -O {output.r2} "
        "--detect_adapter_for_pe -q {params.q} -u {params.u} -l {params.length} {params.polyg} "
        "-j {output.json} -h {output.html} "
        "--thread $(( {threads} < 16 ? {threads} : 16 )) > {log} 2>&1"
