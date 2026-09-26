from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtWidgets import QApplication

from app.cli import main as cli_main
from app.core.benchmark_datasets import create_benchmark_project
from app.core.metadata import load_metadata, validate_metadata
from app.core.preflight_checks import enrichment_config_messages, route_preflight_messages
from app.core.project import ProjectManager
from app.ui.main_window import MainWindow


def _edit(root: Path, change) -> None:
    manager = ProjectManager()
    config = manager.load_config(root)
    change(config)
    manager.save_config(root, config)


def _missing_design_column(config) -> None:
    config.deseq2.design_formula = "~ extraction_day + condition"


def _no_organism(config) -> None:
    # An organism outside the catalogue: the interface cannot refill its identifiers.
    config.reference.organism_name = "Unlisted organism"
    config.enrichment.orgdb = None
    config.enrichment.kegg_organism = None
    config.enrichment.gprofiler_organism = None
    config.ppi.taxon = None


def _no_reference(config) -> None:
    config.reference.genome_fasta_url = None
    config.reference.annotation_gtf_url = None
    config.reference.genome_fasta = None
    config.reference.annotation_file = None


CASES = {"as_bundled": None, "missing_design_column": _missing_design_column,
         "no_organism": _no_organism, "no_reference": _no_reference}
EXPECTED = {"as_bundled": "WARNING", "missing_design_column": "FAIL",
            "no_organism": "REVIEW_REQUIRED", "no_reference": "FAIL"}


def _cli_messages(root: Path, capsys) -> list[dict[str, str]]:
    capsys.readouterr()
    cli_main(["check", "-C", str(root), "--json"])
    return json.loads(capsys.readouterr().out)["messages"]


def _gui_messages(root: Path) -> list[dict[str, str]]:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._load_project(root)
        return window._input_validation_messages()
    finally:
        window.close()


@pytest.mark.parametrize("case", sorted(CASES))
def test_start_gate_and_bulkseq_check_report_the_same_findings(tmp_path, capsys, case):
    root = create_benchmark_project("pasilla_paired_subset", tmp_path, f"parity-{case}")
    if CASES[case]:
        _edit(root, CASES[case])
    gui = _gui_messages(root)
    cli = _cli_messages(root, capsys)
    assert gui == cli
    assert EXPECTED[case] in {m["status"] for m in cli}, cli


def test_the_previous_command_line_findings_would_have_diverged(tmp_path, capsys):
    # Negative control: 0.32.1's `bulkseq check` validated the sheet without the design
    # variables and without the route and enrichment findings. The comparison above must
    # see that difference, or it would pass for any pair of implementations.
    root = create_benchmark_project("pasilla_paired_subset", tmp_path, "parity-old-cli")
    _edit(root, _missing_design_column)
    config = ProjectManager().load_config(root)
    sheet = load_metadata(root / "config" / "samples.tsv")
    old_cli = validate_metadata(sheet, allow_pending_sra=True)
    assert old_cli != _gui_messages(root)
    assert "FAIL" not in {m["status"] for m in old_cli}
    assert route_preflight_messages(config, root) and enrichment_config_messages(config) == []
