from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from workflow.scripts import stage_reference as staging


_SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "stage_reference.py"


def _fasta_bytes() -> bytes:
    return b">chr1 synthetic\nACGTNACGT\n>chr2\nTTAA\n"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_custom_gzip_is_verified_before_decompression_and_locked_exactly(tmp_path: Path) -> None:
    canonical = _fasta_bytes()
    provider_bytes = gzip.compress(canonical, mtime=0)
    source = tmp_path / "provider.fa.gz"
    destination = tmp_path / "references" / "genome.fa"
    sidecar = tmp_path / "references" / "genome.fa.integrity.json"
    source.write_bytes(provider_bytes)

    payload = staging.stage_reference(
        source=source,
        destination=destination,
        sidecar=sidecar,
        kind="fasta",
        input_format="fasta",
        expected_md5=_md5(provider_bytes),
    )

    assert destination.read_bytes() == canonical
    assert payload["source_md5"] == _md5(provider_bytes)
    assert payload["md5_status"] == "VERIFIED"
    assert payload["source_bytes"] == len(provider_bytes)
    assert payload["source_uncompressed_sha256"] == _sha256(canonical)
    assert payload["source_uncompressed_bytes"] == len(canonical)
    assert payload["canonical_sha256"] == _sha256(canonical)
    assert payload["canonical_bytes"] == len(canonical)
    assert json.loads(sidecar.read_text(encoding="utf-8")) == payload


def test_missing_provider_md5_is_explicitly_not_configured(tmp_path: Path) -> None:
    source = tmp_path / "genome.fa"
    destination = tmp_path / "out" / "genome.fa"
    sidecar = tmp_path / "out" / "genome.fa.integrity.json"
    source.write_bytes(_fasta_bytes())

    payload = staging.stage_reference(
        source=source,
        destination=destination,
        sidecar=sidecar,
        kind="fasta",
    )

    assert payload["md5_status"] == "NOT_CONFIGURED"
    assert payload["configured_md5"] is None
    assert payload["source_md5"] == _md5(_fasta_bytes())


def test_cli_accepts_snakemake_empty_optional_values(tmp_path: Path) -> None:
    source = tmp_path / "genome.fa"
    destination = tmp_path / "references" / "genome.fa"
    sidecar = tmp_path / "references" / "genome.fa.integrity.json"
    source.write_bytes(_fasta_bytes())

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--source",
            str(source),
            "--url",
            "--destination",
            str(destination),
            "--sidecar",
            str(sidecar),
            "--kind",
            "fasta",
            "--format",
            "fasta",
            "--expected-md5",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert destination.read_bytes() == _fasta_bytes()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["md5_status"] == "NOT_CONFIGURED"


def test_wrong_provider_checksum_exits_nonzero_and_leaves_no_final(tmp_path: Path) -> None:
    source = tmp_path / "genome.fa.gz"
    destination = tmp_path / "references" / "genome.fa"
    sidecar = tmp_path / "references" / "genome.fa.integrity.json"
    source.write_bytes(gzip.compress(_fasta_bytes(), mtime=0))

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--sidecar",
            str(sidecar),
            "--kind",
            "fasta",
            "--format",
            "fasta",
            "--expected-md5",
            "0" * 32,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "MD5 mismatch" in completed.stderr
    assert not destination.exists()
    assert not sidecar.exists()


@pytest.mark.parametrize(
    ("provider_bytes", "message"),
    [
        (b"", "zero bytes"),
        (b"\x1f\x8b\x08\x00truncated", "decompressed completely"),
        (b"ACGT before a header\n", "before the first header"),
    ],
)
def test_empty_truncated_and_malformed_fasta_leave_no_final(
    tmp_path: Path, provider_bytes: bytes, message: str
) -> None:
    source = tmp_path / "bad-reference"
    destination = tmp_path / "references" / "genome.fa"
    sidecar = tmp_path / "references" / "genome.fa.integrity.json"
    source.write_bytes(provider_bytes)

    with pytest.raises(staging.ReferenceStagingError, match=message):
        staging.stage_reference(
            source=source,
            destination=destination,
            sidecar=sidecar,
            kind="fasta",
        )

    assert not destination.exists()
    assert not sidecar.exists()


def test_url_bytes_follow_the_same_checksum_and_canonical_hash_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical = _fasta_bytes()
    provider_bytes = gzip.compress(canonical, mtime=0)
    destination = tmp_path / "references" / "genome.fa"
    sidecar = tmp_path / "references" / "genome.fa.integrity.json"

    monkeypatch.setattr(
        staging.urllib.request,
        "urlopen",
        lambda _url: io.BytesIO(provider_bytes),
    )
    payload = staging.stage_reference(
        url="https://reference.invalid/genome.fa.gz",
        destination=destination,
        sidecar=sidecar,
        kind="fasta",
        expected_md5=_md5(provider_bytes),
    )

    assert payload["source_kind"] == "url"
    assert payload["source_md5"] == _md5(provider_bytes)
    assert payload["canonical_sha256"] == _sha256(canonical)
    assert destination.read_bytes() == canonical


def test_gff3_sidecar_hashes_the_final_converted_gtf(tmp_path: Path) -> None:
    gff3 = b"chr1\tsource\tgene\t1\t9\t.\t+\t.\tID=g1\n"
    provider_bytes = gzip.compress(gff3, mtime=0)
    final_gtf = (
        b'chr1\tsource\tgene\t1\t9\t.\t+\t.\tgene_id "g1";\n'
        b'chr1\tsource\texon\t1\t9\t.\t+\t.\tgene_id "g1"; transcript_id "t1";\n'
    )
    source = tmp_path / "annotation.gff3.gz"
    destination = tmp_path / "references" / "annotation.gtf"
    sidecar = tmp_path / "references" / "annotation.gtf.integrity.json"
    source.write_bytes(provider_bytes)

    def fake_converter(_source: Path, output: Path) -> None:
        output.write_bytes(final_gtf)

    payload = staging.stage_reference(
        source=source,
        destination=destination,
        sidecar=sidecar,
        kind="annotation",
        input_format="gff3",
        expected_md5=_md5(provider_bytes),
        converter=fake_converter,
    )

    assert payload["source_uncompressed_sha256"] == _sha256(gff3)
    assert payload["canonical_format"] == "gtf"
    assert payload["canonical_sha256"] == _sha256(final_gtf)
    assert payload["canonical_bytes"] == len(final_gtf)
    assert destination.read_bytes() == final_gtf


def test_malformed_annotation_never_reaches_final_path(tmp_path: Path) -> None:
    source = tmp_path / "annotation.gtf"
    destination = tmp_path / "references" / "annotation.gtf"
    sidecar = tmp_path / "references" / "annotation.gtf.integrity.json"
    source.write_text("chr1\tsource\tgene\t1\t4\t.\t+\t.\n", encoding="utf-8")

    with pytest.raises(staging.ReferenceStagingError, match="8 columns"):
        staging.stage_reference(
            source=source,
            destination=destination,
            sidecar=sidecar,
            kind="annotation",
            input_format="gtf",
        )

    assert not destination.exists()
    assert not sidecar.exists()


def test_annotation_with_empty_ninth_column_evidence_never_reaches_final_path(tmp_path: Path) -> None:
    source = tmp_path / "annotation.gtf"
    destination = tmp_path / "references" / "annotation.gtf"
    sidecar = tmp_path / "references" / "annotation.gtf.integrity.json"
    source.write_text("chr1\tsource\texon\t1\t4\t.\t+\t.\t.\n", encoding="utf-8")

    with pytest.raises(staging.ReferenceStagingError, match="no parseable attributes"):
        staging.stage_reference(
            source=source,
            destination=destination,
            sidecar=sidecar,
            kind="annotation",
            input_format="gtf",
        )

    assert not destination.exists()
    assert not sidecar.exists()
