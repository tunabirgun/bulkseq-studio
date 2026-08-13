from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from stage_reference import (  # noqa: E402
    ReferenceStagingError,
    atomic_write_json,
    inspect_annotation,
    inspect_fasta,
    sha256_and_size,
)


_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
_MIN_FEATURE_ROW_CONTIG_COMPATIBILITY = 0.95


def _message(messages: list[dict[str, str]], status: str, text: str) -> None:
    messages.append({"status": status, "message": text})


def _load_sidecar(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReferenceStagingError(f"Integrity sidecar does not exist: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceStagingError(f"Integrity sidecar is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReferenceStagingError(f"Integrity sidecar must contain a JSON object: {path}")
    return payload


def _verify_sidecar(
    *,
    artifact: str,
    canonical_path: Path,
    sidecar_path: Path,
    configured_md5: object,
) -> dict[str, Any]:
    sidecar = _load_sidecar(sidecar_path)
    if sidecar.get("schema_version") != 1:
        raise ReferenceStagingError(f"{artifact} integrity sidecar has an unsupported schema version.")
    if sidecar.get("artifact") != artifact:
        raise ReferenceStagingError(
            f"{artifact} integrity sidecar identifies artifact {sidecar.get('artifact')!r}."
        )

    observed_sha256, observed_bytes = sha256_and_size(canonical_path)
    locked_sha256 = str(sidecar.get("canonical_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(locked_sha256):
        raise ReferenceStagingError(f"{artifact} sidecar canonical SHA-256 is malformed.")
    if observed_sha256 != locked_sha256:
        raise ReferenceStagingError(
            f"{artifact} canonical SHA-256 mismatch: sidecar {locked_sha256}, observed {observed_sha256}."
        )
    try:
        locked_bytes = int(sidecar.get("canonical_bytes"))
    except (TypeError, ValueError) as exc:
        raise ReferenceStagingError(f"{artifact} sidecar canonical byte size is malformed.") from exc
    if observed_bytes != locked_bytes:
        raise ReferenceStagingError(
            f"{artifact} canonical byte-size mismatch: sidecar {locked_bytes}, observed {observed_bytes}."
        )

    configured = str(configured_md5 or "").strip().lower()
    if configured:
        if not _MD5_RE.fullmatch(configured):
            raise ReferenceStagingError(
                f"Configured {artifact} source MD5 must contain exactly 32 hexadecimal characters."
            )
        if sidecar.get("md5_status") != "VERIFIED":
            raise ReferenceStagingError(
                f"{artifact} source checksum is configured but the sidecar is not VERIFIED."
            )
        if str(sidecar.get("configured_md5") or "").lower() != configured:
            raise ReferenceStagingError(
                f"{artifact} sidecar was produced for a different configured source MD5."
            )
        if str(sidecar.get("source_md5") or "").lower() != configured:
            raise ReferenceStagingError(
                f"{artifact} source MD5 in the sidecar does not match the configured checksum."
            )
    else:
        if sidecar.get("md5_status") != "NOT_CONFIGURED":
            raise ReferenceStagingError(
                f"{artifact} source has no configured MD5 but the sidecar status is not NOT_CONFIGURED."
            )
        if sidecar.get("configured_md5") not in (None, ""):
            raise ReferenceStagingError(
                f"{artifact} sidecar records an MD5 that is absent from the current configuration."
            )
        source_md5 = str(sidecar.get("source_md5") or "")
        if not _MD5_RE.fullmatch(source_md5):
            raise ReferenceStagingError(f"{artifact} sidecar source MD5 is malformed.")

    for field in ("source_bytes", "source_uncompressed_bytes"):
        try:
            value = int(sidecar.get(field))
        except (TypeError, ValueError) as exc:
            raise ReferenceStagingError(f"{artifact} sidecar {field} is malformed.") from exc
        if value <= 0:
            raise ReferenceStagingError(f"{artifact} sidecar {field} must be positive.")
    source_uncompressed_sha256 = str(sidecar.get("source_uncompressed_sha256") or "")
    if not _SHA256_RE.fullmatch(source_uncompressed_sha256):
        raise ReferenceStagingError(
            f"{artifact} sidecar source-uncompressed SHA-256 is malformed."
        )
    return sidecar


def _overall_status(messages: list[dict[str, str]]) -> str:
    statuses = [message.get("status", "PASS") for message in messages] or ["PASS"]
    return max(statuses, key=lambda status: _PRIORITY.get(status, 0))


def _configured_counting_contract(
    config: dict[str, Any],
    feature_type: str | None,
    attribute_type: str | None,
) -> tuple[list[str], str]:
    featurecounts = config.get("featurecounts") or {}
    if not isinstance(featurecounts, dict):
        featurecounts = {}
    raw_features = str(
        feature_type if feature_type is not None else featurecounts.get("feature_type", "exon")
    ).strip()
    features = [value.strip() for value in raw_features.split(",") if value.strip()]
    attribute = str(
        attribute_type if attribute_type is not None else featurecounts.get("attribute_type", "gene_id")
    ).strip()
    if not features:
        raise ReferenceStagingError("Configured featureCounts feature_type is empty.")
    if not attribute or any(character.isspace() for character in attribute):
        raise ReferenceStagingError("Configured featureCounts attribute_type is empty or malformed.")
    return features, attribute


def build_reference_evidence(
    *,
    config: dict[str, Any],
    fasta: Path,
    annotation: Path,
    genome_sidecar: Path,
    annotation_sidecar: Path,
    lock_path: Path,
    feature_type: str | None = None,
    attribute_type: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate staged references and build both check and realized lock payloads."""
    messages: list[dict[str, str]] = []
    reference = config.get("reference") or {}
    if not isinstance(reference, dict):
        reference = {}
    try:
        configured_feature_types, configured_attribute = _configured_counting_contract(
            config, feature_type, attribute_type
        )
    except ReferenceStagingError as exc:
        configured_feature_types, configured_attribute = [], ""
        _message(messages, "FAIL", str(exc))

    artifact_evidence: dict[str, dict[str, Any]] = {}
    for artifact, canonical, sidecar, configured_md5 in (
        ("fasta", fasta, genome_sidecar, reference.get("genome_md5")),
        ("annotation", annotation, annotation_sidecar, reference.get("annotation_md5")),
    ):
        try:
            if not canonical.is_file():
                raise ReferenceStagingError(f"Canonical {artifact} file does not exist: {canonical}")
            if canonical.stat().st_size <= 0:
                raise ReferenceStagingError(f"Canonical {artifact} file is empty: {canonical}")
            artifact_evidence[artifact] = _verify_sidecar(
                artifact=artifact,
                canonical_path=canonical,
                sidecar_path=sidecar,
                configured_md5=configured_md5,
            )
        except (OSError, ReferenceStagingError) as exc:
            artifact_evidence[artifact] = {"error": str(exc)}
            _message(messages, "FAIL", str(exc))

    fasta_metrics: dict[str, object] = {}
    annotation_metrics: dict[str, object] = {}
    fasta_contigs: set[str] = set()
    annotation_contigs: set[str] = set()
    feature_rows_by_contig: dict[str, int] = {}
    try:
        fasta_metrics, fasta_contigs = inspect_fasta(fasta)
    except (OSError, ReferenceStagingError) as exc:
        _message(messages, "FAIL", f"FASTA content validation failed: {exc}")
    try:
        annotation_metrics, annotation_contigs, feature_rows_by_contig = inspect_annotation(
            annotation,
            required_feature_types=set(configured_feature_types),
            required_attribute=configured_attribute or None,
        )
    except (OSError, ReferenceStagingError) as exc:
        _message(messages, "FAIL", f"Annotation content validation failed: {exc}")

    if annotation_metrics:
        evidence_counts = annotation_metrics.get("evidence_counts") or {}
        evidence_total = sum(int(evidence_counts.get(key, 0)) for key in ("gene", "exon", "CDS"))
        if evidence_total == 0:
            _message(
                messages,
                "FAIL",
                "Annotation contains no gene, exon, or CDS feature evidence.",
            )
        counting = annotation_metrics.get("counting_contract") or {}
        configured_rows = int(counting.get("feature_rows", 0))
        missing_attribute = int(counting.get("feature_rows_missing_attribute", 0))
        if configured_feature_types and configured_rows == 0:
            _message(
                messages,
                "FAIL",
                "Annotation contains no rows matching configured featureCounts feature_type "
                f"{','.join(configured_feature_types)!r}.",
            )
        if configured_rows > 0 and missing_attribute > 0:
            examples = counting.get("missing_attribute_line_examples") or []
            _message(
                messages,
                "FAIL",
                f"{missing_attribute} of {configured_rows} configured featureCounts feature rows "
                f"lack a non-empty {configured_attribute!r} attribute"
                + (f" (example lines: {', '.join(str(value) for value in examples)})" if examples else "")
                + ".",
            )

    compatibility: dict[str, object] = {}
    if fasta_contigs and annotation_contigs:
        overlap = fasta_contigs & annotation_contigs
        annotation_only = annotation_contigs - fasta_contigs
        total_feature_rows = sum(feature_rows_by_contig.values())
        compatible_feature_rows = sum(
            count for contig, count in feature_rows_by_contig.items() if contig in fasta_contigs
        )
        annotation_only_feature_rows = total_feature_rows - compatible_feature_rows
        feature_row_fraction = (
            compatible_feature_rows / total_feature_rows if total_feature_rows else 0.0
        )
        annotation_only_ranked = sorted(
            (
                {"contig": contig, "feature_rows": feature_rows_by_contig.get(contig, 0)}
                for contig in annotation_only
            ),
            key=lambda item: (-int(item["feature_rows"]), str(item["contig"])),
        )
        compatibility = {
            "fasta_contigs": len(fasta_contigs),
            "annotation_contigs": len(annotation_contigs),
            "overlap_contigs": len(overlap),
            "annotation_only_contigs": len(annotation_only),
            "annotation_contig_overlap_fraction": len(overlap) / len(annotation_contigs),
            "annotation_feature_rows": total_feature_rows,
            "compatible_feature_rows": compatible_feature_rows,
            "annotation_only_feature_rows": annotation_only_feature_rows,
            "feature_row_overlap_fraction": feature_row_fraction,
            "minimum_feature_row_overlap_fraction": _MIN_FEATURE_ROW_CONTIG_COMPATIBILITY,
            "overlap_examples": sorted(overlap)[:10],
            "annotation_only_examples": sorted(annotation_only)[:10],
            "annotation_only_top_contigs": annotation_only_ranked[:10],
        }
        if feature_row_fraction < _MIN_FEATURE_ROW_CONTIG_COMPATIBILITY:
            qualifier = " identifiers are disjoint;" if not overlap else " compatibility is below threshold;"
            _message(
                messages,
                "FAIL",
                "FASTA/annotation contig" + qualifier
                + f" {compatible_feature_rows} of {total_feature_rows} annotation feature rows "
                f"({feature_row_fraction:.2%}) occur on FASTA contigs; required >= "
                f"{_MIN_FEATURE_ROW_CONTIG_COMPATIBILITY:.0%}.",
            )

    if not any(message["status"] == "FAIL" for message in messages):
        genome_status = artifact_evidence.get("fasta", {}).get("md5_status")
        annotation_status = artifact_evidence.get("annotation", {}).get("md5_status")
        _message(
            messages,
            "PASS",
            "Reference FASTA and annotation passed canonical-hash, structure, configured-counting, "
            f"and feature-weighted contig-compatibility checks ({compatibility.get('feature_row_overlap_fraction', 0):.2%}). "
            f"Source MD5 states: genome={genome_status}, annotation={annotation_status}.",
        )

    status = _overall_status(messages)
    lock: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "reference": {
            key: reference.get(key)
            for key in (
                "organism_name",
                "strain",
                "source",
                "release",
                "package_id",
                "assembly_accession",
                "annotation_format",
            )
            if reference.get(key) is not None
        },
        "genome": {
            "path": str(fasta),
            "integrity": artifact_evidence.get("fasta", {}),
            "content": fasta_metrics,
        },
        "annotation": {
            "path": str(annotation),
            "integrity": artifact_evidence.get("annotation", {}),
            "content": annotation_metrics,
        },
        "contig_compatibility": compatibility,
        "counting_contract": annotation_metrics.get("counting_contract") or {
            "feature_types": configured_feature_types,
            "attribute_type": configured_attribute,
        },
        "messages": messages,
    }
    check: dict[str, Any] = {
        "check": "05_reference_validation",
        "status": status,
        "messages": messages,
        "evidence": {
            "reference_lock": str(lock_path),
            "genome_canonical_sha256": artifact_evidence.get("fasta", {}).get("canonical_sha256"),
            "annotation_canonical_sha256": artifact_evidence.get("annotation", {}).get("canonical_sha256"),
            "contig_compatibility": compatibility,
            "counting_contract": annotation_metrics.get("counting_contract") or {
                "feature_types": configured_feature_types,
                "attribute_type": configured_attribute,
            },
        },
    }
    return check, lock


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and lock realized reference artifacts.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--fasta", required=True)
    parser.add_argument("--annotation", required=True)
    parser.add_argument("--genome-sidecar", required=True)
    parser.add_argument("--annotation-sidecar", required=True)
    parser.add_argument("--feature-type", default=None)
    parser.add_argument("--attribute-type", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args()

    try:
        config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ValueError("Configuration root must be a mapping.")
        check, lock = build_reference_evidence(
            config=config,
            fasta=Path(args.fasta),
            annotation=Path(args.annotation),
            genome_sidecar=Path(args.genome_sidecar),
            annotation_sidecar=Path(args.annotation_sidecar),
            lock_path=Path(args.lock),
            feature_type=args.feature_type,
            attribute_type=args.attribute_type,
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        check = {
            "check": "05_reference_validation",
            "status": "FAIL",
            "messages": [{"status": "FAIL", "message": f"Reference validation could not run: {exc}"}],
            "evidence": {"reference_lock": str(args.lock)},
        }
        lock = {
            "schema_version": 1,
            "status": "FAIL",
            "genome": {"path": str(args.fasta)},
            "annotation": {"path": str(args.annotation)},
            "contig_compatibility": {},
            "counting_contract": {
                "feature_types": [args.feature_type] if args.feature_type else [],
                "attribute_type": args.attribute_type,
            },
            "messages": check["messages"],
        }

    atomic_write_json(Path(args.out), check)
    atomic_write_json(Path(args.lock), lock)
    return 1 if check["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
