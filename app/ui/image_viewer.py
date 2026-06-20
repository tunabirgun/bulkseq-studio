from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

# QtSvgWidgets is a separate Qt module; guard the import so the viewer still works
# (PNG only) if it is unavailable in a packaged build.
try:
    from PySide6.QtSvgWidgets import QGraphicsSvgItem

    SVG_AVAILABLE = True
except Exception:  # pragma: no cover
    QGraphicsSvgItem = None  # type: ignore[assignment]
    SVG_AVAILABLE = False


class ImageViewer(QGraphicsView):
    """A zoomable, pannable image view for PNG or (vector) SVG figures.

    - mouse wheel: zoom in/out (anchored under the cursor)
    - click + drag: pan
    - fit(): scale to fit while preserving aspect ratio (handles tall/wide figures)
    SVG renders crisply at any zoom; PNG is the fast default for complex figures.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsItem | None = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(360)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._has_image = False

    def set_image(self, path: str | Path) -> None:
        p = str(path)
        self._scene.clear()
        self._item = None
        self._has_image = False
        if SVG_AVAILABLE and p.lower().endswith(".svg"):
            item = QGraphicsSvgItem(p)
            renderer = item.renderer()
            if renderer is not None and renderer.isValid():
                self._item = item
                self._has_image = True
        else:
            pixmap = QPixmap(p)
            if not pixmap.isNull():
                self._item = QGraphicsPixmapItem(pixmap)
                self._has_image = True
        if self._has_image and self._item is not None:
            self._scene.addItem(self._item)
            self._scene.setSceneRect(self._item.boundingRect())
            self.fit()

    def update_theme(self, bg_hex: str) -> None:
        # A QGraphicsScene does not inherit widget QSS, so set its background
        # explicitly when the app theme changes.
        self.setBackgroundBrush(QColor(bg_hex))

    def clear(self) -> None:
        self._scene.clear()
        self._item = None
        self._has_image = False

    def fit(self) -> None:
        if self._has_image and self._item is not None:
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
