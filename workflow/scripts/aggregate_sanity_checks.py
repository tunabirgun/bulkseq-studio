from __future__ import annotations

import argparse
import json
from pathlib import Path


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def overall_status(messages: list[dict], explicit: str | None) -> str:
    if explicit:
        return explicit
    statuses = [m.get("status", "PASS") for m in messages] or ["PASS"]
    return max(statuses, key=lambda s: PRIORITY.get(s, 0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", nargs="+", required=True, help="explicit list of check JSON files")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    lines = ["RNA-seq Sanity Checks", "====================", ""]
    worst = "PASS"
    for path in sorted(Path(p) for p in args.checks):
        if not path.exists():
            lines.append(f"{path.stem}: MISSING")
            lines.append("")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        messages = payload.get("messages", [])
        status = overall_status(messages, payload.get("status"))
        if PRIORITY.get(status, 0) > PRIORITY.get(worst, 0):
            worst = status
        lines.append(f"{payload.get('check', path.stem)}: {status}")
        for message in messages:
            lines.append(f"  - {message.get('status')}: {message.get('message')}")
        lines.append("")
    lines.insert(2, f"Overall: {worst}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    report = Path("results/reports/sanity_checks.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
