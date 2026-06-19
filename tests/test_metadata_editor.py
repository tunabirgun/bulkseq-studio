from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.metadata_editor import MetadataTable  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_load_to_dataframe_roundtrip() -> None:
    _app()
    table = MetadataTable()
    df = pd.DataFrame(
        [
            {"sample_id": "s1", "condition": "control", "layout": "single", "fastq_1": "a.fq"},
            {"sample_id": "s2", "condition": "treated", "layout": "single", "fastq_1": "b.fq"},
        ]
    )
    table.load_dataframe(df)
    out = table.to_dataframe()
    assert out["sample_id"].tolist() == ["s1", "s2"]
    assert out["condition"].tolist() == ["control", "treated"]


def test_add_and_rename_column() -> None:
    _app()
    table = MetadataTable()
    table.load_dataframe(pd.DataFrame([{"sample_id": "s1"}]))
    table.add_column("batch")
    assert "batch" in table.column_names()
    idx = table.column_names().index("batch")
    table.rename_column(idx, "run")
    assert "run" in table.column_names()
    assert "batch" not in table.column_names()


def test_assign_condition_to_selected() -> None:
    _app()
    table = MetadataTable()
    table.load_dataframe(
        pd.DataFrame(
            [
                {"sample_id": "s1", "condition": "x"},
                {"sample_id": "s2", "condition": "x"},
            ]
        )
    )
    table.selectRow(1)
    table.assign_condition("treated")
    assert table.to_dataframe()["condition"].tolist() == ["x", "treated"]
