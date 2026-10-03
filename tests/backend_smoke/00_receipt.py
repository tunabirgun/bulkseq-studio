from __future__ import annotations

import json
import os
from pathlib import Path
import sys


allowed = {"prepare", "salmon", "ribodetector", "main", "meta", "custom_old",
           "custom_new", "gsva", "custom_report", "string", "ppi"}
if len(sys.argv) != 2 or sys.argv[1] not in allowed:
    raise SystemExit("Unknown completed smoke phase")
phase = sys.argv[1]
root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
receipts = root / "receipts"
receipts.mkdir(parents=True, exist_ok=True)
with (receipts / f"{phase}.json").open("x", encoding="utf-8") as handle:
    json.dump({"phase": phase, "status": "PASS"}, handle)
    handle.write("\n")
print(f"Recorded completed phase: {phase}")
