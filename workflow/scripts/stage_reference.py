from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path
from typing import BinaryIO, Callable


_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SEQUENCE_RE = re.compile(r"^[A-Za-z.*?\-]+$")
_CHUNK_SIZE = 1024 * 1024


class ReferenceStagingError(ValueError):
    """A reference source could not be safely promoted to its final path."""


def _temporary_path(parent: Path, stem: str, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        dir=parent,
        prefix=f".{stem}.",
        suffix=suffix,
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _copy_and_digest(source: BinaryIO, destination: Path) -> tuple[int, str, str]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    byte_size = 0
    with destination.open("wb") as output:
        while True:
            chunk = source.read(_CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            md5.update(chunk)
            sha256.update(chunk)
            byte_size += len(chunk)
        output.flush()
        os.fsync(output.fileno())
    return byte_size, md5.hexdigest(), sha256.hexdigest()


def sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _contig_digest(contigs: set[str]) -> str:
    canonical = "\n".join(sorted(contigs)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def inspect_fasta(path: Path) -> tuple[dict[str, object], set[str]]:
    """Parse a FASTA and return bounded metrics plus its exact contig IDs."""
    records = 0
    total_bases = 0
    current_id: str | None = None
    current_bases = 0
    contigs: set[str] = set()
    try:
        handle = path.open("r", encoding="ascii", errors="strict", newline=None)
        with handle:
            for line_number, raw in enumerate(handle, start=1):
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith(">"):
                    if current_id is not None and current_bases == 0:
                        raise ReferenceStagingError(
                            f"FASTA record '{current_id}' has no sequence (line {line_number})."
                        )
                    header = line[1:].strip()
                    if not header:
                        raise ReferenceStagingError(
                            f"FASTA header is empty at line {line_number}."
                        )
                    current_id = header.split()[0]
                    if current_id in contigs:
                        raise ReferenceStagingError(
                            f"FASTA contig ID '{current_id}' is duplicated."
                        )
                    contigs.add(current_id)
                    records += 1
                    current_bases = 0
                    continue
                if current_id is None:
                    raise ReferenceStagingError(
                        f"FASTA sequence appears before the first header at line {line_number}."
                    )
                if not _SEQUENCE_RE.fullmatch(line):
                    raise ReferenceStagingError(
                        f"FASTA sequence contains invalid characters at line {line_number}."
                    )
                current_bases += len(line)
                total_bases += len(line)
    except UnicodeDecodeError as exc:
        raise ReferenceStagingError("FASTA is not valid ASCII text.") from exc
    if records == 0:
        raise ReferenceStagingError("FASTA contains no records.")
    if current_bases == 0:
        raise ReferenceStagingError(f"FASTA record '{current_id}' has no sequence.")
    metrics: dict[str, object] = {
        "record_count": records,
        "total_bases": total_bases,
        "contig_count": len(contigs),
        "contig_set_sha256": _contig_digest(contigs),
        "contig_examples": sorted(contigs)[:10],
    }
    return metrics, contigs


def _split_attribute_fields(value: str, line_number: int) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\" and quote is not None:
            current.append(character)
            escaped = True
            continue
        if character in {'"', "'"}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            current.append(character)
            continue
        if character == ";" and quote is None:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if quote is not None:
        raise ReferenceStagingError(
            f"Annotation line {line_number} has an unterminated quoted attribute."
        )
    fields.append("".join(current).strip())
    return [field for field in fields if field]


def _parse_attribute_keys(value: str, line_number: int) -> set[str]:
    """Return keys whose GTF/GFF3 values are syntactically valid and non-empty."""
    if not value.strip() or value.strip() == ".":
        raise ReferenceStagingError(
            f"Annotation line {line_number} has no parseable attributes in column 9."
        )
    nonempty_keys: set[str] = set()
    parsed_fields = 0
    for field in _split_attribute_fields(value, line_number):
        if "=" in field and not field.split("=", 1)[0].strip().count(" "):
            key, raw_value = field.split("=", 1)
            key = key.strip()
            raw_value = raw_value.strip()
        else:
            match = re.fullmatch(r"([^\s=;]+)\s+(.+)", field)
            if match is None:
                raise ReferenceStagingError(
                    f"Annotation line {line_number} has an unparseable attribute field: {field!r}."
                )
            key, raw_value = match.group(1), match.group(2).strip()
        if not key or any(character.isspace() for character in key):
            raise ReferenceStagingError(
                f"Annotation line {line_number} has an invalid attribute key."
            )
        if raw_value.startswith(('"', "'")):
            quote = raw_value[0]
            if len(raw_value) < 2 or not raw_value.endswith(quote):
                raise ReferenceStagingError(
                    f"Annotation line {line_number} has an unterminated attribute value for {key}."
                )
            raw_value = raw_value[1:-1].strip()
        elif raw_value.endswith(('"', "'")):
            raise ReferenceStagingError(
                f"Annotation line {line_number} has a malformed attribute value for {key}."
            )
        parsed_fields += 1
        if raw_value and raw_value != ".":
            nonempty_keys.add(key)
    if parsed_fields == 0 or not nonempty_keys:
        raise ReferenceStagingError(
            f"Annotation line {line_number} has no non-empty parseable attributes in column 9."
        )
    return nonempty_keys


def inspect_annotation(
    path: Path,
    *,
    required_feature_types: set[str] | None = None,
    required_attribute: str | None = None,
) -> tuple[dict[str, object], set[str], dict[str, int]]:
    """Parse GTF/GFF3 rows and measure the exact featureCounts-facing contract."""
    feature_count = 0
    evidence = {"gene": 0, "exon": 0, "CDS": 0}
    contigs: set[str] = set()
    feature_type_counts: Counter[str] = Counter()
    attribute_key_counts: Counter[str] = Counter()
    feature_rows_by_contig: Counter[str] = Counter()
    required_types = required_feature_types or set()
    configured_rows = 0
    configured_rows_with_attribute = 0
    missing_attribute_line_examples: list[int] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="strict", newline=None)
        with handle:
            for line_number, raw in enumerate(handle, start=1):
                line = raw.rstrip("\r\n")
                if not line or line.startswith("#"):
                    continue
                fields = line.split("\t")
                if len(fields) != 9:
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has {len(fields)} columns; expected 9."
                    )
                seqid, _source, feature, start, end, _score, strand, frame, attributes = fields
                if not seqid or seqid == ".":
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has no contig identifier."
                    )
                if not feature or feature == ".":
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has no feature type."
                    )
                try:
                    start_value = int(start)
                    end_value = int(end)
                except ValueError as exc:
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has non-integer coordinates."
                    ) from exc
                if start_value < 1 or end_value < start_value:
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has invalid coordinates {start}-{end}."
                    )
                if strand not in {"+", "-", ".", "?"}:
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has invalid strand '{strand}'."
                    )
                if frame not in {"0", "1", "2", "."}:
                    raise ReferenceStagingError(
                        f"Annotation line {line_number} has invalid frame '{frame}'."
                    )
                attribute_keys = _parse_attribute_keys(attributes, line_number)
                feature_count += 1
                contigs.add(seqid)
                feature_type_counts[feature] += 1
                feature_rows_by_contig[seqid] += 1
                attribute_key_counts.update(attribute_keys)
                if feature in required_types:
                    configured_rows += 1
                    if required_attribute and required_attribute in attribute_keys:
                        configured_rows_with_attribute += 1
                    elif len(missing_attribute_line_examples) < 10:
                        missing_attribute_line_examples.append(line_number)
                folded = feature.casefold()
                if folded == "gene":
                    evidence["gene"] += 1
                elif folded == "exon":
                    evidence["exon"] += 1
                elif folded == "cds":
                    evidence["CDS"] += 1
    except UnicodeDecodeError as exc:
        raise ReferenceStagingError("Annotation is not valid UTF-8 text.") from exc
    if feature_count == 0:
        raise ReferenceStagingError("Annotation contains no parseable feature rows.")
    metrics: dict[str, object] = {
        "feature_count": feature_count,
        "evidence_counts": evidence,
        "feature_type_counts": dict(sorted(feature_type_counts.items())),
        "attribute_key_row_counts": dict(sorted(attribute_key_counts.items())),
        "contig_count": len(contigs),
        "contig_set_sha256": _contig_digest(contigs),
        "contig_examples": sorted(contigs)[:10],
    }
    if required_types:
        metrics["counting_contract"] = {
            "feature_types": sorted(required_types),
            "attribute_type": required_attribute,
            "feature_rows": configured_rows,
            "feature_rows_with_attribute": configured_rows_with_attribute,
            "feature_rows_missing_attribute": configured_rows - configured_rows_with_attribute,
            "missing_attribute_line_examples": missing_attribute_line_examples,
        }
    return metrics, contigs, dict(feature_rows_by_contig)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path.parent, path.name, ".json.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        _safe_unlink(temporary)


def _default_gff3_converter(source: Path, destination: Path) -> None:
    completed = subprocess.run(
        ["gffread", str(source), "-T", "-o", str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise ReferenceStagingError(f"gffread GFF3-to-GTF conversion failed: {detail}")


def _decompress_or_copy(source: Path, destination: Path, compressed: bool) -> tuple[str, int]:
    try:
        reader_context = gzip.open(source, "rb") if compressed else source.open("rb")
        with reader_context as reader:
            byte_size, _md5, sha256 = _copy_and_digest(reader, destination)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise ReferenceStagingError(f"Reference source could not be decompressed completely: {exc}") from exc
    if byte_size == 0:
        raise ReferenceStagingError("Reference source is empty after decompression.")
    return sha256, byte_size


def stage_reference(
    *,
    destination: Path,
    sidecar: Path,
    kind: str,
    source: Path | None = None,
    url: str | None = None,
    expected_md5: str | None = None,
    input_format: str | None = None,
    converter: Callable[[Path, Path], None] | None = None,
) -> dict[str, object]:
    """Stage one reference artifact and atomically publish it after integrity checks."""
    source_value = str(source).strip() if source is not None else ""
    url_value = str(url or "").strip()
    if bool(source_value) == bool(url_value):
        raise ReferenceStagingError("Exactly one of source or URL must be configured.")
    if kind not in {"fasta", "annotation"}:
        raise ReferenceStagingError("Reference kind must be 'fasta' or 'annotation'.")
    normalized_format = (input_format or ("fasta" if kind == "fasta" else "gtf")).casefold()
    if kind == "fasta" and normalized_format != "fasta":
        raise ReferenceStagingError("A FASTA artifact must use input format 'fasta'.")
    if kind == "annotation" and normalized_format not in {"gtf", "gff3"}:
        raise ReferenceStagingError("Annotation input format must be 'gtf' or 'gff3'.")

    configured_md5 = str(expected_md5 or "").strip().lower()
    if configured_md5 and not _MD5_RE.fullmatch(configured_md5):
        raise ReferenceStagingError("Configured source MD5 must contain exactly 32 hexadecimal characters.")

    destination = Path(destination)
    sidecar = Path(sidecar)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    source_temporary = _temporary_path(destination.parent, destination.name, ".source.tmp")
    uncompressed_temporary = _temporary_path(destination.parent, destination.name, ".uncompressed.tmp")
    canonical_temporary = _temporary_path(destination.parent, destination.name, ".canonical.tmp")
    sidecar_temporary: Path | None = None

    try:
        if source_value:
            source_path = Path(source_value)
            if not source_path.is_file():
                raise ReferenceStagingError(f"Reference source file does not exist: {source_path}")
            with source_path.open("rb") as reader:
                source_bytes, source_md5, source_sha256 = _copy_and_digest(reader, source_temporary)
            source_kind = "custom_file"
            source_identifier = str(source_path)
        else:
            try:
                with urllib.request.urlopen(url_value) as reader:
                    source_bytes, source_md5, source_sha256 = _copy_and_digest(reader, source_temporary)
            except Exception as exc:
                raise ReferenceStagingError(f"Reference download failed for {url_value}: {exc}") from exc
            source_kind = "url"
            source_identifier = url_value

        if source_bytes == 0:
            raise ReferenceStagingError("Reference source contains zero bytes.")
        if configured_md5 and source_md5 != configured_md5:
            raise ReferenceStagingError(
                f"Reference source MD5 mismatch: expected {configured_md5}, observed {source_md5}."
            )

        with source_temporary.open("rb") as handle:
            compressed = handle.read(2) == b"\x1f\x8b"
        source_uncompressed_sha256, source_uncompressed_bytes = _decompress_or_copy(
            source_temporary, uncompressed_temporary, compressed
        )

        if kind == "fasta":
            shutil.copyfile(uncompressed_temporary, canonical_temporary)
            content_metrics, _contigs = inspect_fasta(canonical_temporary)
            canonical_format = "fasta"
            conversion = "none"
        elif normalized_format == "gff3":
            inspect_annotation(uncompressed_temporary)
            (converter or _default_gff3_converter)(uncompressed_temporary, canonical_temporary)
            content_metrics, _contigs, _feature_rows = inspect_annotation(canonical_temporary)
            canonical_format = "gtf"
            conversion = "gffread -T"
        else:
            shutil.copyfile(uncompressed_temporary, canonical_temporary)
            content_metrics, _contigs, _feature_rows = inspect_annotation(canonical_temporary)
            canonical_format = "gtf"
            conversion = "none"

        canonical_sha256, canonical_bytes = sha256_and_size(canonical_temporary)
        if canonical_bytes == 0:
            raise ReferenceStagingError("Canonical reference artifact contains zero bytes.")
        payload: dict[str, object] = {
            "schema_version": 1,
            "artifact": kind,
            "source_kind": source_kind,
            "source_identifier": source_identifier,
            "source_compression": "gzip" if compressed else "none",
            "source_bytes": source_bytes,
            "source_md5": source_md5,
            "source_sha256": source_sha256,
            "configured_md5": configured_md5 or None,
            "md5_status": "VERIFIED" if configured_md5 else "NOT_CONFIGURED",
            "source_uncompressed_sha256": source_uncompressed_sha256,
            "source_uncompressed_bytes": source_uncompressed_bytes,
            "source_format": normalized_format,
            "canonical_path": str(destination),
            "canonical_format": canonical_format,
            "canonical_sha256": canonical_sha256,
            "canonical_bytes": canonical_bytes,
            "conversion": conversion,
            "content": content_metrics,
        }

        sidecar_temporary = _temporary_path(sidecar.parent, sidecar.name, ".json.tmp")
        with sidecar_temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        # Publish each complete file with an atomic rename. The evidence is placed first so a
        # crash cannot leave a new final artifact that appears to be verified without a sidecar.
        os.replace(sidecar_temporary, sidecar)
        sidecar_temporary = None
        os.replace(canonical_temporary, destination)
        return payload
    finally:
        _safe_unlink(source_temporary)
        _safe_unlink(uncompressed_temporary)
        _safe_unlink(canonical_temporary)
        _safe_unlink(sidecar_temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically stage and checksum a reference artifact.")
    # nargs='?' lets Snakemake pass an explicitly empty parameter without manufacturing a
    # shell-specific quote token; the following option then remains an option, not its value.
    parser.add_argument("--source", nargs="?", const="", default="")
    parser.add_argument("--url", nargs="?", const="", default="")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--kind", choices=("fasta", "annotation"), required=True)
    parser.add_argument("--format", dest="input_format", default=None)
    parser.add_argument("--expected-md5", nargs="?", const="", default="")
    args = parser.parse_args()
    try:
        stage_reference(
            source=Path(args.source) if args.source else None,
            url=args.url or None,
            destination=Path(args.destination),
            sidecar=Path(args.sidecar),
            kind=args.kind,
            expected_md5=args.expected_md5 or None,
            input_format=args.input_format,
        )
    except ReferenceStagingError as exc:
        parser.exit(1, f"Reference staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
