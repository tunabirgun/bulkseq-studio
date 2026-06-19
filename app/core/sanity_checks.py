from __future__ import annotations

import json
from pathlib import Path


def write_check(project_root: Path, name: str, messages: list[dict[str, str]]) -> Path:
    path = project_root / "checks" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"check": name, "status": aggregate_status(messages), "messages": messages}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_sanity_text(project_root)
    return path


def aggregate_status(messages: list[dict[str, str]]) -> str:
    priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
    if not messages:
        return "PASS"
    return max((m.get("status", "PASS") for m in messages), key=lambda s: priority.get(s, 0))


def write_sanity_text(project_root: Path) -> Path:
    lines = ["RNA-seq Sanity Checks", "====================", ""]
    for file in sorted((project_root / "checks").glob("*.json")):
        payload = json.loads(file.read_text(encoding="utf-8"))
        lines.append(f"{payload.get('check')}: {payload.get('status')}")
        for message in payload.get("messages", []):
            lines.append(f"  - {message.get('status')}: {message.get('message')}")
        lines.append("")
    out = project_root / "checks" / "sanity_checks.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    report = project_root / "results" / "reports" / "sanity_checks.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return out
