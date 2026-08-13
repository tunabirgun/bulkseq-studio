from __future__ import annotations

import hashlib
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from app.core.metadata import read_user_table


GENE_ID_ALIASES: tuple[str, ...] = (
    "gene_id", "gene", "geneid", "id", "ensembl", "ensembl_id",
)
LOG2FC_ALIASES: tuple[str, ...] = (
    "log2FoldChange", "log2fc", "logFC", "log2_fold_change",
)
ADJUSTED_P_ALIASES: tuple[str, ...] = (
    "padj", "adj.P.Val", "FDR", "qvalue", "q_value", "adjp", "p_adj", "padj_BH",
)


class DETableValidationError(ValueError):
    """A user-facing validation failure in an external DE-results table."""

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class ValidatedDETable:
    path: Path
    dataframe: pd.DataFrame
    gene_id_column: str
    log2fc_column: str
    adjusted_p_column: str
    sha256: str
    byte_size: int

    @property
    def row_count(self) -> int:
        return len(self.dataframe.index)

    @property
    def column_names(self) -> list[str]:
        return [str(column) for column in self.dataframe.columns]


@dataclass(frozen=True)
class ExternalDEImportDetails:
    numerator: str
    denominator: str
    upstream_method: str = "unknown"
    lfc_shrinkage: str = "unknown"
    p_adjustment_method: str = "unknown"


def _pick_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    by_folded_name: dict[str, str] = {}
    for column in columns:
        # Keep header matching aligned with ingest_deseq2_results.R: aliases are
        # case-insensitive, but surrounding whitespace is not silently normalized.
        by_folded_name.setdefault(column.casefold(), column)
    for alias in aliases:
        match = by_folded_name.get(alias.casefold())
        if match is not None:
            return match
    return None


def _read_full_table(path: Path) -> pd.DataFrame:
    # The project copy deliberately preserves the original bytes and may therefore
    # contain TSV data despite its stable .csv project filename. Let pandas sniff
    # comma versus tab content instead of trusting the suffix.
    with warnings.catch_warnings():
        # Without index_col=False, pandas can silently reinterpret the first value
        # as an index when a malformed row has one extra field. Turning its resulting
        # truncation warning into an error prevents a decimal-comma/extra-token row
        # from being accepted with shifted scientific columns.
        warnings.simplefilter("error", pd.errors.ParserWarning)
        return read_user_table(
            path,
            sep=None,
            engine="python",
            dtype=str,
            keep_default_na=False,
            index_col=False,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric_errors(series: pd.Series, label: str, *, probability: bool = False) -> list[str]:
    raw = series.astype(str).str.strip()
    missing = raw.eq("") | raw.str.casefold().eq("na")
    parsed = pd.to_numeric(raw.mask(missing), errors="coerce")
    explicit_nan = ~missing & raw.str.casefold().isin({"nan", "+nan", "-nan"})
    malformed = ~missing & parsed.isna() & ~explicit_nan
    errors: list[str] = []
    if malformed.any():
        examples = raw[malformed].head(3).tolist()
        shown = ", ".join(repr(value) for value in examples)
        errors.append(
            f"{label} must contain a complete numeric token or an explicit missing value "
            f"(blank/NA); {int(malformed.sum())} token(s) are invalid (for example {shown})."
        )

    numeric = ~missing & parsed.notna()
    finite = pd.Series(False, index=raw.index)
    if numeric.any():
        finite.loc[numeric] = np.isfinite(parsed.loc[numeric].to_numpy(dtype=float))
    nonfinite = explicit_nan | (numeric & ~finite)
    if nonfinite.any():
        errors.append(
            f"{label} contains {int(nonfinite.sum())} non-finite value(s) (NaN or infinite); "
            "use blank/NA only when a result is genuinely missing."
        )
    if not finite.any():
        errors.append(f"{label} must contain at least one finite numeric value.")
    if probability:
        outside = finite & ((parsed < 0.0) | (parsed > 1.0))
        if outside.any():
            errors.append(
                f"Every finite {label.lower()} must be within [0, 1]; "
                f"{int(outside.sum())} value(s) are outside that range."
            )
    return errors


def validate_de_results_table(path: Path) -> ValidatedDETable:
    """Read and validate every row of an externally computed DE-results table.

    The function never rewrites values or signs. It establishes only that the
    identifier, effect-size and adjusted-p columns can be consumed without silent
    coercion by the downstream results-only route.
    """
    path = Path(path)
    try:
        dataframe = _read_full_table(path)
    except Exception as exc:
        raise DETableValidationError([f"Could not read the differential-expression table: {exc}"]) from exc

    if dataframe.empty:
        raise DETableValidationError(["The differential-expression table contains no data rows."])

    columns = [str(column) for column in dataframe.columns]
    gene_column = _pick_column(columns, GENE_ID_ALIASES)
    if gene_column is None and columns:
        first = columns[0].casefold()
        if not first or first.startswith("unnamed:"):
            gene_column = columns[0]
    log2fc_column = _pick_column(columns, LOG2FC_ALIASES)
    adjusted_p_column = _pick_column(columns, ADJUSTED_P_ALIASES)

    errors: list[str] = []
    if gene_column is None:
        errors.append(
            "No supported gene identifier column was found. Use gene_id, gene, geneid, id, "
            "ensembl/ensembl_id, or an exported R row-name first column."
        )
    if log2fc_column is None:
        errors.append(
            "No log2 fold-change column was found. Accepted names include log2FoldChange, "
            "log2FC, logFC, and log2_fold_change."
        )
    if adjusted_p_column is None:
        errors.append(
            "No adjusted-p column was found. Accepted names include padj, adj.P.Val, FDR, "
            "qvalue, q_value, adjp, p_adj, and padj_BH."
        )
    if errors:
        raise DETableValidationError(errors)

    assert gene_column is not None
    assert log2fc_column is not None
    assert adjusted_p_column is not None
    gene_ids = dataframe[gene_column].astype(str).str.strip()
    blank = gene_ids.eq("")
    if blank.any():
        errors.append(f"Gene identifiers must not be blank; {int(blank.sum())} blank row(s) were found.")
    unsafe = gene_ids.map(
        lambda gene: any(
            character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in gene
        )
    ) & ~blank
    if unsafe.any():
        examples = gene_ids[unsafe].head(5).tolist()
        errors.append(
            "Gene identifiers must not contain whitespace or control characters "
            f"(for example {', '.join(examples)})."
        )
    duplicate = gene_ids.duplicated(keep=False) & ~blank
    if duplicate.any():
        examples = gene_ids[duplicate].drop_duplicates().head(5).tolist()
        errors.append(
            f"Gene identifiers must be unique; {int(duplicate.sum())} row(s) participate in duplicates "
            f"(for example {', '.join(examples)})."
        )
    errors.extend(_numeric_errors(dataframe[log2fc_column], "log2 fold change"))
    errors.extend(_numeric_errors(dataframe[adjusted_p_column], "Adjusted p value", probability=True))
    if errors:
        raise DETableValidationError(errors)

    stat = path.stat()
    return ValidatedDETable(
        path=path,
        dataframe=dataframe,
        gene_id_column=gene_column,
        log2fc_column=log2fc_column,
        adjusted_p_column=adjusted_p_column,
        sha256=_sha256(path),
        byte_size=stat.st_size,
    )


def provenance_payload(
    validated: ValidatedDETable,
    *,
    original_basename: str,
    imported_at: str,
    project_copy: str,
    upstream_method: str = "unknown",
    lfc_shrinkage: str = "unknown",
    p_adjustment_method: str = "unknown",
) -> dict[str, Any]:
    return {
        "original_basename": str(original_basename).replace("\\", "/").rsplit("/", 1)[-1],
        "imported_at": imported_at,
        "project_copy": project_copy,
        "sha256": validated.sha256,
        "byte_size": validated.byte_size,
        "row_count": validated.row_count,
        "column_names": validated.column_names,
        "gene_id_column": validated.gene_id_column,
        "log2fc_column": validated.log2fc_column,
        "adjusted_p_column": validated.adjusted_p_column,
        "upstream_method": upstream_method.strip() or "unknown",
        "lfc_shrinkage": lfc_shrinkage,
        "p_adjustment_method": p_adjustment_method.strip() or "unknown",
    }


def _as_mapping(record: Any) -> Mapping[str, Any]:
    if record is None:
        return {}
    if isinstance(record, Mapping):
        return record
    model_dump = getattr(record, "model_dump", None)
    return model_dump(mode="json") if callable(model_dump) else {}


def validate_recorded_project_copy(
    path: Path,
    record: Any,
    *,
    configured_project_copy: str | None = None,
) -> tuple[ValidatedDETable | None, list[str]]:
    """Validate the copy and compare it with import-time integrity/schema facts."""
    try:
        validated = validate_de_results_table(path)
    except DETableValidationError as exc:
        return None, list(exc.errors)

    expected = _as_mapping(record)
    required = (
        "original_basename", "imported_at", "project_copy", "sha256", "byte_size",
        "row_count", "column_names", "gene_id_column", "log2fc_column", "adjusted_p_column",
    )
    missing = [name for name in required if expected.get(name) in (None, "", [])]
    errors: list[str] = []
    if missing:
        errors.append(
            "The project copy has no complete import-time provenance record "
            f"(missing: {', '.join(missing)}). Re-import the original table."
        )
        return validated, errors

    if configured_project_copy is not None and expected.get("project_copy") != configured_project_copy:
        errors.append(
            "The configured project-copy path differs from the path recorded at import; re-import the table."
        )
    if expected.get("sha256") != validated.sha256:
        errors.append("The external-results project copy changed after import (SHA-256 mismatch).")
    if expected.get("byte_size") != validated.byte_size:
        errors.append("The external-results project-copy byte size changed after import.")
    if expected.get("row_count") != validated.row_count:
        errors.append("The external-results project-copy row count changed after import.")
    if list(expected.get("column_names") or []) != validated.column_names:
        errors.append("The external-results project-copy column schema changed after import.")
    for key, actual, label in (
        ("gene_id_column", validated.gene_id_column, "gene identifier"),
        ("log2fc_column", validated.log2fc_column, "log2 fold-change"),
        ("adjusted_p_column", validated.adjusted_p_column, "adjusted-p"),
    ):
        if expected.get(key) != actual:
            errors.append(f"The recorded {label} column no longer matches the project copy.")
    return validated, errors
