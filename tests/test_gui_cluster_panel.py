"""The interface's Cluster (Slurm) panel edits every execution setting and the per-step table."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.core.cluster_settings import documented_fields  # noqa: E402
from app.core.project import ProjectManager  # noqa: E402
from app.ui.cluster_panel import STEP_COLUMNS, ClusterSettingsPanel  # noqa: E402


@pytest.fixture()
def panel():
    QApplication.instance() or QApplication([])
    return ClusterSettingsPanel(lambda text, _help: QLabel(text))


def _row(panel, step):
    return next(r for r in range(panel.steps.rowCount()) if panel.steps.item(r, 0).text() == step)


def test_every_setting_has_an_accessible_editor(panel) -> None:
    assert sorted(panel.widgets) == sorted(documented_fields())
    assert all(widget.accessibleName() for widget in panel.widgets.values())


def test_panel_round_trips_and_validates(panel, tmp_path) -> None:
    manager = ProjectManager()
    root = manager.create_project("panel", tmp_path)
    config = manager.load_config(root)
    panel.load(config)
    panel.widgets["slurm_partition"].setText("compute")
    panel.widgets["slurm_requeue"].setCurrentText("requeue")
    panel.widgets["min_cpus_per_job"].setValue(56)
    panel.widgets["compute_nodes_have_internet"].setChecked(True)
    row = _row(panel, "star_index")
    panel.steps.item(row, STEP_COLUMNS.index("Memory (GB)")).setText("200")
    panel.steps.item(row, STEP_COLUMNS.index("Walltime (min)")).setText("1440")
    panel.steps.item(row, STEP_COLUMNS.index("Partition")).setText("bigmem")
    updated = panel.apply_to(config)
    assert updated.execution.slurm_partition == "compute" and updated.execution.slurm_requeue == "requeue"
    assert updated.execution.min_cpus_per_job == 56 and updated.execution.compute_nodes_have_internet
    assert updated.rule_memory_gb.star_index == 200 and updated.rule_runtime_min.star_index == 1440
    assert updated.execution.rule_resources["star_index"].slurm_partition == "bigmem"
    assert set(updated.execution.rule_resources) == {"star_index"}

    panel.widgets["slurm_qos"].setText("a;b")
    with pytest.raises(ValueError):
        panel.apply_to(config)


def test_save_resources_refreshes_the_panel_so_its_save_keeps_the_new_values(tmp_path) -> None:
    from app.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    window = MainWindow()
    root = ProjectManager().create_project("panel3", tmp_path)
    window._load_project(root, check_unsaved=False)
    window.cores.setValue(24)
    window.ram.setValue(64)
    window._save_resources()
    saved = ProjectManager().load_config(root)
    assert saved.rule_threads.star_align == window.config.rule_threads.star_align
    applied = window.cluster_panel.apply_to(window.config)
    assert applied.rule_threads == saved.rule_threads and applied.rule_memory_gb == saved.rule_memory_gb
    window.close()


def test_steps_without_a_walltime_setting_are_not_editable(panel, tmp_path) -> None:
    manager = ProjectManager()
    panel.load(manager.load_config(manager.create_project("panel2", tmp_path)))
    item = panel.steps.item(_row(panel, "fastqc"), STEP_COLUMNS.index("Walltime (min)"))
    assert item.text() == "" and not (item.flags() & item.flags().ItemIsEditable)
