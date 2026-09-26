"""Per-sample display labels shared by the run summary and the HTML report.

`library_name` is an optional sample-sheet column: a description of the sequencing library that
may be blank and may repeat across rows. It is never a key -- `sample_id` remains the sole
identifier for file paths, matrix columns, results-table columns and every sample match. The
same rule is implemented in R in workflow/scripts/de_common.R, because the figure scripts read
the column from colData rather than from this module; tests/test_sample_labels.py runs both over
the same fixtures and fails when they disagree.
"""

from __future__ import annotations

import csv
import io
from collections import Counter

SAMPLE_ID_COLUMN = "sample_id"
LIBRARY_NAME_COLUMN = "library_name"

# R's read.delim trims these and converts the bare token NA to missing. Matching it exactly keeps
# a sheet reading the same way in the figures (R) and in the reports (Python); str.strip() would
# additionally consume form feed and Unicode spaces that trimws() leaves in place.
_R_WHITESPACE = " \t\r\n"
_R_NA_TOKEN = "NA"


def _clean_name(value) -> str:
    if value is None:
        return ""
    text = str(value).strip(_R_WHITESPACE)
    return "" if text == _R_NA_TOKEN else text


def sample_display_labels(sample_ids, library_names=None) -> list[str]:
    """Label each sample: the library name, the name plus its sample id when the name repeats,
    or the sample id when the name is blank or the column is absent."""
    ids = [str(value) for value in sample_ids]
    if library_names is None:
        return ids
    names = [_clean_name(value) for value in library_names]
    if len(names) != len(ids):
        raise ValueError("library_name and sample_id have different lengths.")
    counts = Counter(name for name in names if name)
    return [f"{name} ({sid})" if counts[name] > 1 else (name or sid)
            for sid, name in zip(ids, names)]


def retained_sample_ids(project_root) -> list[str] | None:
    """Sample ids the fitted model kept, from results/deseq2/pca_coordinates.csv; None before a
    model exists. The figures label only these samples, so a repeated library name whose other
    rows were excluded must not carry a sample-id suffix in the reports either."""
    from pathlib import Path
    path = Path(project_root) / "results" / "deseq2" / "pca_coordinates.csv"
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            ids = [row.get(SAMPLE_ID_COLUMN, "") for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return None
    ids = [sid.strip(_R_WHITESPACE) for sid in ids if sid and sid.strip(_R_WHITESPACE)]
    return ids or None


def sample_label_rows(samples_tsv: str, retained=None) -> list[tuple[str, str, str]]:
    """(sample id, library name, display label) per row of a sample sheet.

    With `retained`, only those samples are listed and labelled, matching the figures. Returns an
    empty list when the sheet has no library_name column or records no name at all, so a report
    that renders these rows adds nothing to a project that does not use the column.
    """
    text = samples_tsv.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not reader.fieldnames or LIBRARY_NAME_COLUMN not in reader.fieldnames:
        return []
    ids: list[str] = []
    names: list[str] = []
    for row in reader:
        sid = (row.get(SAMPLE_ID_COLUMN) or "").strip(_R_WHITESPACE)
        if not sid or (retained is not None and sid not in retained):
            continue
        ids.append(sid)
        names.append(_clean_name(row.get(LIBRARY_NAME_COLUMN)))
    if not any(names):
        return []
    return list(zip(ids, names, sample_display_labels(ids, names)))
