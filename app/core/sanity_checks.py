from __future__ import annotations

from pathlib import Path

from workflow.scripts.check_contract import PRIORITY, render_summary, summarize_checks


def write_check(project_root: Path, name: str, messages: list[dict[str, str]]) -> Path:
    path = project_root / "checks" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"check": name, "status": aggregate_status(messages), "messages": messages}
    import json
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_sanity_text(project_root)
    return path


def aggregate_status(messages: list[dict[str, str]]) -> str:
    if not messages:
        return "FAIL"
    statuses = [m.get("status") for m in messages]
    if any(status not in PRIORITY for status in statuses):
        return "FAIL"
    return max(statuses, key=PRIORITY.__getitem__)


def write_sanity_text(project_root: Path) -> Path:
    summary = summarize_checks(sorted((project_root / "checks").glob("*.json")), expected=False)
    out = project_root / "checks" / "sanity_checks.txt"
    out.write_text(render_summary(summary), encoding="utf-8")
    report = project_root / "results" / "reports" / "sanity_checks.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return out
