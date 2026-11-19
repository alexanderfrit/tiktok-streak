"""WinUI 3 Fluent status badge widget with optional 6px semantic indicator dot."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget
from qfluentwidgets import InfoLevel, isDarkTheme, qconfig

from gui.services.theme import PILL_RADIUS

_VALID_LEVELS = ("success", "warning", "error", "info", "neutral")

# Authentic WinUI 3 semantic tokens: subtle surface tint + quiet hairline border (no neon border)
_DARK_PALETTE: dict[str, tuple[str, str, str, str]] = {
    # level: (bg_rgba, border_rgba, dot_hex, text_hex)
    "success": ("rgba(108, 203, 95, 0.14)", "rgba(255, 255, 255, 0.07)", "#6ccb5f", "#dff0dd"),
    "warning": ("rgba(252, 225, 0, 0.12)", "rgba(255, 255, 255, 0.07)", "#fce100", "#f5ebc6"),
    "error": ("rgba(255, 153, 164, 0.14)", "rgba(255, 255, 255, 0.07)", "#ff99a4", "#f7d9dc"),
    "info": ("rgba(96, 205, 255, 0.14)", "rgba(255, 255, 255, 0.07)", "#60cdff", "#d8eefb"),
    "neutral": ("rgba(255, 255, 255, 0.06)", "rgba(255, 255, 255, 0.08)", "#9e9e9e", "#d4d4d4"),
}

_LIGHT_PALETTE: dict[str, tuple[str, str, str, str]] = {
    "success": ("#dff6dd", "rgba(0, 0, 0, 0.06)", "#0f7b0f", "#0d5c0d"),
    "warning": ("#fff4ce", "rgba(0, 0, 0, 0.06)", "#9d5d00", "#7a4700"),
    "error": ("#fde7e9", "rgba(0, 0, 0, 0.06)", "#c42b1c", "#8f1d12"),
    "info": ("#e6f2fb", "rgba(0, 0, 0, 0.06)", "#005fb8", "#004587"),
    "neutral": ("rgba(0, 0, 0, 0.05)", "rgba(0, 0, 0, 0.07)", "#767676", "#3b3b3b"),
}

_INFO_LEVEL_MAP: dict[InfoLevel, str] = {
    InfoLevel.SUCCESS: "success",
    InfoLevel.WARNING: "warning",
    InfoLevel.ERROR: "error",
    InfoLevel.INFOAMTION: "info",
    InfoLevel.ATTENTION: "warning",
}


def _normalize_level(level: str | InfoLevel | None) -> str:
    if isinstance(level, InfoLevel):
        return _INFO_LEVEL_MAP.get(level, "neutral")
    raw = str(level or "neutral").strip().lower()
    if raw in _VALID_LEVELS:
        return raw
    if raw in ("infoamtion", "information"):
        return "info"
    if raw == "attention":
        return "warning"
    return "neutral"


class _StatusDot(QWidget):
    """Crisp antialiased 6px circular status indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor("#9e9e9e")
        self.setFixedSize(6, 6)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setColor(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(0.0, 0.0, float(self.width()), float(self.height()))
        painter.end()


class StatusPill(QFrame):
    """Compact WinUI 3 Fluent badge showing an optional 6px status dot and label."""

    def __init__(
        self,
        text: str = "",
        level: str | InfoLevel = "neutral",
        parent: QWidget | None = None,
        *,
        show_dot: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self._level = _normalize_level(level)
        self._text = str(text or "")
        self._show_dot = bool(show_dot)

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self._layout = QHBoxLayout(self)
        self._layout.setSpacing(6)

        self._dot = _StatusDot(self)
        self._label = QLabel(self._text, self)
        self._label.setObjectName("StatusPillLabel")
        font = QFont(self._label.font())
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        self._label.setFont(font)

        self._layout.addWidget(self._dot, 0, Qt.AlignVCenter)
        self._layout.addWidget(self._label, 0, Qt.AlignVCenter)

        self._sync_dot_visibility()
        self._apply_palette()
        self.setVisible(bool(self._text.strip()))

        try:
            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception:
            pass

    def _sync_dot_visibility(self) -> None:
        self._dot.setVisible(self._show_dot)
        if self._show_dot:
            self._layout.setContentsMargins(8, 3, 9, 3)
        else:
            self._layout.setContentsMargins(8, 3, 8, 3)

    def _on_theme_changed(self, _theme=None) -> None:
        self._apply_palette()

    def _apply_palette(self) -> None:
        palette = _DARK_PALETTE if isDarkTheme() else _LIGHT_PALETTE
        bg, border, dot_hex, text_hex = palette.get(self._level, palette["neutral"])
        self._dot.setColor(dot_hex)
        self.setStyleSheet(
            f"StatusPill#StatusPill {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {PILL_RADIUS}px;"
            f"}}"
        )
        self._label.setStyleSheet(
            f"QLabel#StatusPillLabel {{"
            f" color: {text_hex};"
            f" background: transparent;"
            f" border: none;"
            f"}}"
        )

    def setState(
        self,
        level: str | InfoLevel,
        text: str,
        *,
        show_dot: bool | None = None,
    ) -> None:
        self._level = _normalize_level(level)
        self._text = str(text or "")
        if show_dot is not None:
            self._show_dot = bool(show_dot)
            self._sync_dot_visibility()
        self._label.setText(self._text)
        self._apply_palette()
        self.setVisible(bool(self._text.strip()))

    def setShowDot(self, show_dot: bool) -> None:
        self._show_dot = bool(show_dot)
        self._sync_dot_visibility()

    def showDot(self) -> bool:
        return self._show_dot

    def setLevel(self, level: str | InfoLevel) -> None:
        self._level = _normalize_level(level)
        self._apply_palette()

    def setText(self, text: str) -> None:
        self._text = str(text or "")
        self._label.setText(self._text)
        self.setVisible(bool(self._text.strip()))

    def text(self) -> str:
        return self._text

    def level(self) -> str:
        return self._level


__all__ = ["StatusPill"]
