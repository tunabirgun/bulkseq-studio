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


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def aggregate_status(messages: list[dict[str, str]]) -> str:
    if not messages:
        return "PASS"
    return max((m.get("status", "PASS") for m in messages), key=lambda s: PRIORITY.get(s, 0))


def write_sanity_text(project_root: Path) -> Path:
    # Same text as workflow/scripts/aggregate_sanity_checks.py for the same check files,
    # including the Overall line and the FAIL entry for an unreadable check.
    title = "BulkSeq Studio validation checks"
    lines = [title, "=" * len(title), ""]
    worst = "PASS"
    for file in sorted((project_root / "checks").glob("*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            payload = {"check": file.stem, "status": "FAIL",
                       "messages": [{"status": "FAIL", "message": f"unreadable check file: {exc}"}]}
        if not isinstance(payload, dict):
            payload = {"check": file.stem, "status": "FAIL",
                       "messages": [{"status": "FAIL", "message": "check file is not a JSON object"}]}
        messages = [m for m in payload.get("messages", []) if isinstance(m, dict)]
        status = payload.get("status") or aggregate_status(messages)
        if PRIORITY.get(status, 0) > PRIORITY.get(worst, 0):
            worst = status
        lines.append(f"{payload.get('check', file.stem)}: {status}")
        for message in messages:
            lines.append(f"  - {message.get('status')}: {message.get('message')}")
        lines.append("")
    lines.insert(2, f"Overall: {worst}")
    out = project_root / "checks" / "sanity_checks.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    report = project_root / "results" / "reports" / "sanity_checks.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return out
