"""Responsive two-slot split row layout for desktop GUI headers and banners."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


def _widget_height_for_width(widget: QWidget, avail_w: int) -> int:
    w_clamped = max(avail_w, 1)
    lay = widget.layout()
    if lay is not None and lay.hasHeightForWidth():
        h = lay.heightForWidth(w_clamped)
        if h > 0:
            return h
    if widget.hasHeightForWidth():
        h = widget.heightForWidth(w_clamped)
        if h > 0:
            return h
    return max(widget.sizeHint().height(), 1)


def _item_height_for_width(item: QLayoutItem, avail_w: int) -> int:
    widget = item.widget()
    if widget is not None:
        return _widget_height_for_width(widget, avail_w)
    if item.hasHeightForWidth():
        h = item.heightForWidth(max(avail_w, 1))
        if h > 0:
            return h
    return max(item.sizeHint().height(), 1)


class ResponsiveSplitLayout(QLayout):
    """Two-item split layout: single row when spacious, stacked cleanly when narrow."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        h_spacing: int = 12,
        v_spacing: int = 8,
        left_min_width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = max(0, int(h_spacing))
        self._v_spacing = max(0, int(v_spacing))
        self._left_min_width = left_min_width
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(width, 1), 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def _visible_items(self) -> list[QLayoutItem]:
        out: list[QLayoutItem] = []
        for item in self._items:
            w = item.widget()
            if w is None or not w.isHidden():
                out.append(item)
        return out

    def minimumSize(self) -> QSize:
        visible = self._visible_items()
        m = self.contentsMargins()
        hm = m.left() + m.right()
        vm = m.top() + m.bottom()
        if not visible:
            return QSize(hm, vm)
        if len(visible) == 1:
            ms = visible[0].minimumSize()
            w = ms.width()
            if self._left_min_width is not None:
                w = min(w, self._left_min_width)
            return QSize(w + hm, ms.height() + vm)

        left_item, right_item = visible[0], visible[1]
        left_widget = left_item.widget()
        right_widget = right_item.widget()
        left_min = left_widget.minimumSizeHint() if left_widget is not None else left_item.minimumSize()
        right_min = right_widget.minimumSizeHint() if right_widget is not None else right_item.minimumSize()

        min_left_w = left_min.width()
        if self._left_min_width is not None:
            min_left_w = min(min_left_w, self._left_min_width)

        w = max(min_left_w, right_min.width()) + hm
        h = max(left_min.height(), right_min.height()) + vm
        return QSize(w, h)

    def sizeHint(self) -> QSize:
        visible = self._visible_items()
        m = self.contentsMargins()
        hm = m.left() + m.right()
        vm = m.top() + m.bottom()
        if not visible:
            return QSize(hm, vm)
        if len(visible) == 1:
            sh = visible[0].sizeHint()
            return QSize(sh.width() + hm, sh.height() + vm)

        left_sh = visible[0].sizeHint()
        right_sh = visible[1].sizeHint()
        return QSize(
            left_sh.width() + self._h_spacing + right_sh.width() + hm,
            max(left_sh.height(), right_sh.height()) + vm,
        )

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        avail_w = max(rect.width() - m.left() - m.right(), 1)
        vm = m.top() + m.bottom()

        visible = self._visible_items()
        if not visible:
            return vm

        if len(visible) == 1:
            only = visible[0]
            only_hint = only.sizeHint()
            only_w = avail_w if self._left_min_width is not None else min(avail_w, only_hint.width())
            only_h = _item_height_for_width(only, only_w)
            if apply:
                only.setGeometry(QRect(x, y, only_w, only_h))
            return only_h + vm

        left_item, right_item = visible[0], visible[1]
        right_hint = right_item.sizeHint()
        right_w = min(right_hint.width(), avail_w)
        right_h = right_hint.height()

        if self._left_min_width is not None:
            fits_single_row = avail_w >= self._left_min_width + self._h_spacing + right_w
        else:
            left_hint = left_item.sizeHint()
            fits_single_row = avail_w >= left_hint.width() + self._h_spacing + right_w

        if fits_single_row:
            if self._left_min_width is not None:
                left_w = max(avail_w - self._h_spacing - right_w, 1)
                left_h = _item_height_for_width(left_item, left_w)
            else:
                left_hint = left_item.sizeHint()
                left_w = left_hint.width()
                left_h = left_hint.height()
            row_h = max(left_h, right_h)
            if apply:
                left_item.setGeometry(QRect(x, y + (row_h - left_h) // 2, left_w, left_h))
                right_item.setGeometry(
                    QRect(x + avail_w - right_w, y + (row_h - right_h) // 2, right_w, right_h)
                )
            return row_h + vm

        left_hint = left_item.sizeHint()
        left_w = avail_w if self._left_min_width is not None else min(avail_w, left_hint.width())
        left_h = _item_height_for_width(left_item, left_w)
        if apply:
            left_item.setGeometry(QRect(x, y, left_w, left_h))
            right_item.setGeometry(QRect(x, y + left_h + self._v_spacing, right_w, right_h))
        return left_h + self._v_spacing + right_h + vm


class ResponsiveSplitRow(QWidget):
    """Convenience QWidget wrapping ResponsiveSplitLayout with proper sizeHint delegation."""

    def __init__(
        self,
        left: QWidget,
        right: QWidget,
        *,
        h_spacing: int = 12,
        v_spacing: int = 8,
        left_min_width: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._split_layout = ResponsiveSplitLayout(
            self,
            h_spacing=h_spacing,
            v_spacing=v_spacing,
            left_min_width=left_min_width,
        )
        left.setParent(self)
        right.setParent(self)
        self._split_layout.addWidget(left)
        self._split_layout.addWidget(right)

        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._split_layout.heightForWidth(width)

    def minimumSizeHint(self) -> QSize:
        return self._split_layout.minimumSize()

    def sizeHint(self) -> QSize:
        return self._split_layout.sizeHint()


__all__ = ["ResponsiveSplitLayout", "ResponsiveSplitRow"]
