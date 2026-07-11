from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd


REQUIRED = ["sample_id", "condition", "layout", "fastq_1"]
PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def _multistudy_gates(df: pd.DataFrame, num: str, den: str) -> list[dict[str, str]]:
    """Standalone mirror of app.core.metadata's multi-study gates (the pipeline runs in WSL where
    the app package is not importable). Fires only when a 'dataset' column has >1 study."""
    msgs: list[dict[str, str]] = []
    if "dataset" not in df.columns:
        return msgs
    ds = df["dataset"].astype(str).str.strip()
    if ds.replace("", pd.NA).nunique(dropna=True) <= 1:
        return msgs
    # Study-of-origin names label per-study files + figure columns, so they must be filename-safe.
    bad_ds = [v for v in ds.unique() if v and not re.match(r"^[A-Za-z0-9_.-]+$", v)]
    if bad_ds:
        msgs.append({"status": "FAIL", "message": (
            "Unsafe study-of-origin (dataset) name(s): " + ", ".join(bad_ds[:5])
            + ". Use only letters, numbers, underscore, dot, and hyphen (no spaces or slashes).")})
    # Same-organism requirement (shared gene-id namespace), case-insensitive.
    if "organism" in df.columns:
        orgs = {}
        for o in df["organism"].astype(str).str.strip():
            if o and o.lower() not in ("unknown", "nan", "na"):
                orgs.setdefault(o.casefold(), o)
        if len(orgs) > 1:
            msgs.append({"status": "FAIL", "message": (
                f"The merged studies use different organisms ({', '.join(sorted(orgs.values()))}). "
                "A multi-study analysis must combine studies of the SAME organism with a shared "
                "gene-id namespace.")})
    # Confounding + admissibility: only assess when the contrast arms are actually present.
    if "condition" in df.columns and num and den:
        cond = df["condition"].astype(str).str.strip()
        present = {c for c in cond if c and c != "unknown"}
        if num in present and den in present:
            levels = [set(cond[ds == d]) for d in ds[ds != ""].unique()]
            spans = any({num, den} <= lv for lv in levels)
            if not spans:
                msgs.append({"status": "FAIL", "message": (
                    f"The contrast '{num}' vs '{den}' is split across studies: no single dataset "
                    "contains both arms, so study-of-origin is perfectly confounded with the "
                    "biological difference. No analysis can separate them — include both conditions "
                    "within at least one study.")})
            else:
                admissible = sum((cond[ds == d] == num).sum() >= 2 and (cond[ds == d] == den).sum() >= 2
                                 for d in ds[ds != ""].unique())
                if admissible < 2:
                    msgs.append({"status": "WARNING", "message": (
                        f"Only {admissible} study contains both '{num}' and '{den}' with >=2 replicates; "
                        "a multi-study meta-analysis needs at least two, so it will not run (the joint "
                        "analysis still runs).")})
    return msgs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--numerator", default="")
    parser.add_argument("--denominator", default="")
    args = parser.parse_args()
    df = pd.read_csv(args.samples, sep="\t", dtype=str).fillna("")
    messages: list[dict[str, str]] = []

    missing = [col for col in REQUIRED if col not in df.columns]
    if missing:
        messages.append({"status": "FAIL", "message": f"Missing required columns: {', '.join(missing)}"})

    ids = list(df.get("sample_id", []))
    duplicates = [sid for sid, count in Counter(ids).items() if count > 1]
    if duplicates:
        messages.append({"status": "FAIL", "message": f"Duplicate sample IDs: {', '.join(duplicates)}"})
    unsafe = [sid for sid in ids if not re.match(r"^[A-Za-z0-9_.-]+$", str(sid))]
    if unsafe:
        messages.append({"status": "FAIL", "message": f"Unsafe sample IDs: {', '.join(unsafe)}"})

    if "condition" in df.columns:
        counts = df.groupby("condition")["sample_id"].count().to_dict()
        for condition, count in counts.items():
            if condition in ("", "unknown"):
                continue
            if count < 2:
                messages.append({"status": "WARNING", "message": f"Condition '{condition}' has fewer than two replicates."})

    messages += _multistudy_gates(df, args.numerator.strip(), args.denominator.strip())

    if not messages:
        messages.append({"status": "PASS", "message": "Metadata passed input validation."})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0))
    payload = {"check": "01_input_validation", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
