# Reference: download genome FASTA + GTF, then build the STAR index with
# genome-size-aware parameters (protocol section 6.8).


def _stage_reference(src, url, dest):
    # mode == "custom": copy the user-supplied file (gzipped or plain) into the
    # project. Otherwise download it from the configured URL. Runs inside WSL, so
    # `src` is the WSL-resolvable path stored by the GUI at lock time.
    import gzip
    import shutil
    import urllib.request

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if REF.get("mode") == "custom":
        if not src:
            raise ValueError("custom reference selected but the source path is not set in config.")
        if not os.path.exists(src):
            raise FileNotFoundError(f"Custom reference file not found inside WSL: {src}")
        if str(src).endswith(".gz"):
            with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copyfile(src, dest)
        return
    if not url:
        raise ValueError("reference URL is not set in config (and mode is not 'custom').")
    tmp = dest + ".gz"
    urllib.request.urlretrieve(url, tmp)
    with gzip.open(tmp, "rb") as f_in, open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(tmp)


rule download_genome:
    output:
        GENOME_FA,
    params:
        url=REF.get("genome_fasta_url", ""),
        src=REF.get("genome_fasta", ""),
    log:
        "logs/download_genome.log",
    run:
        _stage_reference(params.src, params.url, output[0])


rule download_gtf:
    output:
        ANNOTATION_GTF,
    params:
        url=REF.get("annotation_gtf_url", ""),
        src=REF.get("annotation_file", ""),
    log:
        "logs/download_gtf.log",
    run:
        _stage_reference(params.src, params.url, output[0])


rule reference_check:
    input:
        fa=GENOME_FA,
        gtf=ANNOTATION_GTF,
        prev="checks/00_project_setup.json",
    output:
        "checks/05_reference_validation.json",
    benchmark:
        "benchmarks/05_reference_validation.tsv"
    shell:
        "python workflow/scripts/validate_reference.py --config config/config.yaml --out {output}"


rule read_length:
    input:
        lambda wc: raw_fastq(FIRST_SAMPLE, 1),
    output:
        "results/qc/read_length.txt",
    shell:
        # Disable pipefail: head closes the pipe early, giving zcat a SIGPIPE.
        r"set +o pipefail; zcat {input} | head -n 40000 | "
        r"awk 'NR%4==2{{if(length($0)>m)m=length($0)}}END{{print m}}' > {output}"


rule star_index:
    input:
        fa=GENOME_FA,
        gtf=ANNOTATION_GTF,
        rl="results/qc/read_length.txt",
        check="checks/05_reference_validation.json",
    output:
        directory(STAR_INDEX),
    threads:
        rule_threads("star_index", 8)
    resources:
        mem_mb=rule_mem_mb("star_index", 24),
    benchmark:
        "benchmarks/star_index.tsv"
    log:
        "logs/star_index.log",
    shell:
        r"""
        mkdir -p {output}
        GLEN=$(grep -v '^>' {input.fa} | tr -d '\n' | wc -c)
        NBASES=$(python -c "import math,sys; print(min(14, int(math.log2(int(sys.argv[1]))/2 - 1)))" $GLEN)
        RLEN=$(cat {input.rl}); OH=$((RLEN-1))
        echo "genome_length=$GLEN genomeSAindexNbases=$NBASES sjdbOverhang=$OH" > {log}
        STAR --runMode genomeGenerate --genomeDir {output} \
             --genomeFastaFiles {input.fa} --sjdbGTFfile {input.gtf} \
             --sjdbOverhang $OH --genomeSAindexNbases $NBASES \
             --runThreadN {threads} >> {log} 2>&1
        """
