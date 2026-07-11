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
    # Serialize first, then write ONLY if the bytes changed. Re-saving an unchanged sample sheet — as
    # happens when Resuming a stopped run — must NOT touch samples.tsv's mtime, or Snakemake reruns
    # every rule that reads it (rebuilding the whole pipeline instead of continuing). Explicit UTF-8 so
    # non-ASCII metadata (e.g. a Greek delta in a GEO genotype) writes cleanly on any platform; write via
    # bytes (newline='' semantics) so it stays byte-identical to the previous df.to_csv(path, ...) output.
    import io
    buf = io.StringIO()
    df.to_csv(buf, sep="\t", index=False)
    data = buf.getvalue().encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)


def load_metadata(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8").fillna("")


def validate_metadata(df: pd.DataFrame, allow_pending_sra: bool = False,
                      design_variables: list[str] | None = None,
                      contrast: tuple[str, str] | None = None) -> list[dict[str, str]]:
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
    messages.extend(detect_dataset_confounding(df, contrast))
    messages.extend(detect_multistudy_organism_mismatch(df))
    messages.extend(detect_unsafe_dataset_names(df))
    messages.extend(detect_multistudy_admissibility(df, contrast))
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


def _dataset_levels(df: pd.DataFrame) -> pd.Series:
    return df["dataset"].astype(str).str.strip()


def dataset_condition_crosstab(df: pd.DataFrame):
    """samples-per (study-of-origin × condition) table, or None when there is no dataset column."""
    if "dataset" not in df.columns or "condition" not in df.columns:
        return None
    return pd.crosstab(_dataset_levels(df), df["condition"].astype(str).str.strip())


def detect_multistudy_organism_mismatch(df: pd.DataFrame) -> list[dict[str, str]]:
    """FAIL when a multi-study merge mixes organisms.

    A meta-analysis / pooled multi-study run needs ONE organism and a shared gene-id namespace so
    the per-study gene sets intersect. Uses the 'organism' column when present; [] for a single
    study or when the column is absent.
    """
    if "dataset" not in df.columns or "organism" not in df.columns:
        return []
    if _dataset_levels(df).replace("", pd.NA).nunique(dropna=True) <= 1:
        return []
    # Dedup case-insensitively so 'Homo sapiens' / 'homo sapiens' are one organism, but report
    # the original spelling.
    orgs = {}
    for o in df["organism"].astype(str).str.strip():
        if o and o.lower() not in ("unknown", "nan", "na"):
            orgs.setdefault(o.casefold(), o)
    if len(orgs) > 1:
        return [{"status": "FAIL", "message": (
            f"The merged studies use different organisms ({', '.join(sorted(orgs.values()))}). A multi-study "
            f"analysis must combine studies of the SAME organism with a shared gene-id namespace — "
            f"use one organism, or map genes to a common ortholog space first.")}]
    return []


def detect_dataset_confounding(df: pd.DataFrame,
                               contrast: tuple[str, str] | None = None) -> list[dict[str, str]]:
    """Hard gate for a multi-study merge: the contrast is estimable only if at least ONE dataset
    contains BOTH compared conditions.

    When the two groups are split across studies (no single dataset has both), study-of-origin and
    the biological difference are the same axis — perfectly aliased — and NO analysis (pooling with
    a `~ dataset + condition` covariate, batch correction, or meta-analysis) can separate them.
    Returns a FAIL in that case. Single-dataset frames return [] (no cross-study confounding). This
    is NOT a generalization of the batch len==1 test, which has a false-negative for designs like
    D1={A}, D2={B}, D3={A}.
    """
    if "dataset" not in df.columns or "condition" not in df.columns:
        return []
    datasets = _dataset_levels(df)
    if datasets.replace("", pd.NA).nunique(dropna=True) <= 1:
        return []  # a single study cannot be cross-study-confounded
    conds = df["condition"].astype(str).str.strip()
    if contrast and contrast[0] and contrast[1]:
        num, den = str(contrast[0]).strip(), str(contrast[1]).strip()
        # A contrast whose arms are not (yet) present in the sample sheet cannot be assessed — e.g.
        # a freshly fetched multi-study sheet where condition is still "unknown" while the DE tab
        # still holds the default treated/control. Do not emit a spurious confounding FAIL.
        present = {c for c in conds if c and c != "unknown"}
        if num not in present or den not in present:
            return []
    else:
        levels = [c for c in conds.unique() if c and c != "unknown"]
        if len(levels) != 2:
            return []  # cannot assess a hard two-group gate without a two-level contrast
        num, den = levels[0], levels[1]
    spans_both = any(
        {num, den} <= set(conds[datasets == ds]) for ds in datasets.unique() if ds
    )
    if spans_both:
        return []
    return [{"status": "FAIL", "message": (
        f"The two compared groups ('{num}' vs '{den}') are split across studies — no single "
        f"dataset contains both. Study-of-origin and the biological difference are then the same "
        f"axis (perfectly confounded), and no analysis — pooling with a study covariate, batch "
        f"correction, or meta-analysis — can separate them. Compare groups that both appear "
        f"within at least one study, or add samples so each group is present in more than one "
        f"study.")}]


def detect_unsafe_dataset_names(df: pd.DataFrame) -> list[dict[str, str]]:
    """The 'dataset' (study-of-origin) column names per-study output files and figure/table columns,
    so — like sample_id — it must be filename/column safe. A value with spaces or path characters
    breaks the meta-analysis per-study files and figures. FAIL on any value outside [A-Za-z0-9_.-]."""
    if "dataset" not in df.columns:
        return []
    # Only relevant for a genuine multi-study merge — that is when 'dataset' becomes a per-study
    # file/column token. On a single-study sheet the column is ignored downstream (mirrors the WSL
    # gate, which checks the name only after the >1-study early-out), so do not block there.
    if _dataset_levels(df).replace("", pd.NA).nunique(dropna=True) <= 1:
        return []
    vals = [v for v in df["dataset"].astype(str).str.strip().unique() if v]
    bad = [v for v in vals if not re.match(r"^[A-Za-z0-9_.-]+$", v)]
    if not bad:
        return []
    preview = ", ".join(bad[:5]) + (" ..." if len(bad) > 5 else "")
    return [{"status": "FAIL", "message": (
        f"Unsafe study-of-origin (dataset) name(s): {preview}. Use only letters, numbers, "
        f"underscore, dot, and hyphen (no spaces or slashes) — the dataset name labels the "
        f"per-study result files and the cross-study figure columns.")}]


def detect_multistudy_admissibility(df: pd.DataFrame,
                                    contrast: tuple[str, str] | None = None,
                                    min_reps: int = 2) -> list[dict[str, str]]:
    """Fail-fast for a multi-study meta-analysis: it needs at least TWO studies that each contain
    BOTH contrast arms with >= min_reps replicates. Warns (not a hard block — the joint DESeq2 may
    still run) when fewer than two studies are admissible, so the user is not surprised by an empty
    meta-analysis after a full run."""
    if "dataset" not in df.columns or "condition" not in df.columns:
        return []
    datasets = _dataset_levels(df)
    if datasets.replace("", pd.NA).nunique(dropna=True) <= 1:
        return []
    conds = df["condition"].astype(str).str.strip()
    if contrast and contrast[0] and contrast[1]:
        num, den = str(contrast[0]).strip(), str(contrast[1]).strip()
        present = {c for c in conds if c and c != "unknown"}
        if num not in present or den not in present:
            return []  # contrast arms not in the sheet yet — cannot assess
    else:
        levels = [c for c in conds.unique() if c and c != "unknown"]
        if len(levels) != 2:
            return []
        num, den = levels[0], levels[1]
    # If no single study contains BOTH arms, the contrast is study-confounded: detect_dataset_confounding
    # already emits a hard FAIL for that, so do not also raise a (redundant, contradictory) admissibility
    # WARNING here. This mirrors the WSL gate, which nests admissibility under the "spans a study" branch.
    if not any({num, den} <= set(conds[datasets == ds]) for ds in datasets.unique() if ds):
        return []
    admissible = 0
    for ds in datasets.unique():
        if not ds:
            continue
        sub = conds[datasets == ds]
        if (sub == num).sum() >= min_reps and (sub == den).sum() >= min_reps:
            admissible += 1
    if admissible >= 2:
        return []
    return [{"status": "WARNING", "message": (
        f"Only {admissible} study contains both '{num}' and '{den}' with at least {min_reps} "
        f"replicates each. A multi-study meta-analysis needs at least two such studies, so it will "
        f"not run (the joint analysis still runs); add replicates or another study to enable it.")}]
