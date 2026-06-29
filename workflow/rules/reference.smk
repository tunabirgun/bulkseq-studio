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
        fmt=str(REF.get("annotation_format", "gtf")).lower(),
    log:
        "logs/download_gtf.log",
    run:
        # GFF3 input is converted to GTF (gffread -T) so every downstream consumer
        # (STAR --sjdbGTFfile, featureCounts -g gene_id, make_transcriptome) gets the GTF
        # it expects. Only the gff3 path runs gffread; gtf / unset stage unchanged, so the
        # GTF path is byte-identical to before.
        if params.fmt == "gff3":
            import subprocess
            tmp = output[0] + ".gff3"
            _stage_reference(params.src, params.url, tmp)
            with open(log[0], "w", encoding="utf-8") as lf:
                subprocess.run(["gffread", tmp, "-T", "-o", output[0]], check=True,
                               stdout=lf, stderr=subprocess.STDOUT)
            os.remove(tmp)
        else:
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


# HISAT2 graph index (much lower RAM than STAR; viable for large crop genomes).
rule hisat2_index:
    input:
        fa=GENOME_FA,
        check="checks/05_reference_validation.json",
    output:
        directory(HISAT2_INDEX_DIR),
    threads:
        rule_threads("hisat2_index", 8)
    resources:
        mem_mb=rule_mem_mb("hisat2_index", 16),
    benchmark:
        "benchmarks/hisat2_index.tsv"
    log:
        "logs/hisat2_index.log",
    shell:
        "export PATH=\"${{MAMBA_ROOT_PREFIX:-$HOME/micromamba}}/envs/bulkseq/bin:${{PATH}}\" && "
        "command -v hisat2-build >/dev/null 2>&1 || {{ echo 'hisat2 is not installed in the bulkseq environment; the HISAT2 aligner route needs it. In the app open Setup and click Install / repair core environment (or update the env from workflow/envs/bulkseq_core.yaml), then re-run.' >&2; exit 1; }}; "
        "mkdir -p {output} && hisat2-build -p {threads} {input.fa:q} {output}/genome > {log} 2>&1"


# Transcriptome FASTA from genome + GTF (for the Salmon route), then the Salmon index.
rule make_transcriptome:
    input:
        fa=GENOME_FA,
        gtf=ANNOTATION_GTF,
        check="checks/05_reference_validation.json",
    output:
        fa=TRANSCRIPTOME_FA,
        tx2gene="references/tx2gene.tsv",
    log:
        "logs/make_transcriptome.log",
    shell:
        # NCBI RefSeq GTFs carry transcript_id "" on `gene` feature lines, which gffread
        # rejects ("no valid ID found for GFF record"). Drop gene lines first -- gffread
        # builds transcripts from the transcript/exon/CDS records; Ensembl GTFs are
        # unaffected. The tx2gene table is emitted by gffread itself (@id,@geneid) so its
        # transcript names match the FASTA/Salmon index exactly -- robust to RefSeq dual
        # XM_/gnl|WGS transcript records that a raw-GTF parse would mismatch.
        # Drop gene lines (empty transcript_id) and unknown-strand "?" records (trans-spliced
        # organelle genes, e.g. chloroplast rps12, which gffread refuses to parse). Then
        # gtf_clean.pl neutralizes semicolons embedded inside quoted attribute values (NCBI
        # gene symbols such as "CYCB1;1" in soybean/tomato/potato, which gffread mis-reads as
        # the attribute separator). gffread then builds transcripts from the cleaned records.
        # gffread can emit non-unique "unassigned_transcript_N" names for unnamed
        # organellar/tRNA records, which salmon's indexer rejects. Build to .raw, then
        # dedup_transcriptome.sh drops duplicate-named records (keeping FASTA + tx2gene
        # in sync); no-op for already-unique transcriptomes (Ensembl, most assemblies).
        "export PATH=\"${{MAMBA_ROOT_PREFIX:-$HOME/micromamba}}/envs/bulkseq/bin:${{PATH}}\" && "
        "command -v gffread >/dev/null 2>&1 || {{ echo 'gffread is not installed in the bulkseq environment; the Salmon aligner route needs it. In the app open Setup and click Install / repair core environment (or update the env from workflow/envs/bulkseq_core.yaml), then re-run.' >&2; exit 1; }}; "
        "awk -F'\\t' '$3 != \"gene\" && $7 != \"?\"' {input.gtf:q} | "
        "perl workflow/scripts/gtf_clean.pl > {output.fa:q}.nogene.gtf && "
        "gffread -w {output.fa:q}.raw -g {input.fa:q} {output.fa:q}.nogene.gtf > {log} 2>&1 && "
        "gffread {output.fa:q}.nogene.gtf --table @id,@geneid > {output.tx2gene:q}.raw 2>> {log} && "
        "bash workflow/scripts/dedup_transcriptome.sh {output.fa:q}.raw {output.tx2gene:q}.raw {output.fa:q} {output.tx2gene:q} && "
        "rm -f {output.fa:q}.nogene.gtf {output.fa:q}.raw {output.tx2gene:q}.raw"


rule salmon_index:
    input:
        txome=TRANSCRIPTOME_FA,
    output:
        directory(SALMON_INDEX),
    threads:
        rule_threads("salmon_index", 8)
    resources:
        mem_mb=rule_mem_mb("salmon_index", 16),
    benchmark:
        "benchmarks/salmon_index.tsv"
    log:
        "logs/salmon_index.log",
    shell:
        # --keepDuplicates: some NCBI RefSeq annotations list identical transcripts twice
        # (RefSeq XM_ + the original WGS model). Without this, salmon collapses the pair and
        # may keep the copy whose name is absent from tx2gene, zeroing those genes; keeping
        # both lets the tx2gene-named copy carry the counts. No-op for clean assemblies.
        "export PATH=\"${{MAMBA_ROOT_PREFIX:-$HOME/micromamba}}/envs/bulkseq/bin:${{PATH}}\" && "
        "command -v salmon >/dev/null 2>&1 || {{ echo 'salmon is not installed in the bulkseq environment; the Salmon aligner route needs it. In the app open Setup and click Install / repair core environment (or update the env from workflow/envs/bulkseq_core.yaml), then re-run.' >&2; exit 1; }}; "
        "salmon index -t {input.txome:q} -i {output:q} -k 31 -p {threads} --keepDuplicates > {log} 2>&1"
