from __future__ import annotations

import json
from pathlib import Path


PRIORITY = {"PASS": 0, "WARNING": 1, "REVIEW_REQUIRED": 2, "FAIL": 3}


def _reject_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def read_check(path: str | Path) -> dict:
    """Read one scientific check without discarding missing or malformed evidence."""
    path = Path(path)

    def invalid(reason):
        return {"check": path.stem, "status": "FAIL", "valid": False,
                "messages": [{"status": "FAIL", "message": reason}]}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        return invalid(f"Check evidence unavailable or unreadable: {exc}")
    if not isinstance(payload, dict):
        return invalid("Check file must contain a JSON object.")
    if payload.get("check") != path.stem:
        return invalid("Check identifier is missing or does not match its file name.")
    if not isinstance(payload.get("status"), str) or payload["status"] not in PRIORITY:
        return invalid("Check status is missing or unknown.")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return invalid("Check messages must be a nonempty list.")
    for message in messages:
        if (not isinstance(message, dict) or not isinstance(message.get("status"), str)
                or message["status"] not in PRIORITY
                or not isinstance(message.get("message"), str) or not message["message"].strip()):
            return invalid("Check contains a malformed or unknown-status message.")
    canonical = dict(payload)
    canonical["status"] = max([payload["status"], *(m["status"] for m in messages)],
                              key=PRIORITY.__getitem__)
    canonical["valid"] = True
    return canonical


def summarize_checks(paths, *, expected: bool = True) -> dict:
    checks = [read_check(path) for path in paths]
    return {"status": max((check["status"] for check in checks),
                          key=PRIORITY.__getitem__, default="FAIL"),
            "checks": checks, "complete": expected and bool(checks)
            and all(check["valid"] for check in checks),
            "expected_count": len(checks) if expected else None,
            "scope": "expected" if expected else "recorded"}


def render_summary(summary: dict) -> str:
    title = "BulkSeq Studio validation checks"
    lines = [title, "=" * len(title), f"Overall: {summary['status']}", ""]
    if summary["scope"] == "recorded":
        lines.extend(["Coverage: recorded checks only; expected-check completeness was not assessed.", ""])
    for check in summary["checks"]:
        lines.append(f"{check['check']}: {check['status']}")
        lines.extend(f"  - {m['status']}: {m['message']}" for m in check["messages"])
        lines.append("")
    return "\n".join(lines)
