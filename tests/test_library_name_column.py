from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pandas as pd
import pytest
import yaml
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from app.constants import DESCRIPTIVE_METADATA_COLUMNS, REQUIRED_METADATA_COLUMNS
from app.core.config_models import default_config
from app.core.sra_metadata import metadata_to_samples
from app.ui.main_window import MainWindow
from app.ui.metadata_editor import MetadataTable

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "app" / "ui" / "main_window.py"

LEGACY_SHEET = (
    "sample_id\tcondition\tlayout\tfastq_1\n"
    "S1\tcontrol\tsingle\treads/S1.fastq.gz\n"
    "S2\ttreated\tsingle\treads/S2.fastq.gz\n"
)
# Two rows deliberately share a library name and a third leaves it blank: the column is a
# free-text label, not a key, so neither is an error.
NAMED_SHEET = (
    "sample_id\tlibrary_name\tcondition\tlayout\tfastq_1\n"
    "S1\tLiver pool A\tcontrol\tsingle\treads/S1.fastq.gz\n"
    "S2\tLiver pool A\ttreated\tsingle\treads/S2.fastq.gz\n"
    "S3\t\ttreated\tsingle\treads/S3.fastq.gz\n"
)


def _window() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    return window


def _project(root: Path, sheet: str) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    config = default_config(root.name, root)
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    (root / "config" / "samples.tsv").write_text(sheet, encoding="utf-8")
    return root


def _samples(root: Path) -> Path:
    return root / "config" / "samples.tsv"


def _header(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split("\t")


@pytest.fixture
def window():
    win = _window()
    yield win
    win.close()


def test_library_name_loads_directly_after_sample_id(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "named", NAMED_SHEET)
    window._load_project(root)
    assert window.metadata_table.column_names()[:2] == ["sample_id", "library_name"]
    assert window.metadata_table.to_dataframe()["library_name"].tolist() == [
        "Liver pool A", "Liver pool A", ""]


def test_an_imported_archive_library_name_reaches_the_table_verbatim(window, tmp_path: Path) -> None:
    """The accession importer supplies the archive's own library_name; the table shows what
    arrived, blank included, and never substitutes the sample title."""
    meta = pd.DataFrame([
        {"run_accession": "SRR1", "library_layout": "SINGLE", "fastq_ftp": "ftp/SRR1.fastq.gz",
         "fastq_md5": "aaa", "fastq_bytes": "10", "library_name": "Liver pool A",
         "sample_title": "liver replicate 1", "scientific_name": "Homo sapiens"},
        {"run_accession": "SRR2", "library_layout": "SINGLE", "fastq_ftp": "ftp/SRR2.fastq.gz",
         "fastq_md5": "bbb", "fastq_bytes": "10", "library_name": "",
         "sample_title": "liver replicate 2", "scientific_name": "Homo sapiens"},
    ])
    samples = metadata_to_samples(meta)
    window.metadata_table.load_dataframe(samples)
    assert window.metadata_table.column_names()[:2] == ["sample_id", "library_name"]
    loaded = window.metadata_table.to_dataframe()
    assert loaded["library_name"].tolist() == ["Liver pool A", ""]
    assert loaded["library_name"].tolist() != loaded.get("sample_title", pd.Series(["x", "y"])).tolist()


def test_library_name_survives_save_and_reload_without_loss_or_reordering(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "named", NAMED_SHEET)
    path = _samples(root)
    before = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    window._load_project(root)
    window._save_metadata()
    after = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    pd.testing.assert_frame_equal(before, after)
    window._load_project(root)
    reloaded = window.metadata_table.to_dataframe()
    assert list(reloaded.columns) == list(before.columns)
    assert reloaded["library_name"].tolist() == before["library_name"].tolist()


def test_legacy_sheet_opens_without_gaining_a_library_name_column(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "legacy-open", LEGACY_SHEET)
    window._load_project(root)
    assert window.metadata_table.column_names() == LEGACY_SHEET.splitlines()[0].split("\t")


def test_legacy_sheet_without_library_name_saves_byte_identically(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "legacy", LEGACY_SHEET)
    path = _samples(root)
    before = path.read_bytes()
    window._load_project(root)
    window._save_metadata()
    assert path.read_bytes() == before


def test_blank_library_name_column_is_not_written_into_a_legacy_sheet(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "legacy-blank", LEGACY_SHEET)
    path = _samples(root)
    before = path.read_bytes()
    window._load_project(root)
    window.metadata_table.add_column("library_name")
    assert window.metadata_table.column_names()[:2] == ["sample_id", "library_name"]
    window._save_metadata()
    assert path.read_bytes() == before


def test_a_typed_library_name_persists_to_the_file(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "typed", LEGACY_SHEET)
    path = _samples(root)
    window._load_project(root)
    window.metadata_table.add_column("library_name")
    window.metadata_table.setItem(0, 1, QTableWidgetItem("Liver pool A"))
    window._save_metadata()
    saved = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    assert _header(path)[:2] == ["sample_id", "library_name"]
    assert saved["library_name"].tolist() == ["Liver pool A", ""]
    assert saved["sample_id"].tolist() == ["S1", "S2"]


def test_duplicate_library_names_are_accepted_unchanged(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "duplicates", NAMED_SHEET)
    path = _samples(root)
    window._load_project(root)
    window._validate_metadata()
    assert "library_name" not in window.metadata_messages.toPlainText()
    window._save_metadata()
    saved = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    assert saved["library_name"].tolist() == ["Liver pool A", "Liver pool A", ""]


def test_ordinary_added_columns_still_append(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "append", LEGACY_SHEET)
    window._load_project(root)
    window.metadata_table.add_column("batch")
    assert window.metadata_table.column_names()[-1] == "batch"


def test_new_project_header_carries_library_name_after_sample_id() -> None:
    _window()  # a QApplication must exist before a QTableWidget is constructed
    columns = MetadataTable().default_columns()
    assert columns.index("library_name") == columns.index("sample_id") + 1


def test_design_helper_never_offers_a_descriptive_column(window, tmp_path: Path) -> None:
    root = _project(tmp_path / "design", NAMED_SHEET)
    window._load_project(root)
    for extra in ("sample_title", "batch", "read_count", "platform"):
        window.metadata_table.add_column(extra)
    candidates = window._design_covariate_candidates()
    assert candidates == ["batch"]
    assert not set(candidates) & set(DESCRIPTIVE_METADATA_COLUMNS)


def test_imported_results_sheet_keeps_exactly_the_required_columns() -> None:
    """The results-only route writes a header-only sheet that validate_project.py requires to be
    exactly the four required names; a fifth would fail tests/test_validate_design.py."""
    tree = ast.parse(MAIN_WINDOW.read_text(encoding="utf-8"))
    headers = [
        [e.value for e in kw.value.elts]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "DataFrame"
        for kw in node.keywords
        if kw.arg == "columns" and isinstance(kw.value, ast.List)
        and all(isinstance(e, ast.Constant) for e in kw.value.elts)
    ]
    sheets = [h for h in headers if "sample_id" in h]
    assert len(sheets) == 1, f"expected one literal sample-sheet header, found {sheets}"
    assert sheets[0] == REQUIRED_METADATA_COLUMNS


def test_exported_sheet_drops_an_all_blank_library_name(tmp_path):
    import pandas as pd

    from app.core.metadata import export_metadata

    blank = pd.DataFrame({"sample_id": ["a", "b"], "condition": ["x", "y"], "library_name": ["", None]})
    export_metadata(blank, tmp_path / "s.tsv")
    assert (tmp_path / "s.tsv").read_text(encoding="utf-8").splitlines()[0] == "sample_id\tcondition"

    named = blank.assign(library_name=["L1", ""])
    export_metadata(named, tmp_path / "s.csv")
    assert (tmp_path / "s.csv").read_text(encoding="utf-8").splitlines()[0] == "sample_id,condition,library_name"
