from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from app.constants import REQUIRED_METADATA_COLUMNS, SAFE_ID_PATTERN


def dataframe_from_rows(rows: list[dict[str, str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=REQUIRED_METADATA_COLUMNS + ["fastq_2", "replicate", "batch"])
    return pd.DataFrame(rows)


def save_metadata(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)


def load_metadata(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", dtype=str).fillna("")


def validate_metadata(df: pd.DataFrame, allow_pending_sra: bool = False, design_variables: list[str] | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    missing = [col for col in REQUIRED_METADATA_COLUMNS if col not in df.columns]
    if missing:
        messages.append({"status": "FAIL", "message": f"Missing required metadata columns: {', '.join(missing)}"})
        return messages

    ids = df["sample_id"].astype(str).tolist()
    duplicates = [sid for sid, count in Counter(ids).items() if count > 1]
    if duplicates:
        messages.append({"status": "FAIL", "message": f"Duplicate sample_id values: {', '.join(duplicates)}"})

    unsafe = [sid for sid in ids if not re.match(SAFE_ID_PATTERN, sid)]
    if unsafe:
        messages.append({"status": "FAIL", "message": f"Unsafe sample_id values: {', '.join(unsafe)}"})

    for idx, row in df.iterrows():
        layout = str(row.get("layout", "")).lower()
        r1 = str(row.get("fastq_1", ""))
        r2 = str(row.get("fastq_2", ""))
        if not allow_pending_sra and r1 and not Path(r1).exists():
            messages.append({"status": "FAIL", "message": f"Row {idx + 1}: FASTQ R1 does not exist: {r1}"})
        if layout == "paired" and not r2:
            messages.append({"status": "FAIL", "message": f"Row {idx + 1}: paired-end sample is missing fastq_2."})
        if layout == "paired" and r2 and not allow_pending_sra and not Path(r2).exists():
            messages.append({"status": "FAIL", "message": f"Row {idx + 1}: FASTQ R2 does not exist: {r2}"})

    empty_conditions = df["condition"].astype(str).str.strip().isin(["", "unknown"]).sum()
    if empty_conditions:
        messages.append({"status": "REVIEW_REQUIRED", "message": f"{empty_conditions} sample(s) have empty or unknown condition."})

    if design_variables:
        missing_design = [col for col in design_variables if col not in df.columns]
        if missing_design:
            messages.append({"status": "FAIL", "message": f"Design variables missing from metadata: {', '.join(missing_design)}"})

    counts = df.groupby("condition", dropna=False)["sample_id"].count().to_dict() if "condition" in df.columns else {}
    for condition, count in counts.items():
        if condition in ("", "unknown"):
            continue
        if count < 2:
            messages.append({"status": "WARNING", "message": f"Condition '{condition}' has fewer than two biological replicates."})
        elif count < 3:
            messages.append({"status": "WARNING", "message": f"Condition '{condition}' has fewer than the recommended three biological replicates."})

    messages.extend(detect_batch_condition_confounding(df))
    if not messages:
        messages.append({"status": "PASS", "message": "Metadata passed validation."})
    return messages


def detect_batch_condition_confounding(df: pd.DataFrame) -> list[dict[str, str]]:
    if "batch" not in df.columns or "condition" not in df.columns:
        return []
    batches: dict[str, set[str]] = defaultdict(set)
    conditions: dict[str, set[str]] = defaultdict(set)
    for _, row in df.iterrows():
        batch = str(row.get("batch", ""))
        condition = str(row.get("condition", ""))
        if batch and batch != "unknown" and condition and condition != "unknown":
            batches[batch].add(condition)
            conditions[condition].add(batch)
    if batches and all(len(v) == 1 for v in batches.values()) and all(len(v) == 1 for v in conditions.values()):
        return [{"status": "REVIEW_REQUIRED", "message": "Batch and condition appear confounded; design matrix may not be full rank."}]
    return []
