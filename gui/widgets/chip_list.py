"""WinUI 3 Fluent removable chip and flow container widgets for friends and schedule times."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, FlowLayout, FluentIconBase, isDarkTheme, qconfig

from gui.services.theme import INNER_RADIUS, PILL_RADIUS


class RemovableChip(QFrame):
    """Single WinUI 3 surface tag with label, optional leading icon, and inline remove button."""

    removed = Signal(str)

    def __init__(
        self,
        key: str,
        label: str | None = None,
        icon: FluentIconBase | None = None,
        remove_tooltip: str = "Remove",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RemovableChip")
        self._key = str(key)
        self._label_text = str(label if label is not None else key)
        self._icon = icon

        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 3, 5, 3)
        layout.setSpacing(5)

        self._icon_label: QLabel | None = None
        if icon is not None:
            self._icon_label = QLabel(self)
            self._icon_label.setFixedSize(13, 13)
            self._icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        self.labelWidget = QLabel(self._label_text, self)
        self.labelWidget.setObjectName("RemovableChipLabel")
        font = QFont(self.labelWidget.font())
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        self.labelWidget.setFont(font)
        layout.addWidget(self.labelWidget, 0, Qt.AlignVCenter)

        self.removeBtn = QToolButton(self)
        self.removeBtn.setObjectName("RemovableChipRemoveBtn")
        self.removeBtn.setText("×")
        self.removeBtn.setToolTip(remove_tooltip)
        self.removeBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.removeBtn.setFixedSize(QSize(18, 18))
        btn_font = QFont(self.removeBtn.font())
        btn_font.setPointSize(10)
        btn_font.setWeight(QFont.Weight.DemiBold)
        self.removeBtn.setFont(btn_font)
        self.removeBtn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.removeBtn, 0, Qt.AlignVCenter)

        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception:
            pass

    def _on_remove_clicked(self) -> None:
        self.removed.emit(self._key)

    def _on_theme_changed(self, _theme=None) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        dark = isDarkTheme()
        if dark:
            bg = "rgba(255, 255, 255, 0.065)"
            border = "rgba(255, 255, 255, 0.10)"
            text_hex = "#e6e6e6"
            btn_color = "#a6a6a6"
            btn_hover_bg = "rgba(255, 255, 255, 0.12)"
            btn_hover_fg = "#ffffff"
        else:
            bg = "rgba(0, 0, 0, 0.045)"
            border = "rgba(0, 0, 0, 0.09)"
            text_hex = "#242424"
            btn_color = "#666666"
            btn_hover_bg = "rgba(0, 0, 0, 0.09)"
            btn_hover_fg = "#111111"

        if self._icon_label is not None and self._icon is not None:
            try:
                pm = self._icon.icon().pixmap(13, 13)
                self._icon_label.setPixmap(pm)
            except Exception:
                pass

        self.setStyleSheet(
            f"RemovableChip#RemovableChip {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {PILL_RADIUS}px;"
            f"}}"
        )
        self.labelWidget.setStyleSheet(
            f"QLabel#RemovableChipLabel {{"
            f" color: {text_hex};"
            f" background: transparent;"
            f" border: none;"
            f"}}"
        )
        self.removeBtn.setStyleSheet(
            f"QToolButton#RemovableChipRemoveBtn {{"
            f" color: {btn_color};"
            f" background: transparent;"
            f" border: none;"
            f" border-radius: 3px;"
            f" padding: 0px;"
            f"}}"
            f"QToolButton#RemovableChipRemoveBtn:hover {{"
            f" color: {btn_hover_fg};"
            f" background-color: {btn_hover_bg};"
            f"}}"
        )

    def key(self) -> str:
        return self._key

    def text(self) -> str:
        return self._label_text


class ChipFlowWidget(QFrame):
    """Framed WinUI 3 flow container holding RemovableChip items with an empty-state caption."""

    item_removed = Signal(str)

    def __init__(
        self,
        empty_text: str = "",
        icon: FluentIconBase | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChipFlowWidget")
        self._icon = icon
        self._remove_tooltip = "Remove"
        self._chips: list[RemovableChip] = []

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(10, 8, 10, 8)
        self._outer.setSpacing(4)

        self.emptyLabel = CaptionLabel(empty_text, self)
        self.emptyLabel.setObjectName("ChipFlowEmptyLabel")
        self.emptyLabel.setWordWrap(True)
        self._outer.addWidget(self.emptyLabel)

        self._flowHost = QWidget(self)
        self._flowLayout = FlowLayout(self._flowHost, needAni=False)
        self._flowLayout.setContentsMargins(0, 0, 0, 0)
        self._flowLayout.setHorizontalSpacing(6)
        self._flowLayout.setVerticalSpacing(6)
        self._outer.addWidget(self._flowHost)

        self._sync_empty_state()
        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception:
            pass

    def _on_theme_changed(self, _theme=None) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        if isDarkTheme():
            bg = "rgba(255, 255, 255, 0.025)"
            border = "rgba(255, 255, 255, 0.07)"
            empty_color = "#8e8e93"
        else:
            bg = "rgba(0, 0, 0, 0.02)"
            border = "rgba(0, 0, 0, 0.07)"
            empty_color = "#6e6e73"

        self.setStyleSheet(
            f"ChipFlowWidget#ChipFlowWidget {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        self.emptyLabel.setStyleSheet(
            f"QLabel#ChipFlowEmptyLabel {{"
            f" color: {empty_color};"
            f" background: transparent;"
            f" border: none;"
            f"}}"
        )

    def _sync_empty_state(self) -> None:
        has_items = len(self._chips) > 0
        has_empty_txt = bool(self.emptyLabel.text().strip())
        self.emptyLabel.setVisible(not has_items and has_empty_txt)
        self._flowHost.setVisible(has_items)

    def set_empty_text(self, text: str) -> None:
        self.emptyLabel.setText(str(text or ""))
        self._sync_empty_state()

    def set_remove_tooltip(self, text: str) -> None:
        self._remove_tooltip = str(text or "Remove")
        for chip in self._chips:
            chip.removeBtn.setToolTip(self._remove_tooltip)

    def clear(self) -> None:
        self._flowLayout.takeAllWidgets()
        for chip in self._chips:
            chip.hide()
            chip.deleteLater()
        self._chips.clear()
        self._sync_empty_state()

    def set_items(self, items: Iterable[tuple[str, str] | str]) -> None:
        """Populate chips from `(key, display_label)` tuples or plain strings."""
        normalized: list[tuple[str, str]] = []
        for item in items:
            if isinstance(item, tuple) and len(item) >= 2:
                k, lbl = str(item[0]).strip(), str(item[1]).strip()
            else:
                k = str(item).strip()
                lbl = k
            if k:
                normalized.append((k, lbl or k))

        current_pairs = [(c.key(), c.text()) for c in self._chips]
        if current_pairs == normalized:
            self._sync_empty_state()
            return

        self.clear()
        for key, label in normalized:
            chip = RemovableChip(
                key=key,
                label=label,
                icon=self._icon,
                remove_tooltip=self._remove_tooltip,
                parent=self._flowHost,
            )
            chip.removed.connect(self.item_removed.emit)
            self._flowLayout.addWidget(chip)
            self._chips.append(chip)

        self._sync_empty_state()
        self._flowHost.updateGeometry()
        self.updateGeometry()

    def count(self) -> int:
        return len(self._chips)

    def keys(self) -> list[str]:
        return [c.key() for c in self._chips]

    def labels(self) -> list[str]:
        return [c.text() for c in self._chips]

    def chip_at(self, index: int) -> RemovableChip:
        return self._chips[index]

    def chips(self) -> list[RemovableChip]:
        return list(self._chips)


__all__ = ["ChipFlowWidget", "RemovableChip"]
