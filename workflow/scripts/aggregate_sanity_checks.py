from __future__ import annotations

import argparse
from pathlib import Path

from check_contract import render_summary, summarize_checks

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", nargs="+", required=True, help="explicit list of check JSON files")
    parser.add_argument("--out", required=True)
    parser.add_argument("--strict", action="store_true", help="exit nonzero unless every expected check passes")
    parser.add_argument("--expected", action="store_true", help="checks list is the complete expected set")
    args = parser.parse_args()
    summary = summarize_checks(sorted(Path(p) for p in args.checks),
                               expected=args.expected or args.strict)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_summary(summary), encoding="utf-8")
    report = Path("results/reports/sanity_checks.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return int(args.strict and (not summary["complete"] or summary["status"] != "PASS"))


if __name__ == "__main__":
    raise SystemExit(main())
