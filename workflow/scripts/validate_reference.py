from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    ref = cfg.get("reference", {})
    messages: list[dict[str, str]] = []
    if ref.get("mode") == "unset":
        messages.append({"status": "REVIEW_REQUIRED", "message": "No reference selected. Dry-run scaffolding can continue, but biological execution requires a locked reference."})
    for key in ("genome_fasta", "annotation_file"):
        value = ref.get(key)
        if value and not Path(value).exists():
            messages.append({"status": "FAIL", "message": f"Configured {key} does not exist: {value}"})
    if not messages:
        messages.append({"status": "PASS", "message": "Reference configuration is structurally usable."})
    write_payload(Path(args.out), "05_reference_validation", messages)
    return 0


def write_payload(path: Path, name: str, messages: list[dict[str, str]]) -> None:
    priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
    status = max((m["status"] for m in messages), key=lambda s: priority.get(s, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"check": name, "status": status, "messages": messages}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
