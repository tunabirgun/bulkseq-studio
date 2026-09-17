from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

import numpy as np
import pandas as pd


FEATURECOUNTS_METADATA = ("Chr", "Start", "End", "Strand", "Length")
MILLION_TOTAL_WARNING = (
    "Count-matrix column totals are near 1,000,000. This is a normalized-data suspicion only: "
    "valid raw-count columns can have the same total. Confirm that the matrix contains "
    "unnormalized counts."
)
_MISSING_TOKENS = {"", "na", "n/a", "null", "none"}
_MILLION = Decimal(1_000_000)
_MAX_COUNT = Decimal(int(np.iinfo(np.int64).max))


class CountMatrixValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedCountValues:
    values: pd.DataFrame
    has_fractional: bool
    near_million_totals: bool


def validate_count_values(
    frame: pd.DataFrame,
    sample_columns: list[str],
    *,
    estimated_counts: bool,
) -> ValidatedCountValues:
    if not sample_columns:
        raise CountMatrixValidationError("The matrix has no sample-count columns.")

    parsed: dict[str, list[Decimal]] = {}
    totals: list[Decimal] = []
    has_fractional = False
    for column in sample_columns:
        values: list[Decimal] = []
        for position, raw in enumerate(frame[column].tolist(), start=2):
            if pd.isna(raw) or str(raw).strip().casefold() in _MISSING_TOKENS:
                raise CountMatrixValidationError(
                    f"Count matrix row {position}, column '{column}' has a missing value."
                )
            try:
                value = Decimal(str(raw).strip())
            except InvalidOperation as exc:
                raise CountMatrixValidationError(
                    f"Count matrix row {position}, column '{column}' is not numeric."
                ) from exc
            if not value.is_finite():
                raise CountMatrixValidationError(
                    f"Count matrix row {position}, column '{column}' must be finite."
                )
            if value < 0:
                raise CountMatrixValidationError(
                    f"Count matrix row {position}, column '{column}' is negative. "
                    "RNA-seq counts must be non-negative."
                )
            if value > _MAX_COUNT:
                raise CountMatrixValidationError(
                    f"Count matrix row {position}, column '{column}' exceeds the supported "
                    "signed 64-bit count range."
                )
            if value != value.to_integral_value():
                has_fractional = True
            values.append(value)
        parsed[column] = values
        totals.append(sum(values, Decimal(0)))

    if has_fractional and not estimated_counts:
        raise CountMatrixValidationError(
            "The matrix contains non-integer values. Enable the explicit RSEM/tximport "
            "estimated-counts option only when these are estimated counts; normalized or "
            "transformed values such as TPM, FPKM/RPKM, log-CPM, RMA, or normalized DESeq2 "
            "values are not valid input."
        )

    converted = {
        column: [int(value.to_integral_value(rounding=ROUND_HALF_EVEN)) for value in values]
        for column, values in parsed.items()
    }
    near_million = bool(totals) and (
        2 * sum(abs(total - _MILLION) < (_MILLION / 100) for total in totals) >= len(totals)
    )
    return ValidatedCountValues(
        values=pd.DataFrame(converted, index=frame.index, dtype=object),
        has_fractional=has_fractional,
        near_million_totals=near_million,
    )
