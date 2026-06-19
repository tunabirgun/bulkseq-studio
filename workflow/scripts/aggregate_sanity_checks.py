from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    lines = ["RNA-seq Sanity Checks", "====================", ""]
    for path in sorted(Path(args.checks).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        lines.append(f"{payload.get('check', path.stem)}: {payload.get('status')}")
        for message in payload.get("messages", []):
            lines.append(f"  - {message.get('status')}: {message.get('message')}")
        lines.append("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    report = Path("results/reports/sanity_checks.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
