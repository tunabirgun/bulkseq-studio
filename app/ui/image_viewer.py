from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class ImageViewer(QGraphicsView):
    """A zoomable, pannable image view.

    - mouse wheel: zoom in/out (anchored under the cursor)
    - click + drag: pan
    - fit(): scale to fit while preserving aspect ratio (handles tall/wide figures)
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(360)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._has_image = False

    def set_image(self, path: str | Path) -> None:
        pixmap = QPixmap(str(path))
        self._item.setPixmap(pixmap)
        self._has_image = not pixmap.isNull()
        if self._has_image:
            self._scene.setSceneRect(self._item.boundingRect())
            self.fit()

    def update_theme(self, bg_hex: str) -> None:
        # A QGraphicsScene does not inherit widget QSS, so set its background
        # explicitly when the app theme changes.
        self.setBackgroundBrush(QColor(bg_hex))

    def clear(self) -> None:
        self._item.setPixmap(QPixmap())
        self._has_image = False

    def fit(self) -> None:
        if self._has_image:
            self.resetTransform()
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self) -> None:
        if self._has_image:
            self.resetTransform()

    def wheelEvent(self, event) -> None:
        if not self._has_image:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
