from __future__ import annotations

import argparse
from pathlib import Path

from check_contract import read_check


SUCCESS_MARKER = "Input check passed.\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    marker = Path(args.out)
    if marker.exists() and marker.read_text(encoding="utf-8") != SUCCESS_MARKER:
        raise RuntimeError(f"Input gate marker has unexpected contents: {marker}")
    check = read_check(args.check)
    if not check["valid"] or check["status"] not in {"PASS", "WARNING"}:
        marker.unlink(missing_ok=True)
        print(f"Input gate refused {check['check']}: {check['status']}")
        for message in check["messages"]:
            print(message["message"])
        return 1
    marker.write_text(SUCCESS_MARKER, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
