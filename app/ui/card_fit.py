from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget


class CenteredCardFit(QObject):
    """Size a centred card from the height its layout needs at the width it gets.

    A layout item added with an alignment is laid out at its size hint, which does not know
    how a word-wrapped label will wrap at the card's final width, so the last lines of a
    long message were cut off. Refit when the page resizes or the card's contents change.
    """

    def __init__(self, page: QWidget, card: QWidget, margin: int,
                 min_width: int = 380, max_width: int = 560) -> None:
        super().__init__(card)
        self._page, self._card = page, card
        self._margin, self._min_width, self._max_width = margin, min_width, max_width
        page.installEventFilter(self)
        card.installEventFilter(self)
        self.fit()

    def fit(self) -> None:
        width = max(self._min_width, min(self._max_width, self._page.width() - 2 * self._margin))
        self._card.setFixedWidth(width)
        self._card.setFixedHeight(self._card.layout().totalHeightForWidth(width))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual name
        if (watched is self._page and event.type() == QEvent.Type.Resize) or (
                watched is self._card and event.type() == QEvent.Type.LayoutRequest):
            self.fit()
        return False
