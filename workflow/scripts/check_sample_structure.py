from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


# This is a descriptive local-clustering screen, not an outlier test or a
# small-n hypothesis test. A condition is advisory when its median replicate
# distance is at least 50% greater than its closest between-condition distance.
LOCAL_CLUSTERING_RATIO = 1.5
NUMERIC_TOLERANCE = 1e-8


def _warning(message: str, *, reason: str, source: str) -> dict:
    return {
        "check": "22_sample_structure_qc",
        "status": "WARNING",
        "assessment": "NOT_ASSESSABLE",
        "messages": [{"status": "WARNING", "message": message}],
        "evidence": {"source": source, "reason": reason, "metric": "1 - Pearson correlation"},
    }


def _read_samples(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows or not rows[0] or "sample_id" not in rows[0] or "condition" not in rows[0]:
        raise ValueError("samples sheet must contain sample_id and condition columns")
    result: dict[str, str] = {}
    for row in rows:
        sample_id = (row.get("sample_id") or "").strip()
        condition = (row.get("condition") or "").strip()
        if not sample_id or not condition:
            raise ValueError("each sample row must have non-empty sample_id and condition")
        if sample_id in result:
            raise ValueError(f"duplicate sample_id in samples sheet: {sample_id}")
        result[sample_id] = condition
    return result


def _read_correlation(path: Path) -> tuple[list[str], dict[tuple[str, str], float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError("Pearson correlation export is empty or malformed")
    labels = [value.strip() for value in rows[0][1:]]
    if not all(labels) or len(set(labels)) != len(labels):
        raise ValueError("Pearson correlation header must contain unique non-empty sample IDs")
    if len(rows) != len(labels) + 1:
        raise ValueError("Pearson correlation export is not square")
    values: dict[tuple[str, str], float] = {}
    for row, expected_label in zip(rows[1:], labels, strict=True):
        if len(row) != len(labels) + 1 or row[0].strip() != expected_label:
            raise ValueError("Pearson correlation rows must match the header exactly")
        for label, text in zip(labels, row[1:], strict=True):
            try:
                value = float(text)
            except ValueError as exc:
                raise ValueError(f"non-numeric correlation for {expected_label}/{label}") from exc
            if not math.isfinite(value) or value < -1 - NUMERIC_TOLERANCE or value > 1 + NUMERIC_TOLERANCE:
                raise ValueError(f"invalid Pearson correlation for {expected_label}/{label}")
            values[(expected_label, label)] = value
    for first in labels:
        if abs(values[(first, first)] - 1.0) > NUMERIC_TOLERANCE:
            raise ValueError(f"Pearson correlation diagonal is not one for {first}")
        for second in labels:
            if abs(values[(first, second)] - values[(second, first)]) > NUMERIC_TOLERANCE:
                raise ValueError("Pearson correlation export is not symmetric")
    return labels, values


def _distance(values: dict[tuple[str, str], float], first: str, second: str) -> float:
    return max(0.0, 1.0 - values[(first, second)])


def evaluate(samples: dict[str, str], labels: list[str], values: dict[tuple[str, str], float], source: str) -> dict:
    if set(samples) != set(labels):
        return _warning(
            "Sample-structure QC WARNING: sample metadata and Pearson-correlation labels do not match; "
            "replicate clustering was not assessed.",
            reason="sample metadata/correlation label mismatch",
            source=source,
        )

    groups: dict[str, list[str]] = {}
    for sample_id, condition in samples.items():
        groups.setdefault(condition, []).append(sample_id)
    eligible = {condition: ids for condition, ids in groups.items() if len(ids) >= 2}
    if len(eligible) < 2:
        return _warning(
            "Sample-structure QC WARNING: fewer than two conditions have at least two samples; "
            "replicate clustering was not assessed.",
            reason="insufficient replicated conditions",
            source=source,
        )

    details: list[dict] = []
    flagged: list[dict] = []
    replicated_samples = [sample_id for ids in eligible.values() for sample_id in ids]
    for condition, ids in sorted(eligible.items()):
        within = [_distance(values, ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        between = [
            _distance(values, sample_id, other)
            for sample_id in ids
            for other in replicated_samples
            if samples[other] != condition
        ]
        if not within or not between:
            return _warning(
                "Sample-structure QC WARNING: replicate or between-condition distances were unavailable; "
                "replicate clustering was not assessed.",
                reason="missing within- or between-condition distances",
                source=source,
            )
        within_median = statistics.median(within)
        closest_between = min(between)
        ratio = math.inf if closest_between <= NUMERIC_TOLERANCE and within_median > NUMERIC_TOLERANCE else (
            within_median / max(closest_between, NUMERIC_TOLERANCE)
        )
        record = {
            "condition": condition,
            "n": len(ids),
            "within_pair_count": len(within),
            "within_median_distance": within_median,
            "closest_between_distance": closest_between,
            "local_clustering_ratio": ratio if math.isfinite(ratio) else None,
            "advisory": ratio >= LOCAL_CLUSTERING_RATIO,
        }
        details.append(record)
        if record["advisory"]:
            flagged.append(record)

    threshold_text = f"{LOCAL_CLUSTERING_RATIO:g}x"
    evidence = {
        "source": source,
        "metric": "1 - Pearson correlation",
        "rule": "condition median within-condition distance / closest between-condition distance",
        "warning_threshold": LOCAL_CLUSTERING_RATIO,
        "groups": details,
    }
    if flagged:
        summary = "; ".join(
            f"{item['condition']} (within median {item['within_median_distance']:.4g}, "
            f"closest between {item['closest_between_distance']:.4g}, ratio {item['local_clustering_ratio']:.2f}x)"
            for item in flagged
        )
        message = (
            f"Sample-structure QC WARNING: expected replicate clustering is weaker than local "
            f"between-condition separation for {summary}; the {threshold_text} advisory threshold was met. "
            "This is descriptive evidence, not an outlier call and not a small-n hypothesis test; interpret "
            "differential-expression and enrichment results cautiously and investigate technical or biological heterogeneity."
        )
        return {"check": "22_sample_structure_qc", "status": "WARNING", "assessment": "ADVISORY", "messages": [{"status": "WARNING", "message": message}], "evidence": evidence}

    summary = "; ".join(
        f"{item['condition']} (ratio {item['local_clustering_ratio']:.2f}x)" for item in details
    )
    return {
        "check": "22_sample_structure_qc",
        "status": "PASS",
        "assessment": "ASSESSED",
        "messages": [{"status": "PASS", "message": (
            "Sample-structure QC: median within-condition Pearson-correlation distances remain below "
            f"the local {threshold_text} advisory threshold ({summary}).")}],
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess replicate clustering from the exported Pearson matrix.")
    parser.add_argument("--correlations", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = str(Path(args.correlations))
    try:
        payload = evaluate(_read_samples(Path(args.samples)), *_read_correlation(Path(args.correlations)), source)
    except (OSError, UnicodeError, ValueError) as exc:
        payload = _warning(
            f"Sample-structure QC WARNING: {exc}; replicate clustering was not assessed.",
            reason=str(exc),
            source=source,
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
