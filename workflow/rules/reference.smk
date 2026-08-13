# Reference: stage provider/custom bytes atomically, verify a configured source MD5 before
# decompression, persist canonical hashes, validate the realized FASTA/GTF pair, and only then
# release downstream indexing (protocol section 6.8).


GENOME_INTEGRITY = GENOME_FA + ".integrity.json"
ANNOTATION_INTEGRITY = ANNOTATION_GTF + ".integrity.json"
REFERENCE_LOCK = "references/reference.lock.json"
REFERENCE_GATE = "checks/05_reference_validation.passed"

_CUSTOM_REFERENCE = str(REF.get("mode") or "").casefold() == "custom"


rule download_genome:
    output:
        fa=GENOME_FA,
        integrity=GENOME_INTEGRITY,
    params:
        url="" if _CUSTOM_REFERENCE else (REF.get("genome_fasta_url") or ""),
        src=(REF.get("genome_fasta") or "") if _CUSTOM_REFERENCE else "",
        md5=REF.get("genome_md5") or "",
    log:
        "logs/download_genome.log",
    shell:
        "python workflow/scripts/stage_reference.py "
        "--source {params.src:q} --url {params.url:q} "
        "--destination {output.fa:q} --sidecar {output.integrity:q} "
        "--kind fasta --format fasta --expected-md5 {params.md5:q} > {log:q} 2>&1"


rule download_gtf:
    output:
        gtf=ANNOTATION_GTF,
        integrity=ANNOTATION_INTEGRITY,
    params:
        url="" if _CUSTOM_REFERENCE else (REF.get("annotation_gtf_url") or ""),
        src=(REF.get("annotation_file") or "") if _CUSTOM_REFERENCE else "",
        fmt="gff3" if str(REF.get("annotation_format") or "gtf").casefold() == "gff3" else "gtf",
        md5=REF.get("annotation_md5") or "",
    log:
        "logs/download_gtf.log",
    shell:
        "python workflow/scripts/stage_reference.py "
        "--source {params.src:q} --url {params.url:q} "
        "--destination {output.gtf:q} --sidecar {output.integrity:q} "
        "--kind annotation --format {params.fmt:q} --expected-md5 {params.md5:q} "
        "> {log:q} 2>&1"


rule reference_check:
    input:
        fa=GENOME_FA,
        gtf=ANNOTATION_GTF,
        genome_integrity=GENOME_INTEGRITY,
        annotation_integrity=ANNOTATION_INTEGRITY,
        prev="checks/00_project_setup.json",
    output:
        check="checks/05_reference_validation.json",
        lock=REFERENCE_LOCK,
    params:
        feature_type=(config.get("featurecounts") or {}).get("feature_type", "exon"),
        attribute_type=(config.get("featurecounts") or {}).get("attribute_type", "gene_id"),
    benchmark:
        "benchmarks/05_reference_validation.tsv"
    log:
        "logs/reference_validation.log",
    run:
        # validate_reference exits non-zero on a scientific FAIL. Preserve its explicit check and
        # lock evidence as successful outputs here, then let reference_integrity_gate fail the DAG.
        # Unexpected failures that did not produce both evidence files still fail this rule.
        import subprocess

        command = [
            "python", "workflow/scripts/validate_reference.py",
            "--config", "config/config.yaml",
            "--fasta", str(input.fa),
            "--annotation", str(input.gtf),
            "--genome-sidecar", str(input.genome_integrity),
            "--annotation-sidecar", str(input.annotation_integrity),
            "--feature-type", str(params.feature_type),
            "--attribute-type", str(params.attribute_type),
            "--out", str(output.check),
            "--lock", str(output.lock),
        ]
        with open(log[0], "w", encoding="utf-8") as handle:
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
            handle.write(f"validator_exit_code={completed.returncode}\n")
        if not os.path.isfile(output.check) or not os.path.isfile(output.lock):
            raise ValueError(
                f"Reference validator exited {completed.returncode} without complete check/lock evidence."
            )


rule reference_integrity_gate:
    input:
        fa=GENOME_FA,
        gtf=ANNOTATION_GTF,
        genome_integrity=GENOME_INTEGRITY,
        annotation_integrity=ANNOTATION_INTEGRITY,
        check="checks/05_reference_validation.json",
        lock=REFERENCE_LOCK,
    output:
        REFERENCE_GATE,
    run:
        import hashlib
        import json

        check_payload = json.loads(open(input.check, encoding="utf-8").read())
        lock_payload = json.loads(open(input.lock, encoding="utf-8").read())
        if check_payload.get("status") != "PASS" or lock_payload.get("status") != "PASS":
            messages = check_payload.get("messages") or []
            detail = "; ".join(str(message.get("message") or "") for message in messages)
            raise ValueError(f"Reference integrity gate failed: {detail}")
        check_evidence = check_payload.get("evidence") or {}
        locked_contract = lock_payload.get("counting_contract") or {}
        checked_contract = check_evidence.get("counting_contract") or {}
        configured_fc = config.get("featurecounts") or {}
        expected_features = sorted(
            value.strip()
            for value in str(configured_fc.get("feature_type", "exon")).split(",")
            if value.strip()
        )
        expected_attribute = str(configured_fc.get("attribute_type", "gene_id")).strip()
        if (
            checked_contract != locked_contract
            or locked_contract.get("feature_types") != expected_features
            or locked_contract.get("attribute_type") != expected_attribute
            or int(locked_contract.get("feature_rows", 0)) <= 0
            or int(locked_contract.get("feature_rows_missing_attribute", 0)) != 0
        ):
            raise ValueError("Reference integrity gate found an invalid configured counting contract.")
        compatibility = lock_payload.get("contig_compatibility") or {}
        if (
            check_evidence.get("contig_compatibility") != compatibility
            or float(compatibility.get("feature_row_overlap_fraction", 0.0)) < 0.95
            or float(compatibility.get("minimum_feature_row_overlap_fraction", 0.0)) != 0.95
        ):
            raise ValueError("Reference integrity gate found insufficient feature-weighted contig compatibility.")
        for artifact, canonical, sidecar_path, evidence_key in (
            ("genome", input.fa, input.genome_integrity, "genome_canonical_sha256"),
            ("annotation", input.gtf, input.annotation_integrity, "annotation_canonical_sha256"),
        ):
            locked = str(
                ((lock_payload.get(artifact) or {}).get("integrity") or {}).get("canonical_sha256")
                or ""
            )
            checked = str(check_evidence.get(evidence_key) or "")
            sidecar = json.loads(open(sidecar_path, encoding="utf-8").read())
            sidecar_sha256 = str(sidecar.get("canonical_sha256") or "")
            digest = hashlib.sha256()
            with open(canonical, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            observed = digest.hexdigest()
            if (
                len(locked) != 64
                or checked != locked
                or sidecar_sha256 != locked
                or observed != locked
            ):
                raise ValueError(
                    f"Reference integrity gate found inconsistent {artifact} canonical hashes."
                )
        temporary = str(output[0]) + ".tmp"
        os.makedirs(os.path.dirname(str(output[0])), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write("PASS\n")
        os.replace(temporary, output[0])


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
        check=REFERENCE_GATE,
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
        check=REFERENCE_GATE,
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
        check=REFERENCE_GATE,
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
