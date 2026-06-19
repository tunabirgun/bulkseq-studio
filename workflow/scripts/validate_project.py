from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    messages: list[dict[str, str]] = []
    config_path = Path(args.config)
    samples_path = Path(args.samples)
    if not config_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing config: {config_path}"})
    else:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for section in ("project", "input", "reference", "workflow", "resources"):
            if section not in payload:
                messages.append({"status": "FAIL", "message": f"Missing config section: {section}"})
    if not samples_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing samples table: {samples_path}"})
    if not messages:
        messages.append({"status": "PASS", "message": "Project setup files are present."})
    write_payload(Path(args.out), "00_project_setup", messages)
    return 0


def write_payload(path: Path, name: str, messages: list[dict[str, str]]) -> None:
    priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
    status = max((m["status"] for m in messages), key=lambda s: priority.get(s, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"check": name, "status": status, "messages": messages}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
