from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.reference_manager import validate_reference


def test_reference_validation_passes_minimal_gff3() -> None:
    base = Path("manual_test_reference") / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    fasta = base / "genome.fa"
    gff = base / "annotation.gff3"
    fasta.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    gff.write_text("chr1\t.\tgene\t1\t4\t.\t+\t.\tID=gene1;Name=Gene1\nchr1\t.\tCDS\t1\t4\t.\t+\t0\tParent=gene1\n", encoding="utf-8")
    messages = validate_reference(fasta, gff)
    assert not any(m["status"] == "FAIL" for m in messages)
