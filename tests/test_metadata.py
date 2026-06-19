from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import validate_metadata


BASE = Path("manual_test_metadata")


def test_detect_paired_fastq() -> None:
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    r1 = base / "sampleA_R1.fastq.gz"
    r2 = base / "sampleA_R2.fastq.gz"
    r1.write_text("", encoding="utf-8")
    r2.write_text("", encoding="utf-8")
    rows = detect_fastq_inputs([r1, r2])
    assert rows[0]["layout"] == "paired"
    assert rows[0]["fastq_2"] == str(r2)


def test_metadata_duplicate_fails() -> None:
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    fastq = base / "a.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    df = pd.DataFrame(
        [
            {"sample_id": "s1", "condition": "control", "layout": "single", "fastq_1": str(fastq)},
            {"sample_id": "s1", "condition": "treated", "layout": "single", "fastq_1": str(fastq)},
        ]
    )
    messages = validate_metadata(df)
    assert any(m["status"] == "FAIL" and "Duplicate" in m["message"] for m in messages)
