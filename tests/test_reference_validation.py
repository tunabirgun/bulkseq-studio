from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.reference_manager import load_reference_catalog, validate_reference


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
