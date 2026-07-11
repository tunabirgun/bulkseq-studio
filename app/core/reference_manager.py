from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from app.core.paths import data_path


def load_reference_catalog() -> list[dict[str, object]]:
    with data_path("reference_catalog.yaml").open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload.get("references", [])


def catalog_entry_for_organism(organism_name: str | None) -> dict[str, object] | None:
    # Case-insensitive match on organism_name so GUI/programmatic organism-write
    # sites can pull the per-organism enrichment/PPI identifiers from the catalog.
    if not organism_name:
        return None
    target = organism_name.strip().casefold()
    if not target or target == "unset":
        return None
    for entry in load_reference_catalog():
        name = str(entry.get("organism_name", "")).strip().casefold()
        if name and name == target:
            return entry
    return None


def validate_reference(genome_fasta: Path, annotation: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    # .is_file() (not .exists()): an empty text field becomes Path("") == Path(".") whose .exists()
    # is True (the cwd), which would slip past this guard and then raise PermissionError on open(".").
    if not genome_fasta.is_file():
        messages.append({"status": "FAIL", "message": f"Genome FASTA not found: {genome_fasta}"})
    if not annotation.is_file():
        messages.append({"status": "FAIL", "message": f"Annotation file not found: {annotation}"})
    if messages:
        return messages

    fasta_contigs = _first_fasta_contigs(genome_fasta)
    if not fasta_contigs:
        messages.append({"status": "FAIL", "message": "Genome FASTA has no sequence records."})
    feature_info = _annotation_info(annotation)
    if not feature_info["contigs"]:
        messages.append({"status": "FAIL", "message": "Annotation has no parseable feature rows."})
    if not feature_info["gene_features"]:
        messages.append({"status": "FAIL", "message": "Annotation has no gene features."})
    if not (feature_info["exon_features"] or feature_info["cds_features"]):
        messages.append({"status": "WARNING", "message": "Annotation has no exon or CDS features."})
    if fasta_contigs and feature_info["contigs"] and fasta_contigs.isdisjoint(feature_info["contigs"]):
        messages.append({"status": "FAIL", "message": "No contig names match between FASTA and annotation."})
    if not feature_info["gene_name"]:
        messages.append({"status": "WARNING", "message": "No gene_name attribute detected; reports may use stable IDs only."})
    if not messages:
        messages.append({"status": "PASS", "message": "Reference passed structural validation."})
    return messages


def create_reference_manifest(target_dir: Path, organism_name: str, genome_fasta: Path, annotation: Path, extra: dict[str, str] | None = None) -> dict[str, object]:
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "organism_name": organism_name,
        "genome_fasta": str(genome_fasta),
        "annotation_file": str(annotation),
        "genome_md5": md5sum(genome_fasta),
        "annotation_md5": md5sum(annotation),
        "validation": validate_reference(genome_fasta, annotation),
    }
    if extra:
        manifest.update(extra)
    (target_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (target_dir / "validation_report.json").write_text(json.dumps(manifest["validation"], indent=2), encoding="utf-8")
    (target_dir / "checksums.md5").write_text(f"{manifest['genome_md5']}  {genome_fasta.name}\n{manifest['annotation_md5']}  {annotation.name}\n", encoding="utf-8")
    return manifest


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_text(path: Path):
    # Transparently read gzipped references: the Reference Manager file picker offers *.fa.gz /
    # *.fasta.gz / *.gtf.gz / *.gff3.gz, so validate_reference must decompress a .gz input — reading
    # the DEFLATE bytes as plain text finds no '>' headers / no tab-split rows and falsely FAILs a
    # valid reference. Reads headers/feature rows only, so it stays cheap on a large gzipped genome.
    import gzip
    opener = gzip.open if str(path).endswith(".gz") else open
    return opener(path, "rt", encoding="utf-8", errors="replace")


def _first_fasta_contigs(path: Path, limit: int = 10000) -> set[str]:
    contigs: set[str] = set()
    with _open_text(path) as handle:
        for line in handle:
            if line.startswith(">"):
                contigs.add(line[1:].split()[0])
                if len(contigs) >= limit:
                    break
    return contigs


def _annotation_info(path: Path) -> dict[str, object]:
    contigs: set[str] = set()
    gene_features = exon_features = cds_features = gene_name = 0
    with _open_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            contigs.add(parts[0])
            feature = parts[2].lower()
            attrs = parts[8]
            gene_features += feature == "gene"
            exon_features += feature == "exon"
            cds_features += feature == "cds"
            gene_name += "gene_name" in attrs or "Name=" in attrs
    return {"contigs": contigs, "gene_features": gene_features, "exon_features": exon_features, "cds_features": cds_features, "gene_name": gene_name}
