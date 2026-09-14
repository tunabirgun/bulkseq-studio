"""Pins for the sample-sheet schema constants that are mirrored outside app/constants.py."""
from __future__ import annotations

import ast
from pathlib import Path
import sys

from app.constants import (DESCRIPTIVE_METADATA_COLUMNS, OPTIONAL_METADATA_COLUMNS,
                           REQUIRED_METADATA_COLUMNS, SCAFFOLD_METADATA_COLUMNS)
from app.core.project import ProjectManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow" / "scripts"))
import check_covariate_structure as ccs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = REPO_ROOT / "scripts" / "capture_gui_matrix.py"


def _synthetic_samples_call() -> ast.Call:
    """The _write_tsv(...) call in capture_gui_matrix.py that writes config/samples.tsv.

    Parsed rather than imported: the script pulls in PySide6 and the main window at import time.
    """
    tree = ast.parse(CAPTURE_SCRIPT.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_write_tsv"
             and "samples.tsv" in ast.unparse(node.args[0])]
    assert len(calls) == 1, f"expected one synthetic samples.tsv writer, found {len(calls)}"
    return calls[0]


def test_library_name_is_optional_not_required() -> None:
    # In REQUIRED it would be a hard "Missing required metadata columns" failure for every
    # sheet written before 0.31.0.
    assert "library_name" in OPTIONAL_METADATA_COLUMNS
    assert "library_name" not in REQUIRED_METADATA_COLUMNS


def test_scaffold_header_agrees_across_all_three_writers(tmp_path: Path) -> None:
    expected = list(SCAFFOLD_METADATA_COLUMNS)
    assert expected[:2] == ["sample_id", "library_name"]
    assert set(expected) <= set(REQUIRED_METADATA_COLUMNS) | set(OPTIONAL_METADATA_COLUMNS)

    root = ProjectManager().create_project("ScaffoldHeader", tmp_path)
    for name in ("samples.tsv", "samples.auto_generated.tsv"):
        text = (root / "config" / name).read_text(encoding="utf-8")
        assert text == "\t".join(expected) + "\n", name

    call = _synthetic_samples_call()
    header_src = ast.unparse(call.args[1])
    header = (expected if "SCAFFOLD_METADATA_COLUMNS" in header_src
              else ast.literal_eval(call.args[1]))
    assert header == expected
    # A header wider than its rows writes a malformed TSV that pandas reads column-shifted.
    assert [len(row.elts) for row in call.args[2].elts] == [len(expected)] * len(call.args[2].elts)


def test_descriptive_columns_match_the_workflow_copy() -> None:
    # check_covariate_structure.py cannot import app: it runs in the pipeline environment.
    assert set(ccs.EXCLUDED_COLUMNS) == set(DESCRIPTIVE_METADATA_COLUMNS)
    assert set(DESCRIPTIVE_METADATA_COLUMNS) <= set(REQUIRED_METADATA_COLUMNS) | set(OPTIONAL_METADATA_COLUMNS)
