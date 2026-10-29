"""Validate the cached, public STRING teaching snapshot."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs_src" / "reproduction" / "05_string_snapshot.py"
SNAPSHOT = ROOT / "docs_src" / "network-example.json"


def test_string_snapshot_passes_its_contract() -> None:
    subprocess.run([sys.executable, str(SCRIPT), "--validate", str(SNAPSHOT)], check=True)


def test_string_snapshot_rejects_an_invalid_score(tmp_path) -> None:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data["edges"][0]["score"] = 1001
    broken = tmp_path / "broken-network.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([sys.executable, str(SCRIPT), "--validate", str(broken)], check=True)


def test_string_snapshot_rejects_swapped_node_labels(tmp_path) -> None:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data["nodes"][0]["label"], data["nodes"][1]["label"] = data["nodes"][1]["label"], data["nodes"][0]["label"]
    broken = tmp_path / "swapped-network.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([sys.executable, str(SCRIPT), "--validate", str(broken)], check=True)
