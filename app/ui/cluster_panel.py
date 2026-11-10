"""Cluster (Slurm) settings: every `execution` field plus per-step resources, in one panel.

Built from app.core.cluster_settings, so the panel, the documentation and `bulkseq config`
name the same settings. Saving goes through the same AppConfig validation as the command line.
"""

from __future__ import annotations

import typing
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.cluster_settings import EXECUTION_GROUPS, RULE_PLACEMENT_FIELDS
from app.core.config_models import (
    AppConfig,
    ExecutionConfig,
    RuleMemoryGb,
    RuleRuntimeMin,
    RuleSlurmPlacement,
    RuleThreads,
)

# Per-step table: step name, then threads, memory (GB), walltime (min), then placement fields.
STEP_COLUMNS = ["Step", "Threads", "Memory (GB)", "Walltime (min)"] + [label for _, label in RULE_PLACEMENT_FIELDS]


def _bounds(field) -> tuple[int, int]:
    low, high = 0, 2_000_000_000
    for item in field.metadata:
        low = getattr(item, "ge", low)
        high = getattr(item, "le", high)
    return int(low), int(min(high, 2_000_000_000))


class ClusterSettingsPanel(QWidget):
    def __init__(self, info_label: Callable[[str, str], QWidget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widgets: dict[str, QWidget] = {}
        for title, rows in EXECUTION_GROUPS:
            group = QGroupBox(title)
            form = QFormLayout(group)
            for name, label, help_text in rows:
                widget = self._editor(name)
                widget.setAccessibleName(label)
                widget.setToolTip(help_text)
                self.widgets[name] = widget
                form.addRow(info_label(label, help_text), widget)
            layout.addWidget(group)

        steps_group = QGroupBox("Per-step resources and placement")
        steps_layout = QVBoxLayout(steps_group)
        self.steps = QTableWidget(0, len(STEP_COLUMNS))
        self.steps.setHorizontalHeaderLabels(STEP_COLUMNS)
        self.steps.setAccessibleName("Per-step cluster resources")
        self.steps.verticalHeader().setVisible(False)
        self.steps.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.steps.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.steps.horizontalHeader().setStretchLastSection(True)
        self.steps.setToolTip(
            "Threads and memory are what each step asks for on any executor; walltime only matters on a "
            "cluster. A blank placement cell uses the run-wide value above. Save Resources resets threads "
            "and memory to this computer's limits, so set cluster values here after it.")
        steps_layout.addWidget(self.steps)
        layout.addWidget(steps_group)

    @staticmethod
    def _editor(name: str) -> QWidget:
        field = ExecutionConfig.model_fields[name]
        annotation = field.annotation
        if annotation is bool:
            return QCheckBox()
        if annotation is int:
            spin = QSpinBox()
            spin.setRange(*_bounds(field))
            return spin
        if typing.get_origin(annotation) is typing.Literal:
            combo = QComboBox()
            combo.addItems([str(value) for value in typing.get_args(annotation)])
            return combo
        return QLineEdit()

    @staticmethod
    def _step_names() -> list[str]:
        names: list[str] = []
        for model in (RuleThreads, RuleMemoryGb, RuleRuntimeMin):
            names += [name for name in model.model_fields if name not in names]
        return names

    def load(self, config: AppConfig) -> None:
        execution = config.execution
        for name, widget in self.widgets.items():
            value = getattr(execution, name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(str(value))
            elif isinstance(value, list):
                widget.setText(", ".join(str(item) for item in value))
            else:
                widget.setText(str(value))
        names = self._step_names()
        names += sorted(name for name in execution.rule_resources if name not in names)
        self.steps.setRowCount(len(names))
        sections = (config.rule_threads.model_dump(), config.rule_memory_gb.model_dump(),
                    config.rule_runtime_min.model_dump())
        for row, name in enumerate(names):
            step = QTableWidgetItem(name)
            step.setFlags(step.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.steps.setItem(row, 0, step)
            for column, values in enumerate(sections, start=1):
                item = QTableWidgetItem(str(values.get(name, "")))
                if name not in values:
                    # This step has no such setting (it uses the run-wide default); not editable.
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip("Uses the run-wide default for this step.")
                self.steps.setItem(row, column, item)
            placement = execution.rule_resources.get(name, RuleSlurmPlacement())
            for offset, (field, _) in enumerate(RULE_PLACEMENT_FIELDS, start=4):
                self.steps.setItem(row, offset, QTableWidgetItem(getattr(placement, field)))
        # Tall enough to show every step without an inner scroll bar.
        height = self.steps.horizontalHeader().height() + 2 * self.steps.frameWidth()
        height += sum(self.steps.rowHeight(row) for row in range(self.steps.rowCount()))
        self.steps.setMinimumHeight(height)

    def apply_to(self, config: AppConfig) -> AppConfig:
        """A validated copy of ``config`` carrying the panel's values (raises ValueError)."""
        data = config.model_dump(mode="json")
        execution = data["execution"]
        for name, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                execution[name] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                execution[name] = widget.value()
            elif isinstance(widget, QComboBox):
                execution[name] = widget.currentText()
            else:
                execution[name] = widget.text().strip()
        sections = (data["rule_threads"], data["rule_memory_gb"], data["rule_runtime_min"])
        placements: dict[str, dict[str, str]] = {}
        for row in range(self.steps.rowCount()):
            name = self.steps.item(row, 0).text()
            for column, section in enumerate(sections, start=1):
                text = (self.steps.item(row, column).text() if self.steps.item(row, column) else "").strip()
                if name in section:
                    try:
                        section[name] = int(text)
                    except ValueError:
                        raise ValueError(f"{STEP_COLUMNS[column]} for {name} must be a whole number.") from None
            values = {field: (self.steps.item(row, offset).text().strip() if self.steps.item(row, offset) else "")
                      for offset, (field, _) in enumerate(RULE_PLACEMENT_FIELDS, start=4)}
            if any(values.values()):
                placements[name] = values
        execution["rule_resources"] = placements
        return AppConfig.model_validate(data)
