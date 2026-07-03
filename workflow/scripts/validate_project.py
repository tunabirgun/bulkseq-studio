from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml


def check_design(config: dict, samples_path: Path) -> list[dict[str, str]]:
    """Fail fast when the DE design references a factor level that does not exist in the
    sample sheet. Without this the run only crashes at the DESeq2 step ("'ref' must be an
    existing level") after alignment and counting have already run for many minutes."""
    msgs: list[dict[str, str]] = []
    if (config.get("input") or {}).get("type") == "deseq2_results":
        return msgs  # results are uploaded; no DE model is fit, so the design is unused
    de = config.get("deseq2") or {}
    # Required (factor -> {levels}) from the reference level and every contrast.
    required: dict[str, set[str]] = {}
    ref = de.get("reference_level")
    if isinstance(ref, dict):
        for factor, level in ref.items():
            if level:
                required.setdefault(str(factor), set()).add(str(level))
    for con in de.get("contrasts") or []:
        if not isinstance(con, dict) or not con.get("factor"):
            continue
        for key in ("numerator", "denominator"):
            if con.get(key):
                required.setdefault(str(con["factor"]), set()).add(str(con[key]))
    if not required or not samples_path.exists():
        return msgs
    with samples_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows:
        return msgs
    cols = list(rows[0].keys())
    for factor, levels in required.items():
        if factor not in cols:
            msgs.append({"status": "FAIL", "message": (
                f"The differential-expression design references the column '{factor}', which is "
                f"not in the sample sheet (columns: {', '.join(cols)}). Set the contrast factor "
                f"to a column that exists.")})
            continue
        present = sorted({str(r.get(factor, "")).strip() for r in rows if str(r.get(factor, "")).strip()})
        for level in sorted(levels):
            if level not in present:
                msgs.append({"status": "FAIL", "message": (
                    f"The design uses '{level}' for '{factor}', but the sample sheet has no such "
                    f"value. Available {factor} values: {', '.join(present) or '(none)'}. Fix the "
                    f"reference level / contrast on Workflow Settings to match your sample "
                    f"conditions, then re-run.")})
    return msgs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    messages: list[dict[str, str]] = []
    config_path = Path(args.config)
    samples_path = Path(args.samples)
    payload: dict = {}
    if not config_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing config: {config_path}"})
    else:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for section in ("project", "input", "reference", "workflow", "resources"):
            if section not in payload:
                messages.append({"status": "FAIL", "message": f"Missing config section: {section}"})
        # Contamination screening needs a user-provided FastQ Screen config; warn if it is
        # enabled without one (the screen is skipped) or points at a missing file (it will fail).
        wf = payload.get("workflow") or {}
        if wf.get("contamination_screen"):
            conf = ((payload.get("contamination") or {}).get("conf") or "").strip()
            if not conf:
                messages.append({"status": "WARNING", "message": "Contamination screening is enabled but no FastQ Screen config (contamination.conf) is set; the screen will be skipped. Set a fastq_screen.conf under Advanced parameters to run it."})
            elif not Path(conf).exists():
                messages.append({"status": "WARNING", "message": f"FastQ Screen config not found: {conf}; the contamination screen will fail until the path is fixed or the screen is disabled."})
    if not samples_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing samples table: {samples_path}"})
    messages.extend(check_design(payload, samples_path))
    if not messages:
        messages.append({"status": "PASS", "message": "Project setup files are present."})
    write_payload(Path(args.out), "00_project_setup", messages)
    # Stop the run now on a fatal setup error (bad design, missing config/samples) with a clear
    # message, instead of letting it fail minutes later at alignment or DESeq2.
    fails = [m["message"] for m in messages if m["status"] == "FAIL"]
    if fails:
        for msg in fails:
            print(f"PROJECT SETUP ERROR: {msg}", file=sys.stderr)
        return 1
    return 0


def write_payload(path: Path, name: str, messages: list[dict[str, str]]) -> None:
    priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
    status = max((m["status"] for m in messages), key=lambda s: priority.get(s, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"check": name, "status": status, "messages": messages}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
