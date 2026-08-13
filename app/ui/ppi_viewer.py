from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QStackedWidget, QVBoxLayout, QWidget

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


DEFAULT_EMPTY_MESSAGE = (
    "No PPI network loaded yet.\n"
    "Load an existing network or build one with the Network construction controls."
)


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
        self._content: QWidget | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ppiViewerStack")
        self._empty_page = QWidget(self._stack)
        empty_layout = QVBoxLayout(self._empty_page)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.addStretch(1)
        self._empty_card = QFrame(self._empty_page)
        self._empty_card.setObjectName("ppiEmptyCard")
        self._empty_card.setMinimumWidth(380)
        self._empty_card.setMaximumWidth(560)
        empty_card_layout = QVBoxLayout(self._empty_card)
        empty_card_layout.setContentsMargins(20, 18, 20, 18)
        empty_card_layout.setSpacing(8)
        self._empty_title = QLabel("No network loaded", self._empty_card)
        self._empty_title.setObjectName("ppiEmptyTitle")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label = QLabel(DEFAULT_EMPTY_MESSAGE, self._empty_card)
        self._empty_label.setObjectName("ppiEmptyState")
        self._empty_label.setAccessibleName("PPI network state")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        empty_card_layout.addWidget(self._empty_title)
        empty_card_layout.addWidget(self._empty_label)
        empty_layout.addWidget(self._empty_card, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        self._stack.addWidget(self._empty_page)
        layout.addWidget(self._stack, 1)

        if WEBENGINE_AVAILABLE and viewer_html_path().exists():
            self.view = QWebEngineView(self)
            # QWebEngineView defaults to NoFocus on this Qt build.  Without an
            # explicit policy, the HTML canvas can only be reached by a mouse or
            # synthetic JavaScript focus, despite its correct tabindex.
            self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.view.setAccessibleName("Interactive protein interaction network")
            self.view.setAccessibleDescription(
                "Use arrow keys to inspect proteins, Enter or Space to focus a protein and its "
                "neighbours, Escape to clear selection, plus and minus to zoom, and zero to fit."
            )
            settings = self.view.settings()
            try:  # offline lockdown: a local page must not reach remote URLs
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            except Exception:
                pass
            self.view.loadFinished.connect(self._on_loaded)
            self.view.setUrl(QUrl.fromLocalFile(str(viewer_html_path())))
            self._content = self.view
            self._stack.addWidget(self.view)
        else:
            self.view = None
            from app.ui.image_viewer import ImageViewer

            fallback_page = QWidget(self._stack)
            fallback_layout = QVBoxLayout(fallback_page)
            fallback_layout.setContentsMargins(0, 0, 0, 0)
            note = QLabel("Interactive view unavailable — showing the static PPI figure.")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            self._fallback = ImageViewer(self)
            fallback_layout.addWidget(note)
            fallback_layout.addWidget(self._fallback, 1)
            self._content = fallback_page
            self._stack.addWidget(fallback_page)

        self._style_empty_state()
        self.set_empty_state()

    @property
    def available(self) -> bool:
        return self.view is not None

    @property
    def empty_state_visible(self) -> bool:
        """Whether the explicit no-network surface is the active canvas."""
        return self._stack.currentWidget() is self._empty_page

    # --- lifecycle -------------------------------------------------------
    def _on_loaded(self, ok: bool) -> None:
        self._ready = bool(ok)
        if not self._ready:
            if self._pending_graph is not None:
                self.set_empty_state("The interactive PPI viewer could not be loaded.")
            return
        if self._theme is not None:
            self._run("setTheme", self._theme)
        if self._pending_graph is not None:
            self._show_content()
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

    def _show_content(self) -> None:
        if self._content is not None:
            self._stack.setCurrentWidget(self._content)
            self._schedule_viewport_refresh()

    def _schedule_viewport_refresh(self) -> None:
        # A hidden QWebEngineView can report a zero-sized Cytoscape container
        # when the graph is first injected. Re-layout after the stacked page has
        # received its real geometry so loaded nodes cannot remain off-canvas.
        QTimer.singleShot(0, lambda: self._run("resize"))
        QTimer.singleShot(180, lambda: self._run("resize"))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual name
        super().showEvent(event)
        if self._stack.currentWidget() is self._content:
            self._schedule_viewport_refresh()

    def _style_empty_state(self, palette: dict | None = None) -> None:
        widget_palette = self.palette()
        fallback_bg = widget_palette.color(QPalette.ColorRole.Window)
        fallback_text = widget_palette.color(QPalette.ColorRole.WindowText)
        bg = QColor(str((palette or {}).get("bg", fallback_bg.name())))
        surface = QColor(str((palette or {}).get("surface", bg.name())))
        border = QColor(str((palette or {}).get("edge", fallback_text.name())))
        title = QColor(str((palette or {}).get("text", fallback_text.name())))
        text = QColor(str((palette or {}).get("muted", (palette or {}).get("text", fallback_text.name()))))
        if not bg.isValid():
            bg = fallback_bg
        if not text.isValid():
            text = fallback_text
        if not surface.isValid():
            surface = bg
        if not border.isValid():
            border = fallback_text
        if not title.isValid():
            title = fallback_text

        self._empty_page.setStyleSheet(f"background-color: {bg.name()};")
        self._empty_card.setStyleSheet(
            "QFrame#ppiEmptyCard {"
            f"background-color: {surface.name()}; border: 1px solid {border.name()};"
            "border-radius: 10px;"
            "}"
        )
        self._empty_title.setStyleSheet(
            f"color: {title.name()}; font-size: 12pt; font-weight: 600; background: transparent;")
        self._empty_label.setStyleSheet(
            f"color: {text.name()}; font-size: 9.5pt; background: transparent;")

    # --- public API ------------------------------------------------------
    def set_empty_state(self, message: str | None = None) -> None:
        """Show a non-interactive, theme-aware message in place of the canvas."""
        text = (message or DEFAULT_EMPTY_MESSAGE).strip()
        self._empty_label.setText(text)
        self._empty_label.setAccessibleDescription(text)
        self._stack.setCurrentWidget(self._empty_page)

    def clear_network(self, message: str | None = None) -> None:
        """Clear pending/rendered content and restore the explicit empty state."""
        self._pending_graph = None
        if self._fallback is not None:
            self._fallback.clear()
        if self.view is not None and self._ready:
            self._inject({"nodes": [], "edges": []})
        self.set_empty_state(message)

    def load_graph(self, elements: dict) -> None:
        if self.view is None:
            return
        if not isinstance(elements, dict) or not elements.get("nodes"):
            self.clear_network()
            return
        self._show_content()
        if self._ready:
            self._inject(elements)
        else:
            self._pending_graph = elements

    def load_static(self, png_or_svg: str | Path) -> None:
        if self._fallback is not None and Path(png_or_svg).exists():
            self._fallback.set_image(png_or_svg)
            if getattr(self._fallback, "_has_image", False):
                self._show_content()

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
        self._style_empty_state(palette)
        if self._fallback is not None:
            bg = QColor(str(palette.get("bg", "")))
            if not bg.isValid():
                bg = self.palette().color(QPalette.ColorRole.Window)
            self._fallback.update_theme(bg.name())
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
