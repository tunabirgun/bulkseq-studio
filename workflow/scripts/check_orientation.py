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


def _text(value: object) -> str:
    return str(value or "").strip()


def orientation_messages(cfg: dict) -> list[dict[str, str]]:
    """Describe the sign convention used by the active analysis route.

    Imported results are not produced by the local DE model.  Their orientation must therefore
    come exclusively from ``input.deseq2_results_direction``; old contrast/reference settings
    can remain in a converted project and must have no effect on either the verdict or wording.
    """
    inp = cfg.get("input", {}) or {}
    if inp.get("type") == "deseq2_results":
        direction = inp.get("deseq2_results_direction") or {}
        if not isinstance(direction, dict):
            direction = {}
        numerator = _text(direction.get("numerator"))
        denominator = _text(direction.get("denominator"))
        if not numerator or not denominator or numerator.casefold() == denominator.casefold():
            return [{
                "status": "FAIL",
                "message": (
                    "The imported results do not have a valid confirmed direction: record two "
                    "distinct source comparison labels so positive log2FC has an unambiguous "
                    "meaning. Local differential-expression settings do not define imported signs."
                ),
            }]
        if direction.get("confirmed") is not True:
            return [{
                "status": "FAIL",
                "message": (
                    f"The imported source direction ({numerator} relative to {denominator}) has not "
                    "been confirmed. Confirm it against the source results before interpreting "
                    "up- and down-regulated genes."
                ),
            }]

        inverted = bool(CASE_RE.search(denominator)) and bool(CONTROL_RE.search(numerator))
        if inverted:
            return [{
                "status": "REVIEW_REQUIRED",
                "message": (
                    f"The imported source comparison records positive log2FC as higher in "
                    f"'{numerator}' than '{denominator}', which appears control-over-case. Verify "
                    "that this matches the project copy's source analysis before interpreting "
                    "up/down gene sets; local contrast settings do not alter the supplied signs."
                ),
            }]
        return [{
            "status": "PASS",
            "message": (
                f"Imported-results orientation confirmed: positive log2FC means higher in "
                f"'{numerator}' than '{denominator}'. The supplied signs are preserved."
            ),
        }]

    deseq2 = cfg.get("deseq2", {}) or {}
    contrasts = deseq2.get("contrasts") or []
    contrast = contrasts[0] if contrasts else {}
    factor = contrast.get("factor", "")
    numerator = _text(contrast.get("numerator"))
    denominator = _text(contrast.get("denominator"))

    # reference_level is a factor -> level map; the reference for the contrast factor is the
    # DESeq2 baseline. The denominator is the baseline of the contrast itself.
    reference_level = deseq2.get("reference_level", {}) or {}
    ref_for_factor = _text(reference_level.get(factor)) if factor else ""
    baseline = ref_for_factor or denominator

    inverted = bool(baseline) and bool(numerator) and CASE_RE.search(baseline) and CONTROL_RE.search(numerator)
    if inverted:
        return [{
            "status": "REVIEW_REQUIRED",
            "message": (
                f"Contrast baseline '{baseline}' looks case-like and numerator '{numerator}' looks "
                "control-like: positive log2FC = up in the CONTROL group, so the up/down gene lists "
                "and the enrichment up/down ontologies are inverted vs the usual case-vs-control "
                "convention; consider setting reference_level to the control level."
            ),
        }]
    return [{
        "status": "PASS",
        "message": (
            f"Contrast orientation looks conventional (baseline '{baseline or 'n/a'}' vs "
            f"numerator '{numerator or 'n/a'}'); positive log2FC = up in the numerator group."
        ),
    }]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    messages = orientation_messages(cfg)

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "PASS"
    payload = {"check": "19_orientation_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
