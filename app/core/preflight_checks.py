"""Pre-run findings shared by the interface's Start gate and `bulkseq check`.

Each function reads only the project configuration and files, so both front doors
report the same findings for the same project.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from app.core.config_models import (
    AppConfig,
    Deseq2ResultsDirectionProvenance,
    Deseq2ResultsFileProvenance,
)
from app.core.de_results import validate_recorded_project_copy
from app.core.metadata import validate_metadata

# Input routes that align raw reads, mirroring the Snakefile's
# `not (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE)` guard on the
# alignment-only targets.
ALIGNMENT_ROUTES = ("fastq", "sra", "mixed")
PENDING_INPUT_ROUTES = ("sra", "count_matrix", "microarray", "deseq2_results")


def design_variables(config: AppConfig, formula: str | None = None) -> list[str]:
    """Metadata columns a design formula references, plus every contrast factor."""
    text = str(formula if formula is not None else config.deseq2.design_formula).split("~", 1)[-1]
    variables = [t.strip() for t in re.split(r"[+*:]", text) if t.strip()]
    for contrast in config.deseq2.contrasts:
        if contrast.factor and contrast.factor not in variables:
            variables.append(contrast.factor)
    return variables


def active_contrast(config: AppConfig, numerator: str | None = None,
                    denominator: str | None = None) -> tuple[str, str] | None:
    if config.input.type == "deseq2_results":
        direction = config.input.deseq2_results_direction
        if direction.confirmed and direction.numerator and direction.denominator:
            return direction.numerator, direction.denominator
        return None
    c0 = config.deseq2.contrasts[0] if config.deseq2.contrasts else None
    num = (numerator if numerator is not None else (c0.numerator if c0 else "")) or ""
    den = (denominator if denominator is not None else (c0.denominator if c0 else "")) or ""
    return (num.strip(), den.strip()) if num.strip() and den.strip() else None


def route_preflight_messages(config: AppConfig, project_root: Path) -> list[dict[str, str]]:
    """Route-specific input files and reference requirements."""
    messages: list[dict[str, str]] = []
    mode = config.input.type
    configured_input = None
    if mode == "count_matrix":
        configured_input = config.input.count_matrix
    elif mode == "deseq2_results":
        configured_input = config.input.deseq2_results
    elif mode == "microarray" and config.microarray.source == "local_matrix":
        configured_input = config.microarray.expression_matrix
    if configured_input:
        input_path = Path(configured_input)
        if not input_path.is_absolute():
            input_path = project_root / input_path
        if not input_path.exists():
            messages.append({"status": "FAIL", "message": f"Configured input file is missing: {configured_input}"})
    elif mode in ("count_matrix", "deseq2_results"):
        messages.append({"status": "FAIL",
                         "message": f"The {mode.replace('_', ' ')} route has no configured input table."})
    if mode in ALIGNMENT_ROUTES:
        ref = config.reference
        if not ((ref.genome_fasta_url and ref.annotation_gtf_url) or (ref.genome_fasta and ref.annotation_file)):
            messages.append({"status": "FAIL", "message": (
                "Raw-read processing needs a genome FASTA and annotation. Select a preset or custom reference.")})
    if not messages:
        messages.append({"status": "PASS", "message": "The active input route and reference requirements are configured."})
    return messages


def enrichment_config_messages(config: AppConfig) -> list[dict[str, str]]:
    """Enrichment switched on with nothing that identifies the organism to any route."""
    if not config.workflow.enrichment:
        return []
    enr = config.enrichment
    transfer = enr.transfer != "off" and (config.ppi.taxon or enr.transfer_emapper or enr.transfer_ko_table)
    if not (enr.kegg_organism or enr.orgdb or enr.gprofiler_organism or transfer):
        return [{"status": "REVIEW_REQUIRED",
                 "message": "Enrichment is enabled but no organism is configured "
                            "(no OrgDb, KEGG code, g:Profiler organism or STRING taxon). Enrichment and the "
                            "STRING PPI network will be skipped. Select your organism on the "
                            "Reference Manager tab, or disable Enrichment."}]
    return []


def deseq2_results_preflight_messages(config: AppConfig, project_root: Path) -> list[dict[str, str]]:
    """Direction, full project copy and import-time provenance of imported results."""
    messages: list[dict[str, str]] = []
    try:
        confirmed = Deseq2ResultsDirectionProvenance.model_validate(
            config.input.deseq2_results_direction.model_dump(mode="json"))
        if not confirmed.confirmed:
            raise ValueError("the recorded direction has not been explicitly confirmed")
    except ValueError as exc:
        messages.append({"status": "FAIL",
                         "message": f"Imported-results direction provenance is incomplete or invalid: {exc}"})
    else:
        messages.append({"status": "PASS", "message": (
            "Imported-results direction confirmed: positive log2FoldChange means higher in "
            f"{confirmed.numerator} than {confirmed.denominator}.")})
    configured = config.input.deseq2_results
    if not configured:
        messages.append({"status": "FAIL", "message": "The external-results route has no configured project-copy table."})
        return messages
    project_copy = Path(configured)
    if not project_copy.is_absolute():
        project_copy = project_root / project_copy
    if not project_copy.exists():
        messages.append({"status": "FAIL", "message": f"The external-results project copy is missing: {configured}"})
        return messages
    file_provenance = config.input.deseq2_results_provenance
    file_provenance_invalid = False
    try:
        file_provenance = Deseq2ResultsFileProvenance.model_validate(file_provenance.model_dump(mode="json"))
    except ValueError as exc:
        file_provenance_invalid = True
        messages.append({"status": "FAIL", "message": f"External-results file provenance is invalid: {exc}"})
    validated, errors = validate_recorded_project_copy(project_copy, file_provenance, configured_project_copy=configured)
    messages.extend({"status": "FAIL", "message": error} for error in errors)
    if validated is not None and not errors and not file_provenance_invalid:
        messages.append({"status": "PASS", "message": (
            f"Validated the complete external-results project copy: {validated.row_count:,} rows, "
            f"{len(validated.column_names)} columns, SHA-256 {validated.sha256[:12]}….")})
    return messages


def input_validation_messages(config: AppConfig, project_root: Path, samples: pd.DataFrame, *,
                              formula: str | None = None, numerator: str | None = None,
                              denominator: str | None = None) -> list[dict[str, str]]:
    """Every finding the Start gate records in check 01, in its order."""
    if config.input.type == "deseq2_results":
        messages = deseq2_results_preflight_messages(config, project_root)
    else:
        messages = validate_metadata(
            samples, allow_pending_sra=config.input.type in PENDING_INPUT_ROUTES,
            design_variables=design_variables(config, formula),
            contrast=active_contrast(config, numerator, denominator))
    return list(messages) + route_preflight_messages(config, project_root) + enrichment_config_messages(config)
