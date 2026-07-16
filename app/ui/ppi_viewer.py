from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.core.paths import app_root

# QtWebEngine is a large, separately-shipped Chromium module; guard the import so
# the PPI tab degrades to the static figure (never crashes) if it is missing in a
# packaged build or fails to initialise.
try:
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except Exception:  # pragma: no cover
    QWebEngineView = None  # type: ignore[assignment]
    QWebEngineSettings = None  # type: ignore[assignment]
    WEBENGINE_AVAILABLE = False


def viewer_html_path() -> Path:
    return app_root() / "app" / "assets" / "web" / "ppi" / "viewer.html"


class PpiViewer(QWidget):
    """Interactive PPI network (cytoscape.js in a QWebEngineView).

    Data is assembled in Python and pushed in via runJavaScript; the page is fully
    offline (vendored JS, remote content disabled). When QtWebEngine is
    unavailable, the widget falls back to the static PPI figure in an ImageViewer.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending_graph: dict | None = None
        self._theme: dict | None = None
        self._fallback = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if WEBENGINE_AVAILABLE and viewer_html_path().exists():
            self.view = QWebEngineView(self)
            settings = self.view.settings()
            try:  # offline lockdown: a local page must not reach remote URLs
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            except Exception:
                pass
            self.view.loadFinished.connect(self._on_loaded)
            self.view.setUrl(QUrl.fromLocalFile(str(viewer_html_path())))
            layout.addWidget(self.view)
        else:
            self.view = None
            from app.ui.image_viewer import ImageViewer

            note = QLabel("Interactive view unavailable — showing the static PPI figure.")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            self._fallback = ImageViewer(self)
            layout.addWidget(note)
            layout.addWidget(self._fallback, 1)

    @property
    def available(self) -> bool:
        return self.view is not None

    # --- lifecycle -------------------------------------------------------
    def _on_loaded(self, ok: bool) -> None:
        self._ready = bool(ok)
        if not self._ready:
            return
        if self._theme is not None:
            self._run("setTheme", self._theme)
        if self._pending_graph is not None:
            self._inject(self._pending_graph)
            self._pending_graph = None

    def _run(self, fn: str, *args) -> None:
        if self.view is None or not self._ready:
            return
        js_args = ",".join(json.dumps(a) for a in args)
        self.view.page().runJavaScript(f"window.PPI && PPI.{fn}({js_args})")

    def _inject(self, elements: dict) -> None:
        # allow_nan=False guarantees no bare NaN (invalid JSON that would break
        # the page); the assembler nulls missing values beforehand.
        payload = json.dumps({"elements": elements}, allow_nan=False)
        self.view.page().runJavaScript(f"window.PPI && PPI.render({payload})")

    # --- public API ------------------------------------------------------
    def load_graph(self, elements: dict) -> None:
        if self.view is None:
            return
        if self._ready:
            self._inject(elements)
        else:
            self._pending_graph = elements

    def load_static(self, png_or_svg: str | Path) -> None:
        if self._fallback is not None and Path(png_or_svg).exists():
            self._fallback.set_image(png_or_svg)

    def set_layout(self, name: str) -> None:
        self._run("setLayout", name)

    def set_color_by(self, field: str) -> None:
        self._run("setColorBy", field)

    def set_size_by(self, field: str) -> None:
        self._run("setSizeBy", field)

    def set_labels(self, on: bool) -> None:
        self._run("setLabels", bool(on))

    def set_gene_italic(self, on: bool) -> None:
        self._run("setGeneItalic", bool(on))

    def set_focus_labels(self, on: bool) -> None:
        self._run("setFocusLabels", bool(on))

    def set_direction_filter(self, mode: str) -> None:
        self._run("setDirectionFilter", mode)

    def set_confidence(self, floor: float) -> None:
        self._run("setConfidence", float(floor))

    def update_theme(self, palette: dict) -> None:
        self._theme = palette
        if self._ready:
            self._run("setTheme", palette)

    def export_image(self, fmt: str, bg: str, callback) -> None:
        if self.view is None or not self._ready:
            callback("")
            return
        self.view.page().runJavaScript(
            f"window.PPI ? PPI.exportImage({json.dumps(fmt)}, {json.dumps(bg)}) : ''", callback)

    # --- self-test helpers ----------------------------------------------
    def probe_version(self, callback) -> None:
        if self.view is None:
            callback(None)
            return
        self.view.page().runJavaScript("window.PPI ? PPI.version() : ''", callback)

    def stats(self, callback) -> None:
        if self.view is None or not self._ready:
            callback("{}")
            return
        self.view.page().runJavaScript("window.PPI ? PPI.stats() : '{}'", callback)
