from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import yaml

from app.core.reference_manager import load_reference_catalog, validate_reference
from workflow.scripts import stage_reference as staging
from workflow.scripts import validate_reference as workflow_validation


_VALIDATOR = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "validate_reference.py"


def _stage_pair(
    tmp_path: Path,
    *,
    genome_contig: str = "chr1",
    annotation_contig: str = "chr1",
    feature: str = "exon",
    fasta_contigs: list[str] | None = None,
    annotation_rows: list[str] | None = None,
) -> dict[str, Path | str]:
    source_dir = tmp_path / "sources"
    reference_dir = tmp_path / "references"
    source_dir.mkdir(parents=True)
    genome_source = source_dir / "genome.fa"
    annotation_source = source_dir / "annotation.gtf"
    genome_bytes = "".join(
        f">{contig}\nACGTACGTACGT\n" for contig in (fasta_contigs or [genome_contig])
    ).encode()
    rows = annotation_rows or [
        f'{annotation_contig}\tsource\t{feature}\t1\t9\t.\t+\t.\tgene_id "g1";'
    ]
    annotation_bytes = ("\n".join(rows) + "\n").encode()
    genome_source.write_bytes(genome_bytes)
    annotation_source.write_bytes(annotation_bytes)
    genome = reference_dir / "genome.fa"
    annotation = reference_dir / "annotation.gtf"
    genome_sidecar = reference_dir / "genome.fa.integrity.json"
    annotation_sidecar = reference_dir / "annotation.gtf.integrity.json"
    genome_md5 = hashlib.md5(genome_bytes).hexdigest()
    annotation_md5 = hashlib.md5(annotation_bytes).hexdigest()
    staging.stage_reference(
        source=genome_source,
        destination=genome,
        sidecar=genome_sidecar,
        kind="fasta",
        expected_md5=genome_md5,
    )
    staging.stage_reference(
        source=annotation_source,
        destination=annotation,
        sidecar=annotation_sidecar,
        kind="annotation",
        input_format="gtf",
        expected_md5=annotation_md5,
    )
    return {
        "genome": genome,
        "annotation": annotation,
        "genome_sidecar": genome_sidecar,
        "annotation_sidecar": annotation_sidecar,
        "genome_md5": genome_md5,
        "annotation_md5": annotation_md5,
    }


def _config(pair: dict[str, Path | str]) -> dict:
    return {
        "reference": {
            "organism_name": "Synthetic organism",
            "source": "test fixture",
            "release": "1",
            "annotation_format": "gtf",
            "genome_md5": pair["genome_md5"],
            "annotation_md5": pair["annotation_md5"],
        },
        "featurecounts": {"feature_type": "exon", "attribute_type": "gene_id"},
    }


def test_reference_validation_passes_minimal_gff3() -> None:
    base = Path("manual_test_reference") / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    fasta = base / "genome.fa"
    gff = base / "annotation.gff3"
    fasta.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    gff.write_text("chr1\t.\tgene\t1\t4\t.\t+\t.\tID=gene1;Name=Gene1\nchr1\t.\tCDS\t1\t4\t.\t+\t0\tParent=gene1\n", encoding="utf-8")
    messages = validate_reference(fasta, gff)
    assert not any(m["status"] == "FAIL" for m in messages)


def test_reference_catalog_has_no_placeholder_urls() -> None:
    catalog = load_reference_catalog()
    assert len(catalog) >= 20
    for entry in catalog:
        for field in ("genome_fasta_url", "annotation_gtf_url"):
            value = entry.get(field)
            # URLs are either a real https download or explicitly null (a
            # documented no-RefSeq-GTF case); never a TODO/placeholder.
            assert value is None or (isinstance(value, str) and value.startswith("https://")), (
                f"{entry['organism_name']} {field}={value!r}"
            )
            if isinstance(value, str):
                assert "TODO" not in value and "placeholder" not in value


def test_reference_catalog_populated_entries_have_accession() -> None:
    for entry in load_reference_catalog():
        if entry.get("genome_fasta_url"):
            acc = str(entry.get("assembly_accession", ""))
            assert acc and "placeholder" not in acc, entry["organism_name"]


def test_workflow_reference_gate_locks_exact_realized_hashes(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path)
    lock_path = tmp_path / "references" / "reference.lock.json"

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=lock_path,
    )

    genome_bytes = Path(pair["genome"]).read_bytes()
    annotation_bytes = Path(pair["annotation"]).read_bytes()
    assert check["status"] == "PASS"
    assert lock["status"] == "PASS"
    assert lock["genome"]["integrity"]["canonical_sha256"] == hashlib.sha256(genome_bytes).hexdigest()
    assert lock["annotation"]["integrity"]["canonical_sha256"] == hashlib.sha256(annotation_bytes).hexdigest()
    assert lock["genome"]["integrity"]["md5_status"] == "VERIFIED"
    assert lock["annotation"]["integrity"]["md5_status"] == "VERIFIED"
    assert lock["contig_compatibility"]["overlap_contigs"] == 1
    assert lock["annotation"]["content"]["evidence_counts"]["exon"] == 1
    assert lock["counting_contract"]["feature_rows"] == 1
    assert lock["counting_contract"]["feature_rows_missing_attribute"] == 0


def test_disjoint_contigs_exit_nonzero_but_preserve_check_and_lock_evidence(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path, genome_contig="chrGenome", annotation_contig="chrAnnotation")
    config_path = tmp_path / "config.yaml"
    check_path = tmp_path / "checks" / "05_reference_validation.json"
    lock_path = tmp_path / "references" / "reference.lock.json"
    config_path.write_text(yaml.safe_dump(_config(pair)), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(_VALIDATOR),
            "--config",
            str(config_path),
            "--fasta",
            str(pair["genome"]),
            "--annotation",
            str(pair["annotation"]),
            "--genome-sidecar",
            str(pair["genome_sidecar"]),
            "--annotation-sidecar",
            str(pair["annotation_sidecar"]),
            "--feature-type",
            "exon",
            "--attribute-type",
            "gene_id",
            "--out",
            str(check_path),
            "--lock",
            str(lock_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    check = json.loads(check_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert check["status"] == "FAIL"
    assert lock["status"] == "FAIL"
    assert lock["contig_compatibility"]["overlap_contigs"] == 0
    assert any("disjoint" in message["message"] for message in check["messages"])


def test_annotation_without_gene_exon_or_cds_evidence_fails(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path, feature="transcript")

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    assert check["status"] == "FAIL"
    assert lock["status"] == "FAIL"
    assert any("no gene, exon, or CDS" in message["message"] for message in check["messages"])


def test_gene_only_annotation_fails_when_featurecounts_is_configured_for_exons(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path, feature="gene")

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    assert check["status"] == "FAIL"
    assert lock["counting_contract"]["feature_types"] == ["exon"]
    assert lock["counting_contract"]["feature_rows"] == 0
    assert any("contains no rows matching" in message["message"] for message in check["messages"])


def test_every_counted_feature_row_requires_nonempty_configured_attribute(tmp_path: Path) -> None:
    pair = _stage_pair(
        tmp_path,
        annotation_rows=[
            'chr1\tsource\texon\t1\t9\t.\t+\t.\ttranscript_id "t1";',
            'chr1\tsource\texon\t2\t8\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
        ],
    )

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    assert check["status"] == "FAIL"
    contract = lock["counting_contract"]
    assert contract["feature_rows"] == 2
    assert contract["feature_rows_with_attribute"] == 1
    assert contract["feature_rows_missing_attribute"] == 1
    assert any("lack a non-empty 'gene_id'" in message["message"] for message in check["messages"])


def test_one_percent_feature_row_contig_overlap_fails(tmp_path: Path) -> None:
    rows = ['chr1\tsource\texon\t1\t9\t.\t+\t.\tgene_id "match";']
    rows.extend(
        f'chrMissing\tsource\texon\t1\t9\t.\t+\t.\tgene_id "missing_{index}";'
        for index in range(99)
    )
    pair = _stage_pair(tmp_path, annotation_rows=rows)

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    compatibility = lock["contig_compatibility"]
    assert check["status"] == "FAIL"
    assert compatibility["compatible_feature_rows"] == 1
    assert compatibility["annotation_feature_rows"] == 100
    assert compatibility["feature_row_overlap_fraction"] == 0.01
    assert compatibility["annotation_only_feature_rows"] == 99
    assert compatibility["annotation_only_top_contigs"] == [
        {"contig": "chrMissing", "feature_rows": 99}
    ]


def test_small_alt_contig_feature_minority_passes_weighted_gate(tmp_path: Path) -> None:
    rows = [
        f'chr1\tsource\texon\t1\t9\t.\t+\t.\tgene_id "main_{index}";'
        for index in range(96)
    ]
    rows.extend(
        f'chrAlt\tsource\texon\t1\t9\t.\t+\t.\tgene_id "alt_{index}";'
        for index in range(4)
    )
    pair = _stage_pair(tmp_path, annotation_rows=rows)

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    compatibility = lock["contig_compatibility"]
    assert check["status"] == "PASS"
    assert compatibility["feature_row_overlap_fraction"] == 0.96
    assert compatibility["annotation_only_examples"] == ["chrAlt"]
    assert compatibility["minimum_feature_row_overlap_fraction"] == 0.95


def test_post_stage_truncation_fails_hash_and_structure_checks(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path)
    Path(pair["annotation"]).write_text("chr1\tsource\tgene\t1\t9\t.\t+\t.\n", encoding="utf-8")

    check, lock = workflow_validation.build_reference_evidence(
        config=_config(pair),
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    assert check["status"] == "FAIL"
    assert lock["status"] == "FAIL"
    messages = "\n".join(message["message"] for message in check["messages"])
    assert "canonical SHA-256 mismatch" in messages
    assert "8 columns" in messages


def test_not_configured_md5_is_accepted_and_persisted_as_explicit_state(tmp_path: Path) -> None:
    pair = _stage_pair(tmp_path)
    for artifact in ("genome", "annotation"):
        sidecar_path = Path(pair[f"{artifact}_sidecar"])
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        payload["configured_md5"] = None
        payload["md5_status"] = "NOT_CONFIGURED"
        sidecar_path.write_text(json.dumps(payload), encoding="utf-8")
    config = _config(pair)
    config["reference"]["genome_md5"] = None
    config["reference"]["annotation_md5"] = None

    check, lock = workflow_validation.build_reference_evidence(
        config=config,
        fasta=pair["genome"],
        annotation=pair["annotation"],
        genome_sidecar=pair["genome_sidecar"],
        annotation_sidecar=pair["annotation_sidecar"],
        lock_path=tmp_path / "references" / "reference.lock.json",
    )

    assert check["status"] == "PASS"
    assert lock["genome"]["integrity"]["md5_status"] == "NOT_CONFIGURED"
    assert lock["annotation"]["integrity"]["md5_status"] == "NOT_CONFIGURED"
