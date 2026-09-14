"""Realized-strandedness rendering shared by the run summary and the HTML report.

Both reports must describe the same record in the same words; a per-sample (mixed) run that
reads as uniform in one of them is a provenance defect, not a cosmetic one.
"""

from __future__ import annotations

STRANDEDNESS_LABELS = {0: "unstranded", 1: "forward", 2: "reverse"}


def realized_strandedness_text(payload: dict) -> str | None:
    """Render only a complete realized record; never substitute the configured value."""
    provenance = payload.get("strandedness")
    realized = provenance.get("realized") if isinstance(provenance, dict) else None
    if not isinstance(realized, dict):
        return None
    code = realized.get("code")
    label = realized.get("label")
    path = realized.get("path")
    if (isinstance(code, bool) or code not in STRANDEDNESS_LABELS
            or label != STRANDEDNESS_LABELS[code] or not isinstance(path, str)
            or not path.strip()):
        return None
    per_sample = realized.get("per_sample")
    if isinstance(per_sample, dict) and per_sample and not realized.get("uniform", True):
        detail = ", ".join(f"{sid}={STRANDEDNESS_LABELS.get(c, c)}"
                           for sid, c in sorted(per_sample.items()))
        return f"mixed (realized per-sample from {path}: {detail})"
    return f"{label} ({code}; realized from {path})"
