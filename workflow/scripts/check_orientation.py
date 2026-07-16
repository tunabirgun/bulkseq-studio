from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}

# Case-like (disease/perturbed) vs control-like level-name patterns. Matched case-insensitively
# on the reference/denominator and numerator level names to catch an inverted DE contrast.
# Negative lookbehind excludes the "un-"/"ab-" negated opposites (untreated/uninfected/
# unstimulated are control; abnormal is case) while still matching underscore-joined names like
# 'dex_treated' or 'normal_tissue' -- which a \b anchor would miss ('_' is a word char).
CASE_RE = re.compile(
    r"cancer|tumou?r|carcinoma|disease|diseased|(?<!un)treated|treatment|(?<!un)infected|mutant|"
    r"knock ?out|ko\b|(?<!un)stimulated|patient|case|tumor",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(
    r"control|healthy|(?<!ab)normal|wild ?type|wt\b|untreated|mock|vehicle|baseline|ctrl",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    deseq2 = cfg.get("deseq2", {}) or {}
    contrasts = deseq2.get("contrasts") or []
    contrast = contrasts[0] if contrasts else {}
    factor = contrast.get("factor", "")
    numerator = str(contrast.get("numerator", "") or "")
    denominator = str(contrast.get("denominator", "") or "")

    # reference_level is a factor -> level map; the reference for the contrast factor is the
    # DESeq2 baseline. The denominator is the baseline of the contrast itself.
    reference_level = deseq2.get("reference_level", {}) or {}
    ref_for_factor = str(reference_level.get(factor, "") or "") if factor else ""

    # Baseline = the reference_level entry when set, else the contrast denominator.
    baseline = ref_for_factor or denominator

    messages: list[dict[str, str]] = []
    inverted = bool(baseline) and bool(numerator) and CASE_RE.search(baseline) and CONTROL_RE.search(numerator)
    if inverted:
        messages.append({
            "status": "REVIEW_REQUIRED",
            "message": (
                f"Contrast baseline '{baseline}' looks case-like and numerator '{numerator}' looks "
                "control-like: positive log2FC = up in the CONTROL group, so the up/down gene lists "
                "and the enrichment up/down ontologies are inverted vs the usual case-vs-control "
                "convention; consider setting reference_level to the control level."
            ),
        })
    else:
        messages.append({
            "status": "PASS",
            "message": (
                f"Contrast orientation looks conventional (baseline '{baseline or 'n/a'}' vs "
                f"numerator '{numerator or 'n/a'}'); positive log2FC = up in the numerator group."
            ),
        })

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "PASS"
    payload = {"check": "19_orientation_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
