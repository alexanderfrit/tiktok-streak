"""SpinBox wrapper preventing collapsed 1px QLineEdit width on PySide6 6.12 + qfluentwidgets."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QFocusEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import SpinBox


class FluentSpinBox(SpinBox):
    """SpinBox that explicitly enforces visible geometry on its internal QLineEdit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(130)
        self.valueChanged.connect(lambda _val: self._schedule_line_edit_fix())
        le = self.lineEdit()
        if le is not None:
            le.textChanged.connect(lambda _txt: self._schedule_line_edit_fix())
        self._schedule_line_edit_fix()

    def _fix_line_edit_geometry(self) -> None:
        le = self.lineEdit()
        up_btn = getattr(self, "upButton", None)
        if le is None or up_btn is None:
            return
        right_pad = 78 if up_btn.isVisible() else 12
        le.setGeometry(
            10,
            1,
            max(32, self.width() - right_pad - 10),
            max(20, self.height() - 2),
        )

    def _schedule_line_edit_fix(self) -> None:
        self._fix_line_edit_geometry()
        QTimer.singleShot(0, self._fix_line_edit_geometry)

    def setSymbolVisible(self, isVisible: bool) -> None:
        super().setSymbolVisible(isVisible)
        self._schedule_line_edit_fix()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fix_line_edit_geometry()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_line_edit_fix()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        self._schedule_line_edit_fix()

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._schedule_line_edit_fix()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._schedule_line_edit_fix()


__all__ = ["FluentSpinBox"]
