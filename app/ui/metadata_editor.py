from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QTableWidgetItem

from app.constants import OPTIONAL_METADATA_COLUMNS, REQUIRED_METADATA_COLUMNS


class MetadataTable(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, len(self.default_columns()))
        self.setHorizontalHeaderLabels(self.default_columns())
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setAlternatingRowColors(True)

    @staticmethod
    def default_columns() -> list[str]:
        return REQUIRED_METADATA_COLUMNS + OPTIONAL_METADATA_COLUMNS

    def load_dataframe(self, df: pd.DataFrame) -> None:
        columns = list(df.columns) or self.default_columns()
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setRowCount(len(df))
        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, col in enumerate(columns):
                item = QTableWidgetItem(str(row.get(col, "")))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.setItem(row_idx, col_idx, item)
        self.resizeColumnsToContents()

    def to_dataframe(self) -> pd.DataFrame:
        columns = [self.horizontalHeaderItem(i).text() for i in range(self.columnCount())]
        rows: list[dict[str, str]] = []
        for r in range(self.rowCount()):
            rows.append({col: (self.item(r, c).text() if self.item(r, c) else "") for c, col in enumerate(columns)})
        return pd.DataFrame(rows, columns=columns)

    def load_tsv(self, path: Path) -> None:
        self.load_dataframe(pd.read_csv(path, sep="\t", dtype=str).fillna(""))

    def append_empty_row(self) -> None:
        self.insertRow(self.rowCount())

    def delete_selected_rows(self) -> None:
        rows = sorted({idx.row() for idx in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self.removeRow(row)

    def duplicate_selected_rows(self) -> None:
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        for row in rows:
            target = self.rowCount()
            self.insertRow(target)
            for col in range(self.columnCount()):
                text = self.item(row, col).text() if self.item(row, col) else ""
                self.setItem(target, col, QTableWidgetItem(text))

    def add_column(self, name: str) -> None:
        col = self.columnCount()
        self.insertColumn(col)
        self.setHorizontalHeaderItem(col, QTableWidgetItem(name))

    def rename_column(self, index: int, name: str) -> None:
        if 0 <= index < self.columnCount():
            self.setHorizontalHeaderItem(index, QTableWidgetItem(name))

    def remove_column(self, index: int) -> None:
        if 0 <= index < self.columnCount():
            self.removeColumn(index)

    def column_names(self) -> list[str]:
        return [self.horizontalHeaderItem(i).text() if self.horizontalHeaderItem(i) else "" for i in range(self.columnCount())]

    def assign_condition(self, value: str) -> None:
        names = self.column_names()
        if "condition" not in names:
            return
        col = names.index("condition")
        for row in sorted({idx.row() for idx in self.selectedIndexes()}):
            self.setItem(row, col, QTableWidgetItem(value))

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_clipboard()
            return
        super().keyPressEvent(event)

    def paste_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        # Parse the clipboard into a grid: rows on newlines, cells on tabs.
        # Excel/xlsx copies tab-separated cells with a trailing newline; drop it.
        lines = text.split("\n")
        if len(lines) > 1 and lines[-1] == "":
            lines = lines[:-1]
        grid = [line.rstrip("\r").split("\t") for line in lines]
        selected = self.selectedIndexes()
        if selected:
            top = min(idx.row() for idx in selected)
            left = min(idx.column() for idx in selected)
        else:
            top = self.currentRow() if self.currentRow() >= 0 else 0
            left = self.currentColumn() if self.currentColumn() >= 0 else 0
        # A single copied value pasted onto a multi-cell selection fills every
        # selected cell (Excel/Sheets behaviour).
        if len(grid) == 1 and len(grid[0]) == 1 and len(selected) > 1:
            value = grid[0][0]
            for idx in selected:
                self.setItem(idx.row(), idx.column(), QTableWidgetItem(value))
            return
        # Otherwise lay the copied block down from the top-left anchor,
        # growing rows and columns as needed.
        for r, cells in enumerate(grid):
            row = top + r
            if row >= self.rowCount():
                self.insertRow(self.rowCount())
            for c, cell in enumerate(cells):
                col = left + c
                if col >= self.columnCount():
                    self.add_column(f"col_{col}")
                self.setItem(row, col, QTableWidgetItem(cell))

    def autofill_replicates(self) -> None:
        columns = [self.horizontalHeaderItem(i).text() for i in range(self.columnCount())]
        if "condition" not in columns or "replicate" not in columns:
            return
        condition_col = columns.index("condition")
        replicate_col = columns.index("replicate")
        counts: dict[str, int] = {}
        for row in range(self.rowCount()):
            condition = self.item(row, condition_col).text() if self.item(row, condition_col) else "unknown"
            counts[condition] = counts.get(condition, 0) + 1
            self.setItem(row, replicate_col, QTableWidgetItem(str(counts[condition])))
