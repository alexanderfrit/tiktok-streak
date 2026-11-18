"""High-contrast Fluent status pill widget with 6px indicator dot."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget
from qfluentwidgets import InfoLevel, isDarkTheme, qconfig

from gui.services.theme import PILL_RADIUS

_VALID_LEVELS = ("success", "warning", "error", "info", "neutral")

_DARK_PALETTE: dict[str, tuple[str, str, str, str]] = {
    # level: (bg_rgba, border_rgba, dot_hex, text_hex)
    "success": ("rgba(16, 185, 129, 0.14)", "rgba(52, 211, 153, 0.35)", "#34d399", "#a7f3d0"),
    "warning": ("rgba(245, 158, 11, 0.14)", "rgba(251, 191, 36, 0.36)", "#fbbf24", "#fde68a"),
    "error": ("rgba(239, 68, 68, 0.15)", "rgba(248, 113, 113, 0.38)", "#f87171", "#fecaca"),
    "info": ("rgba(59, 130, 246, 0.15)", "rgba(96, 165, 250, 0.38)", "#60a5fa", "#bfdbfe"),
    "neutral": ("rgba(255, 255, 255, 0.07)", "rgba(255, 255, 255, 0.14)", "#a1a1aa", "#e4e4e7"),
}

_LIGHT_PALETTE: dict[str, tuple[str, str, str, str]] = {
    "success": ("rgba(16, 185, 129, 0.10)", "rgba(5, 150, 105, 0.28)", "#059669", "#065f46"),
    "warning": ("rgba(245, 158, 11, 0.12)", "rgba(217, 119, 6, 0.30)", "#d97706", "#92400e"),
    "error": ("rgba(239, 68, 68, 0.10)", "rgba(220, 38, 38, 0.28)", "#dc2626", "#991b1b"),
    "info": ("rgba(37, 99, 235, 0.10)", "rgba(37, 99, 235, 0.28)", "#2563eb", "#1e40af"),
    "neutral": ("rgba(0, 0, 0, 0.05)", "rgba(0, 0, 0, 0.12)", "#52525b", "#27272a"),
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
        self._color = QColor("#a1a1aa")
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
    """Compact pill badge showing a 6px status dot and high-contrast label."""

    def __init__(
        self,
        text: str = "",
        level: str | InfoLevel = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self._level = _normalize_level(level)
        self._text = str(text or "")

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 3, 10, 3)
        layout.setSpacing(6)

        self._dot = _StatusDot(self)
        self._label = QLabel(self._text, self)
        self._label.setObjectName("StatusPillLabel")
        font = QFont(self._label.font())
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)

        layout.addWidget(self._dot, 0, Qt.AlignVCenter)
        layout.addWidget(self._label, 0, Qt.AlignVCenter)

        self._apply_palette()
        self.setVisible(bool(self._text.strip()))

        try:
            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception:
            pass

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

    def setState(self, level: str | InfoLevel, text: str) -> None:
        self._level = _normalize_level(level)
        self._text = str(text or "")
        self._label.setText(self._text)
        self._apply_palette()
        self.setVisible(bool(self._text.strip()))

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
