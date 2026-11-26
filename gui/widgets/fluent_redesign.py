"""Reusable Windows 11 Fluent Design widgets and dialogs for the redesigned GUI."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    HeaderCardWidget,
    IconWidget,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentPushButton,
    isDarkTheme,
    qconfig,
)

from gui.services.i18n import tr
from gui.services.local_runner import _resolve_asset_path
from gui.services.storage import DEFAULT_PHOTO_PATH
from gui.services.theme import INNER_RADIUS, PILL_RADIUS, escape_qt_mnemonic
from gui.widgets.status_pill import StatusPill


def apply_fluent_dialog_footer(
    dialog: MessageBoxBase,
    *,
    left_widgets: Sequence[QWidget] | None = None,
    right_widgets: Sequence[QWidget] | None = None,
) -> None:
    """Arrange MessageBoxBase footer in authentic Windows 11 right-aligned Fluent layout.

    Replaces the default 50/50 stretched footer with optional compact left utility actions,
    flexible spacer, and compact right-aligned Secondary -> Primary actions.
    """
    lay = dialog.buttonLayout
    while lay.count():
        lay.takeAt(0)

    lay.setSpacing(8)

    left_list = list(left_widgets or ())
    if right_widgets is None:
        right_list: list[QWidget] = [dialog.cancelButton, dialog.yesButton]
    else:
        right_list = list(right_widgets)

    for w in left_list:
        w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay.addWidget(w, 0, Qt.AlignVCenter)

    lay.addStretch(1)

    for w in right_list:
        w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if isinstance(w, (PushButton, PrimaryPushButton)) and w.minimumWidth() < 88:
            w.setMinimumWidth(88)
        lay.addWidget(w, 0, Qt.AlignVCenter)


class StepPill(QFrame):
    """Numbered Fluent step indicator ('done' | 'current' | 'pending')."""

    def __init__(
        self,
        text: str = "",
        state: str = "pending",
        *,
        fixed_width: int | None = None,
        expanding: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FluentStepPill")
        self._state = state if state in ("done", "current", "pending") else "pending"
        self._text = str(text or "")

        if fixed_width is not None:
            self.setFixedWidth(int(fixed_width))
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        elif expanding:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)

        self.glyphLabel = QLabel(self)
        self.glyphLabel.setObjectName("StepPillGlyph")
        g_font = QFont(self.glyphLabel.font())
        g_font.setPointSize(9)
        g_font.setWeight(QFont.Weight.Bold)
        self.glyphLabel.setFont(g_font)

        self.textLabel = QLabel(self._text, self)
        self.textLabel.setObjectName("StepPillText")
        t_font = QFont(self.textLabel.font())
        t_font.setPointSize(9)
        t_font.setWeight(QFont.Weight.DemiBold)
        self.textLabel.setFont(t_font)

        if expanding:
            lay.addStretch(1)
            lay.addWidget(self.glyphLabel, 0, Qt.AlignVCenter)
            lay.addWidget(self.textLabel, 0, Qt.AlignVCenter)
            lay.addStretch(1)
        else:
            lay.addWidget(self.glyphLabel, 0, Qt.AlignVCenter)
            lay.addWidget(self.textLabel, 1, Qt.AlignVCenter)

        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def set_state(self, state: str, text: str | None = None) -> None:
        self._state = state if state in ("done", "current", "pending") else "pending"
        if text is not None:
            self._text = str(text)
            self.textLabel.setText(self._text)
        self._apply_theme()

    def set_text(self, text: str) -> None:
        self._text = str(text)
        self.textLabel.setText(self._text)

    def text(self) -> str:
        return self._text

    def state(self) -> str:
        return self._state

    def _apply_theme(self, _theme=None) -> None:
        dark = isDarkTheme()
        if self._state == "done":
            glyph = "✓"
            if dark:
                bg = "rgba(108, 203, 95, 0.14)"
                border = "rgba(108, 203, 95, 0.30)"
                fg = "#6ccb5f"
            else:
                bg = "#edf7ed"
                border = "#c6e7c6"
                fg = "#0f7b0f"
        elif self._state == "current":
            glyph = "●"
            if dark:
                bg = "rgba(76, 194, 255, 0.14)"
                border = "rgba(76, 194, 255, 0.40)"
                fg = "#4cc2ff"
            else:
                bg = "#eef6ff"
                border = "#9cc7f2"
                fg = "#005fb8"
        else:
            glyph = "●"
            if dark:
                bg = "rgba(255, 255, 255, 0.05)"
                border = "rgba(255, 255, 255, 0.10)"
                fg = "#a6a6a6"
            else:
                bg = "#f3f4f6"
                border = "#e2e5ea"
                fg = "#5f6672"

        self.glyphLabel.setText(glyph)
        self.setStyleSheet(
            f"StepPill#FluentStepPill {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {PILL_RADIUS}px;"
            f"}}"
        )
        lbl_css = f"color: {fg}; background: transparent; border: none;"
        self.glyphLabel.setStyleSheet(lbl_css)
        self.textLabel.setStyleSheet(lbl_css)


class PreflightSummaryTile(QFrame):
    """Equal-width green pass tile shown in the compact All-Pass preflight bar."""

    def __init__(self, title: str = "", detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreflightSummaryTile")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        self.iconLabel = QLabel("✓", self)
        i_font = QFont(self.iconLabel.font())
        i_font.setPointSize(10)
        i_font.setWeight(QFont.Weight.Bold)
        self.iconLabel.setFont(i_font)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.titleLabel = StrongBodyLabel(title, self)
        self.titleLabel.setMinimumWidth(0)
        self.titleLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detailLabel = CaptionLabel(detail, self)
        self.detailLabel.setMinimumWidth(0)
        self.detailLabel.setWordWrap(True)
        self.detailLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(self.titleLabel)
        col.addWidget(self.detailLabel)

        lay.addWidget(self.iconLabel, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)

        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def set_content(self, title: str, detail: str) -> None:
        self.titleLabel.setText(str(title or ""))
        self.detailLabel.setText(str(detail or ""))

    def _apply_theme(self, _theme=None) -> None:
        if isDarkTheme():
            bg = "rgba(108, 203, 95, 0.10)"
            border = "rgba(108, 203, 95, 0.25)"
            fg = "#6ccb5f"
        else:
            bg = "#eef8ef"
            border = "#cbe6cc"
            fg = "#0f7b0f"
        self.setStyleSheet(
            f"PreflightSummaryTile#PreflightSummaryTile {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        fg_primary = "#ffffff" if isDarkTheme() else "#1a1a1a"
        self.iconLabel.setStyleSheet(f"color: {fg}; background: transparent; border: none;")
        self.titleLabel.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        self.detailLabel.setStyleSheet(f"color: {fg}; background: transparent;")


class PreflightRow(QFrame):
    """Inline readiness row with status icon, fixed 116px title, detail, and 1-click fix CTA."""

    fix_clicked = Signal(str)

    def __init__(
        self,
        check_id: str,
        title: str = "",
        detail: str = "",
        status: str = "pass",
        fix_key: str | None = None,
        fix_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FluentPreflightRow")
        self.check_id = check_id
        self._status = status if status in ("pass", "warn", "error") else "pass"
        self._fix_key = fix_key or ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(10)

        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedWidth(16)
        self.iconLabel.setAlignment(Qt.AlignCenter)
        i_font = QFont(self.iconLabel.font())
        i_font.setPointSize(10)
        i_font.setWeight(QFont.Weight.Bold)
        self.iconLabel.setFont(i_font)

        self.titleLabel = StrongBodyLabel(title, self)
        self.titleLabel.setFixedWidth(116)

        self.detailLabel = CaptionLabel(detail, self)
        self.detailLabel.setMinimumWidth(0)
        self.detailLabel.setWordWrap(True)
        self.detailLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.fixBtn = PushButton(escape_qt_mnemonic(fix_label), self)
        self.fixBtn.setFixedHeight(30)
        self.fixBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.fixBtn.clicked.connect(self._on_fix_clicked)
        self.fixBtn.setVisible(bool(self._fix_key and fix_label))

        lay.addWidget(self.iconLabel, 0, Qt.AlignVCenter)
        lay.addWidget(self.titleLabel, 0, Qt.AlignVCenter)
        lay.addWidget(self.detailLabel, 1, Qt.AlignVCenter)
        lay.addWidget(self.fixBtn, 0, Qt.AlignVCenter)

        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def _on_fix_clicked(self) -> None:
        if self._fix_key:
            self.fix_clicked.emit(self._fix_key)

    def update_check(
        self,
        *,
        status: str,
        title: str,
        detail: str,
        fix_key: str | None = None,
        fix_label: str = "",
    ) -> None:
        self._status = status if status in ("pass", "warn", "error") else "pass"
        self._fix_key = fix_key or ""
        self.titleLabel.setText(str(title or ""))
        self.detailLabel.setText(str(detail or ""))
        self.fixBtn.setText(escape_qt_mnemonic(str(fix_label or "")))
        self.fixBtn.setVisible(bool(self._fix_key and fix_label))
        self._apply_theme()

    def _apply_theme(self, _theme=None) -> None:
        dark = isDarkTheme()
        if self._status == "error":
            glyph = "✕"
            if dark:
                bg = "rgba(255, 153, 164, 0.10)"
                border = "rgba(255, 153, 164, 0.34)"
                fg = "#ff99a4"
            else:
                bg = "#fdf2f2"
                border = "#e7b4b8"
                fg = "#b42318"
        elif self._status == "warn":
            glyph = "!"
            if dark:
                bg = "rgba(252, 225, 0, 0.08)"
                border = "rgba(252, 225, 0, 0.28)"
                fg = "#fce100"
            else:
                bg = "#fef9ec"
                border = "#e6ca86"
                fg = "#9d5d00"
        else:
            glyph = "✓"
            if dark:
                bg = "rgba(108, 203, 95, 0.10)"
                border = "rgba(108, 203, 95, 0.28)"
                fg = "#6ccb5f"
            else:
                bg = "#eef8ef"
                border = "#cbe6cc"
                fg = "#0f7b0f"

        self.iconLabel.setText(glyph)
        self.setStyleSheet(
            f"PreflightRow#FluentPreflightRow {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        fg_primary = "#ffffff" if isDarkTheme() else "#1a1a1a"
        self.iconLabel.setStyleSheet(f"color: {fg}; background: transparent; border: none;")
        self.titleLabel.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        self.detailLabel.setStyleSheet(f"color: {fg}; background: transparent;")


def _draw_cover_pixmap(painter: QPainter, target: QRectF, pixmap: QPixmap) -> None:
    """Paint `pixmap` aspect-fill (object-fit: cover) centered inside `target`."""
    if pixmap.isNull():
        return
    tw, th = target.width(), target.height()
    pw, ph = pixmap.width(), pixmap.height()
    if tw <= 0 or th <= 0 or pw <= 0 or ph <= 0:
        return
    scale = max(tw / pw, th / ph)
    dw, dh = pw * scale, ph * scale
    dx = target.x() + (tw - dw) / 2.0
    dy = target.y() + (th - dh) / 2.0
    painter.drawPixmap(QRectF(dx, dy, dw, dh), pixmap, QRectF(0, 0, pw, ph))


class _AvatarCircle(QWidget):
    """Circular TikTok user avatar rendering real photo bytes or tinted initial glyph."""

    def __init__(self, diameter: int = 24, bg_hex: str = "#2b313e", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = max(14, int(diameter))
        self._pixmap = QPixmap()
        self._initial = "U"
        self._bg_color = QColor(bg_hex)
        self.setFixedSize(self._diameter, self._diameter)

    def set_diameter(self, diameter: int) -> None:
        d = max(14, int(diameter))
        if d != self._diameter:
            self._diameter = d
            self.setFixedSize(d, d)
            self.update()

    def set_identity(self, initial: str, *, bg_hex: str | None = None, pixmap: QPixmap | None = None) -> None:
        ch = (str(initial or "").strip().lstrip("@")[:1] or "U").upper()
        self._initial = ch
        if bg_hex:
            self._bg_color = QColor(bg_hex)
        if pixmap is not None:
            self._pixmap = pixmap
        self.update()

    def set_pixmap_bytes(self, raw: bytes | None) -> None:
        pm = QPixmap()
        if raw:
            pm.loadFromData(raw)
        self._pixmap = pm
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        clip = QPainterPath()
        clip.addEllipse(rect)
        painter.setClipPath(clip)
        if not self._pixmap.isNull():
            _draw_cover_pixmap(painter, rect, self._pixmap)
        else:
            painter.fillRect(rect, self._bg_color)
            f = QFont(self.font())
            f.setPixelSize(max(9, int(self.height() * 0.46)))
            f.setWeight(QFont.Weight.DemiBold)
            painter.setFont(f)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignCenter, self._initial)


class _TikTokVideoCard(QWidget):
    """1:1 replica of TikTok Web DivVideoContainer portrait video share card."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cover = QPixmap()
        self._avatar = QPixmap()
        self._author = "tiktok.com"
        self._share_times = 2
        self._card_w = 124
        self._card_h = 196
        self.setFixedSize(self._card_w, self._card_h)

    def set_card_size(self, w: int, h: int) -> None:
        nw, nh = max(72, int(w)), max(108, int(h))
        if (nw, nh) != (self._card_w, self._card_h):
            self._card_w, self._card_h = nw, nh
            self.setFixedSize(nw, nh)
            self.update()

    def set_state(
        self,
        *,
        author: str,
        share_times: int,
        cover_bytes: bytes | None = None,
        avatar_bytes: bytes | None = None,
    ) -> None:
        self._author = str(author or "").strip() or "tiktok.com"
        self._share_times = max(1, int(share_times or 1))
        if cover_bytes is not None:
            pm = QPixmap()
            if cover_bytes:
                pm.loadFromData(cover_bytes)
            self._cover = pm
        if avatar_bytes is not None:
            av = QPixmap()
            if avatar_bytes:
                av.loadFromData(avatar_bytes)
            self._avatar = av
        self.update()

    def clear_media(self) -> None:
        if not self._cover.isNull() or not self._avatar.isNull():
            self._cover = QPixmap()
            self._avatar = QPixmap()
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        dark = isDarkTheme()
        w = float(self.width())
        h = float(self.height())
        stack_off = 4.0 if self._share_times >= 2 else 0.0
        cw = max(10.0, w - stack_off)
        ch = max(10.0, h - stack_off)

        # Stacked second-card depth hint when sending >=2 videos
        if self._share_times >= 2:
            back_rect = QRectF(stack_off, stack_off, cw - 0.5, ch - 0.5)
            back_path = QPainterPath()
            back_path.addRoundedRect(back_rect, 8.0, 8.0)
            painter.fillPath(back_path, QColor(255, 255, 255, 24) if dark else QColor(22, 24, 35, 16))
            painter.setPen(QPen(QColor(255, 255, 255, 45) if dark else QColor(22, 24, 35, 36), 1.0))
            painter.drawPath(back_path)

        card_rect = QRectF(0.5, 0.5, cw - 1.0, ch - 1.0)
        clip = QPainterPath()
        clip.addRoundedRect(card_rect, 8.0, 8.0)
        painter.save()
        painter.setClipPath(clip)

        # Cover image or sleek TikTok dark gradient fallback
        if not self._cover.isNull():
            _draw_cover_pixmap(painter, card_rect, self._cover)
        else:
            grad = QLinearGradient(0, 0, 0, ch)
            grad.setColorAt(0.0, QColor("#2a2f3d"))
            grad.setColorAt(0.55, QColor("#1b1f29"))
            grad.setColorAt(1.0, QColor("#11131a"))
            painter.fillRect(card_rect, grad)

        # Bottom contrast scrim matching TikTok Web video share card
        scrim = QLinearGradient(0, ch * 0.52, 0, ch)
        scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 190))
        painter.fillRect(QRectF(0, ch * 0.52, cw, ch * 0.48), scrim)

        # Centered TikTok white play triangle with subtle drop shadow
        cx, cy = cw / 2.0, ch / 2.0 - 2.0
        pr = max(8.0, min(cw, ch) * 0.115)
        tri = QPolygonF([
            QPointF(cx - pr * 0.55, cy - pr * 0.78),
            QPointF(cx + pr * 0.85, cy),
            QPointF(cx - pr * 0.55, cy + pr * 0.78),
        ])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawPolygon(tri.translated(0.8, 1.2))
        painter.setBrush(QColor(255, 255, 255, 238))
        painter.drawPolygon(tri)

        # Bottom-left creator avatar + semiBold nickname
        pad_x = 8.0
        pad_b = 7.0
        av_d = 18.0 if cw >= 110 else 14.0
        av_rect = QRectF(pad_x, ch - pad_b - av_d, av_d, av_d)
        av_clip = QPainterPath()
        av_clip.addEllipse(av_rect)
        painter.save()
        painter.setClipPath(av_clip)
        if not self._avatar.isNull():
            _draw_cover_pixmap(painter, av_rect, self._avatar)
        else:
            painter.fillRect(av_rect, QColor("#fe2c55"))
            af = QFont(self.font())
            af.setPixelSize(max(8, int(av_d * 0.5)))
            af.setWeight(QFont.Weight.Bold)
            painter.setFont(af)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(av_rect, Qt.AlignCenter, (self._author.lstrip("@")[:1] or "T").upper())
        painter.restore()

        name_x = pad_x + av_d + 5.0
        name_w = max(12.0, cw - name_x - pad_x)
        nf = QFont(self.font())
        nf.setPixelSize(11 if cw >= 110 else 10)
        nf.setWeight(QFont.Weight.DemiBold)
        painter.setFont(nf)
        fm = painter.fontMetrics()
        elided = fm.elidedText(self._author, Qt.ElideRight, int(name_w))
        painter.setPen(QColor(255, 255, 255, 238))
        painter.drawText(
            QRectF(name_x, av_rect.y(), name_w, av_d),
            Qt.AlignLeft | Qt.AlignVCenter,
            elided,
        )

        # Subtle top-right xN badge when sharing >= 2 videos
        if self._share_times >= 2:
            badge_txt = f"×{self._share_times}"
            bf = QFont(self.font())
            bf.setPixelSize(10)
            bf.setWeight(QFont.Weight.Bold)
            painter.setFont(bf)
            bw = painter.fontMetrics().horizontalAdvance(badge_txt) + 12.0
            bh = 18.0
            b_rect = QRectF(cw - bw - 6.0, 6.0, bw, bh)
            b_path = QPainterPath()
            b_path.addRoundedRect(b_rect, 9.0, 9.0)
            painter.fillPath(b_path, QColor(14, 16, 22, 195))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
            painter.drawPath(b_path)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(b_rect, Qt.AlignCenter, badge_txt)

        painter.restore()
        # Crisp 1px TikTok border
        painter.setPen(QPen(QColor(255, 255, 255, 34) if dark else QColor(22, 24, 35, 31), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(clip)


class _TikTokPhotoCard(QWidget):
    """1:1 replica of TikTok Web DivImageVideoMessageContainer full-bleed rounded photo."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._max_w = 124
        self._landscape_w = 160
        self._max_h = 152
        self._recalc_size()

    def set_bounds(self, *, portrait_w: int, landscape_w: int, max_h: int) -> None:
        self._max_w = max(72, int(portrait_w))
        self._landscape_w = max(self._max_w, int(landscape_w))
        self._max_h = max(72, int(max_h))
        self._recalc_size()

    def set_photo_path(self, path: str | None) -> None:
        pm = QPixmap()
        if path and Path(path).is_file():
            pm.load(str(path))
        self._pixmap = pm
        self._recalc_size()
        self.update()

    def _recalc_size(self) -> None:
        if self._pixmap.isNull() or self._pixmap.height() <= 0:
            w = self._max_w
            h = min(self._max_h, int(round(self._max_w * (160.0 / 180.0))))
        else:
            ratio = self._pixmap.width() / float(self._pixmap.height())
            if ratio < 1.0:
                w = self._max_w
                h = min(self._max_h, int(round(w / max(ratio, 0.56))))
            elif ratio < 1.2:
                w = self._max_w
                h = min(self._max_h, int(round(w * (160.0 / 180.0))))
            else:
                w = self._landscape_w
                h = min(self._max_h, int(round(w * (180.0 / 260.0))))
        self.setFixedSize(max(64, int(w)), max(64, int(h)))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        dark = isDarkTheme()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 8.0, 8.0)

        painter.save()
        painter.setClipPath(clip)
        if not self._pixmap.isNull():
            _draw_cover_pixmap(painter, rect, self._pixmap)
        else:
            painter.fillRect(rect, QColor(255, 255, 255, 33) if dark else QColor(0, 0, 0, 13))
            painter.setPen(QColor("#a0a6b2") if dark else QColor("#616773"))
            painter.drawText(rect, Qt.AlignCenter, "IMG")
        painter.restore()

        painter.setPen(QPen(QColor(255, 255, 255, 34) if dark else QColor(22, 24, 35, 31), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(clip)


class DMPreviewPanel(QWidget):
    """Equal-height right-column 1:1 TikTok Web DM Preview & live Streak Pet Tasks breakdown."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stacked = False
        self._density_tier = ""
        self._last_args: dict[str, Any] = {}
        self.setMinimumWidth(196)
        self.setMaximumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # Card 1: 1:1 TikTok Web /messages preview container
        self.previewCard = HeaderCardWidget(self)
        self.previewCard.setTitle(tr("content.dm_preview_title"))
        self.previewCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.previewCard.setMinimumWidth(0)
        self.previewCard.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.previewFriendChip = StatusPill("@friend", "neutral", self.previewCard, show_dot=False)
        self.previewCard.headerLayout.addStretch(1)
        self.previewCard.headerLayout.addWidget(self.previewFriendChip, 0, Qt.AlignVCenter)

        self.chatCanvas = QFrame(self.previewCard)
        self.chatCanvas.setObjectName("DMPreviewCanvas")
        self.chatCanvas.setMinimumHeight(230)
        self.chatCanvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas_lay = QVBoxLayout(self.chatCanvas)
        canvas_lay.setContentsMargins(0, 0, 0, 0)
        canvas_lay.setSpacing(0)

        # 1a. DivChatHeader: recipient avatar + display name & @handle
        self.chatHeader = QFrame(self.chatCanvas)
        self.chatHeader.setObjectName("DMChatHeader")
        hdr_lay = QHBoxLayout(self.chatHeader)
        hdr_lay.setContentsMargins(12, 9, 12, 9)
        hdr_lay.setSpacing(9)
        self.recipientAvatar = _AvatarCircle(30, "#3b4252", self.chatHeader)
        hdr_text = QVBoxLayout()
        hdr_text.setContentsMargins(0, 0, 0, 0)
        hdr_text.setSpacing(0)
        self.chatHeaderName = StrongBodyLabel("friend", self.chatHeader)
        self.chatHeaderName.setMinimumWidth(0)
        self.chatHeaderHandle = CaptionLabel("@friend", self.chatHeader)
        self.chatHeaderHandle.setMinimumWidth(0)
        hdr_text.addWidget(self.chatHeaderName)
        hdr_text.addWidget(self.chatHeaderHandle)
        hdr_lay.addWidget(self.recipientAvatar, 0, Qt.AlignVCenter)
        hdr_lay.addLayout(hdr_text, 1)
        canvas_lay.addWidget(self.chatHeader, 0)

        # 1b. DivChatMain: scrollable right-aligned TikTok outgoing message rows
        self.chatScroll = QScrollArea(self.chatCanvas)
        self.chatScroll.setObjectName("DMChatScroll")
        self.chatScroll.setFrameShape(QFrame.NoFrame)
        self.chatScroll.setWidgetResizable(True)
        self.chatScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chatScroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chatScroll.setStyleSheet("QScrollArea#DMChatScroll { background: transparent; border: none; }")

        self.chatThreadWidget = QWidget(self.chatScroll)
        self.chatThreadWidget.setStyleSheet("background: transparent;")
        self._thread_lay = QVBoxLayout(self.chatThreadWidget)
        self._thread_lay.setContentsMargins(10, 10, 10, 10)
        self._thread_lay.setSpacing(8)

        # Outgoing row 1: Text message bubble + sender mini-avatar
        self.msgRowWidget = QWidget(self.chatThreadWidget)
        msg_row = QHBoxLayout(self.msgRowWidget)
        msg_row.setContentsMargins(0, 0, 0, 0)
        msg_row.setSpacing(8)
        msg_row.addStretch(1)
        self.msgBubble = QLabel("🔥 Daily Streak", self.msgRowWidget)
        self.msgBubble.setObjectName("DMMsgBubble")
        self.msgBubble.setWordWrap(True)
        self.msgBubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.msgAvatar = _AvatarCircle(22, "#fe2c55", self.msgRowWidget)
        msg_row.addWidget(self.msgBubble, 0, Qt.AlignTop)
        msg_row.addWidget(self.msgAvatar, 0, Qt.AlignTop)
        self._thread_lay.addWidget(self.msgRowWidget)

        # Outgoing row 2: Portrait Video Share Card + sender mini-avatar
        self.videoRowWidget = QWidget(self.chatThreadWidget)
        self.videoBubble = self.videoRowWidget
        vid_row = QHBoxLayout(self.videoRowWidget)
        vid_row.setContentsMargins(0, 0, 0, 0)
        vid_row.setSpacing(8)
        vid_row.addStretch(1)
        self.videoCard = _TikTokVideoCard(self.videoRowWidget)
        self.videoAvatar = _AvatarCircle(22, "#fe2c55", self.videoRowWidget)
        vid_row.addWidget(self.videoCard, 0, Qt.AlignTop)
        vid_row.addWidget(self.videoAvatar, 0, Qt.AlignTop)
        self._thread_lay.addWidget(self.videoRowWidget)

        # Outgoing row 3: Full-bleed rounded Photo Card + sender mini-avatar
        self.photoRowWidget = QWidget(self.chatThreadWidget)
        self.photoBubble = self.photoRowWidget
        ph_row = QHBoxLayout(self.photoRowWidget)
        ph_row.setContentsMargins(0, 0, 0, 0)
        ph_row.setSpacing(8)
        ph_row.addStretch(1)
        self.photoCard = _TikTokPhotoCard(self.photoRowWidget)
        self.photoAvatar = _AvatarCircle(22, "#fe2c55", self.photoRowWidget)
        ph_row.addWidget(self.photoCard, 0, Qt.AlignTop)
        ph_row.addWidget(self.photoAvatar, 0, Qt.AlignTop)
        self._thread_lay.addWidget(self.photoRowWidget)

        self._thread_lay.addStretch(1)
        self.chatScroll.setWidget(self.chatThreadWidget)
        canvas_lay.addWidget(self.chatScroll, 1)

        # 1c. DivChatBottom: subtle "Send a message..." composer pill
        self.chatComposer = QFrame(self.chatCanvas)
        self.chatComposer.setObjectName("DMChatBottom")
        cmp_outer = QVBoxLayout(self.chatComposer)
        cmp_outer.setContentsMargins(10, 8, 10, 9)
        cmp_outer.setSpacing(0)
        self.composerPill = QFrame(self.chatComposer)
        self.composerPill.setObjectName("DMComposerPill")
        pill_lay = QHBoxLayout(self.composerPill)
        pill_lay.setContentsMargins(10, 6, 8, 6)
        pill_lay.setSpacing(6)
        self.composerPlaceholder = CaptionLabel(tr("content.dm_composer_placeholder"), self.composerPill)
        self.composerPlaceholder.setMinimumWidth(0)
        self.composerPlaceholder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.composerPhotoIcon = IconWidget(FIF.PHOTO, self.composerPill)
        self.composerPhotoIcon.setFixedSize(13, 13)
        self.composerEmojiLabel = CaptionLabel("☺", self.composerPill)
        pill_lay.addWidget(self.composerPlaceholder, 1, Qt.AlignVCenter)
        pill_lay.addWidget(self.composerPhotoIcon, 0, Qt.AlignVCenter)
        pill_lay.addWidget(self.composerEmojiLabel, 0, Qt.AlignVCenter)
        cmp_outer.addWidget(self.composerPill)
        canvas_lay.addWidget(self.chatComposer, 0)

        self.previewCard.viewLayout.setContentsMargins(10, 8, 10, 10)
        self.previewCard.viewLayout.addWidget(self.chatCanvas)
        root.addWidget(self.previewCard, 1)

        # Card 2: Streak Pet Tasks (reactive point calculator)
        self.petCard = HeaderCardWidget(self)
        self.petCard.setTitle(tr("content.pet_tasks_title"))
        self.petCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.petCard.setMinimumWidth(0)
        self.petTotalBadge = StatusPill(tr("content.pet_pts_short", pts=7), "success", self.petCard)
        self.petCard.headerLayout.addStretch(1)
        self.petCard.headerLayout.addWidget(self.petTotalBadge, 0, Qt.AlignVCenter)

        pet_lay = QVBoxLayout()
        pet_lay.setContentsMargins(0, 0, 0, 0)
        pet_lay.setSpacing(6)
        self._pet_rows: dict[str, tuple[QLabel, BodyLabel, CaptionLabel]] = {}
        for key, lbl_key, pt_key in (
            ("msg", "content.pet_task_msg", "content.pet_pt_1"),
            ("video", "content.pet_task_video", "content.pet_pt_2"),
            ("photo", "content.pet_task_photo", "content.pet_pt_4"),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            ic = QLabel("✓", self.petCard)
            ic.setFixedWidth(14)
            lbl = BodyLabel(tr(lbl_key), self.petCard)
            pts = CaptionLabel(tr(pt_key), self.petCard)
            row.addWidget(ic, 0, Qt.AlignVCenter)
            row.addWidget(lbl, 1, Qt.AlignVCenter)
            row.addWidget(pts, 0, Qt.AlignVCenter)
            pet_lay.addLayout(row)
            self._pet_rows[key] = (ic, lbl, pts)
        self.petCard.viewLayout.addLayout(pet_lay)
        root.addWidget(self.petCard, 0)

        self._apply_density_tier(296, 460)
        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def set_stacked_mode(self, stacked: bool) -> None:
        self._stacked = bool(stacked)
        if self._stacked:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.setMinimumWidth(196)
            self.setMaximumWidth(320)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._apply_density_tier(
            self.width() if self.width() >= 180 else 296,
            self.height() if self.height() >= 240 else 460,
        )

    def sizeHint(self) -> QSize:
        return QSize(296, 480) if not self._stacked else QSize(520, 420)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_density_tier(event.size().width(), event.size().height())

    def _apply_density_tier(self, width: int, height: int) -> None:
        w = max(180, int(width or self.width() or 296))
        h = max(240, int(height or self.height() or 460))
        if w <= 235:
            tier = "cramped"
        elif w < 275 or h < 395:
            tier = "compact"
        else:
            tier = "spacious"

        show_cmp = tier == "spacious" or (tier == "compact" and h >= 420)
        self.chatComposer.setVisible(show_cmp)

        if tier == self._density_tier and w > 235:
            # Keep msgBubble maxWidth synced cleanly when panel widens gently
            av_reserve = 30 if self.msgAvatar.isVisible() else 0
            self.msgBubble.setMaximumWidth(max(120, min(260, w - 58 - av_reserve)))
            return
        self._density_tier = tier

        if tier == "cramped":
            self.recipientAvatar.set_diameter(22)
            for av in (self.msgAvatar, self.videoAvatar, self.photoAvatar):
                av.hide()
            self.videoCard.set_card_size(88, 132)
            self.photoCard.set_bounds(portrait_w=88, landscape_w=120, max_h=96)
            self.msgBubble.setMaximumWidth(max(115, w - 52))
            self._thread_lay.setContentsMargins(8, 8, 8, 8)
            self._thread_lay.setSpacing(6)
        elif tier == "compact":
            self.recipientAvatar.set_diameter(24)
            for av in (self.msgAvatar, self.videoAvatar, self.photoAvatar):
                av.set_diameter(18)
                av.show()
            self.videoCard.set_card_size(96, 148)
            self.photoCard.set_bounds(portrait_w=96, landscape_w=126, max_h=108)
            self.msgBubble.setMaximumWidth(max(125, w - 76))
            self._thread_lay.setContentsMargins(9, 9, 9, 9)
            self._thread_lay.setSpacing(7)
        else:
            self.recipientAvatar.set_diameter(30)
            for av in (self.msgAvatar, self.videoAvatar, self.photoAvatar):
                av.set_diameter(22)
                av.show()
            self.videoCard.set_card_size(112, 162)
            self.photoCard.set_bounds(portrait_w=112, landscape_w=150, max_h=128)
            self.msgBubble.setMaximumWidth(max(145, min(250, w - 84)))
            self._thread_lay.setContentsMargins(10, 10, 10, 10)
            self._thread_lay.setSpacing(8)

    def _apply_theme(self, _theme=None) -> None:
        dark = isDarkTheme()
        if dark:
            # Exact TikTok Web /messages Dark tokens (UIPageFlat2, UIShapeSecondaryMuted, LineSecondary)
            canvas_bg = "#1e1e1e"
            canvas_border = "rgba(255, 255, 255, 0.12)"
            line_sec = "rgba(255, 255, 255, 0.12)"
            msg_bg = "#00a2c9"
            msg_border = "none"
            msg_fg = "#ffffff"
            hdr_name_fg = "#ffffff"
            hdr_sub_fg = "rgba(255, 255, 255, 0.60)"
            comp_bg = "rgba(255, 255, 255, 0.10)"
            comp_fg = "rgba(255, 255, 255, 0.48)"
        else:
            # Exact TikTok Web /messages Light tokens (UIPageFlat1, Neutral 6% / 12%, #161823)
            canvas_bg = "#ffffff"
            canvas_border = "rgba(22, 24, 35, 0.12)"
            line_sec = "rgba(22, 24, 35, 0.12)"
            msg_bg = "rgba(22, 24, 35, 0.06)"
            msg_border = "1px solid rgba(22, 24, 35, 0.12)"
            msg_fg = "#161823"
            hdr_name_fg = "#161823"
            hdr_sub_fg = "rgba(22, 24, 35, 0.55)"
            comp_bg = "rgba(22, 24, 35, 0.06)"
            comp_fg = "rgba(22, 24, 35, 0.50)"

        self.chatCanvas.setStyleSheet(
            f"QFrame#DMPreviewCanvas {{"
            f" background-color: {canvas_bg};"
            f" border: 1px solid {canvas_border};"
            f" border-radius: 8px;"
            f"}}"
        )
        self.chatHeader.setStyleSheet(
            f"QFrame#DMChatHeader {{"
            f" background: transparent;"
            f" border: none;"
            f" border-bottom: 1px solid {line_sec};"
            f"}}"
        )
        self.chatHeaderName.setStyleSheet(f"color: {hdr_name_fg}; background: transparent;")
        self.chatHeaderHandle.setStyleSheet(f"color: {hdr_sub_fg}; background: transparent;")
        self.msgBubble.setStyleSheet(
            f"QLabel#DMMsgBubble {{"
            f" background-color: {msg_bg};"
            f" color: {msg_fg};"
            f" border: {msg_border};"
            f" border-radius: 8px;"
            f" padding: 7px 12px;"
            f" font-size: 13px;"
            f"}}"
        )
        self.chatComposer.setStyleSheet(
            f"QFrame#DMChatBottom {{"
            f" background: transparent;"
            f" border: none;"
            f" border-top: 1px solid {line_sec};"
            f"}}"
        )
        self.composerPill.setStyleSheet(
            f"QFrame#DMComposerPill {{"
            f" background-color: {comp_bg};"
            f" border: none;"
            f" border-radius: 8px;"
            f"}}"
        )
        self.composerPlaceholder.setStyleSheet(f"color: {comp_fg}; background: transparent;")
        self.composerEmojiLabel.setStyleSheet(f"color: {comp_fg}; background: transparent;")
        sb_handle = "rgba(255, 255, 255, 0.24)" if dark else "rgba(22, 24, 35, 0.22)"
        sb_hover = "rgba(255, 255, 255, 0.40)" if dark else "rgba(22, 24, 35, 0.36)"
        self.chatScroll.setStyleSheet(
            "QScrollArea#DMChatScroll { background: transparent; border: none; }"
            " QScrollArea#DMChatScroll QScrollBar:vertical {"
            "  background: transparent; width: 5px; margin: 4px 2px 4px 0px; border: none;"
            " }"
            f" QScrollArea#DMChatScroll QScrollBar::handle:vertical {{"
            f"  background: {sb_handle}; min-height: 24px; border-radius: 2px;"
            f" }}"
            f" QScrollArea#DMChatScroll QScrollBar::handle:vertical:hover {{ background: {sb_hover}; }}"
            " QScrollArea#DMChatScroll QScrollBar::add-line:vertical,"
            " QScrollArea#DMChatScroll QScrollBar::sub-line:vertical { height: 0px; background: transparent; }"
            " QScrollArea#DMChatScroll QScrollBar::add-page:vertical,"
            " QScrollArea#DMChatScroll QScrollBar::sub-page:vertical { background: transparent; }"
        )
        self.videoCard.update()
        self.photoCard.update()
        if self._last_args:
            self._refresh_pet_row_styles(
                msg_ok=bool(self._last_args.get("_msg_ok")),
                vid_ok=bool(self._last_args.get("_vid_ok")),
                photo_ok=bool(self._last_args.get("_photo_ok")),
            )

    def _refresh_pet_row_styles(self, *, msg_ok: bool, vid_ok: bool, photo_ok: bool) -> None:
        dark = isDarkTheme()
        fg_primary = "#ffffff" if dark else "#1a1a1a"
        ok_fg = "#6ccb5f" if dark else "#0f7b0f"
        off_fg = "#7a7a7a" if dark else "#8c939f"
        for key, active in (("msg", msg_ok), ("video", vid_ok), ("photo", photo_ok)):
            ic, lbl, pts = self._pet_rows[key]
            ic.setText("✓" if active else "○")
            color = ok_fg if active else off_fg
            ic.setStyleSheet(f"color: {color}; font-weight: 700; background: transparent;")
            pts.setStyleSheet(f"color: {color}; font-weight: 600; background: transparent;")
            if active:
                lbl.setStyleSheet(f"color: {fg_primary}; background: transparent;")
            else:
                lbl.setStyleSheet(f"color: {off_fg}; background: transparent;")

    def set_video_media(
        self,
        *,
        cover_bytes: bytes | None = None,
        avatar_bytes: bytes | None = None,
        author_nickname: str = "",
    ) -> None:
        author = str(author_nickname or "").strip() or (
            str(self._last_args.get("video_author") or "").strip() or "tiktok.com"
        )
        shares = max(1, int(self._last_args.get("share_times") or 2))
        self.videoCard.set_state(
            author=author,
            share_times=shares,
            cover_bytes=cover_bytes,
            avatar_bytes=avatar_bytes,
        )

    def clear_video_media(self) -> None:
        self.videoCard.clear_media()

    def update_preview(
        self,
        *,
        message: str,
        video_enabled: bool,
        video_url: str,
        share_times: int,
        photo_enabled: bool,
        photo_path: str,
        friend_handle: str = "",
        video_author: str = "",
        video_verified: bool = False,
        sender_handle: str = "",
    ) -> int:
        clean_friend = str(friend_handle or "").strip().lstrip("@")
        handle = f"@{clean_friend}" if clean_friend else "@friend"
        disp_name = clean_friend or "friend"
        self.previewFriendChip.setState("neutral", handle, show_dot=False)
        self.chatHeaderName.setText(disp_name)
        self.chatHeaderHandle.setText(handle)
        self.recipientAvatar.set_identity(disp_name, bg_hex="#3b4252")

        sender_init = (str(sender_handle or "").strip().lstrip("@")[:1] or "M").upper()
        for av in (self.msgAvatar, self.videoAvatar, self.photoAvatar):
            av.set_identity(sender_init, bg_hex="#fe2c55")

        msg_clean = str(message or "").strip()
        self.msgBubble.setText(msg_clean or "🔥 Daily Streak")

        shares = max(1, int(share_times or 1))
        if video_enabled:
            self.videoRowWidget.show()
            author_disp = str(video_author or "").strip().lstrip("@")
            if author_disp:
                author_label = author_disp
            elif str(video_url or "").strip():
                short = str(video_url).strip().replace("https://", "").replace("www.", "")
                m_at = short.split("@", 1)[1].split("/", 1)[0] if "@" in short else ""
                author_label = m_at or short[:22]
            else:
                author_label = "tiktok.com"
            self.videoCard.set_state(author=author_label, share_times=shares)
        else:
            self.videoRowWidget.hide()

        if photo_enabled:
            self.photoRowWidget.show()
            custom = str(photo_path or "").strip()
            target = _resolve_asset_path(custom or DEFAULT_PHOTO_PATH)
            self.photoCard.set_photo_path(target)
        else:
            self.photoRowWidget.hide()

        msg_ok = bool(msg_clean)
        vid_ok = bool(video_enabled and shares >= 2)
        photo_ok = bool(photo_enabled)
        total_pts = (1 if msg_ok else 0) + (2 if vid_ok else 0) + (4 if photo_ok else 0)

        self._last_args = {
            "message": message,
            "video_enabled": video_enabled,
            "video_url": video_url,
            "share_times": share_times,
            "photo_enabled": photo_enabled,
            "photo_path": photo_path,
            "friend_handle": friend_handle,
            "video_author": video_author,
            "video_verified": video_verified,
            "sender_handle": sender_handle,
            "_msg_ok": msg_ok,
            "_vid_ok": vid_ok,
            "_photo_ok": photo_ok,
        }

        level = "success" if total_pts >= 5 else ("info" if total_pts >= 1 else "warning")
        self.petTotalBadge.setState(level, tr("content.pet_pts_short", pts=total_pts))
        self._refresh_pet_row_styles(msg_ok=msg_ok, vid_ok=vid_ok, photo_ok=photo_ok)
        return total_pts

    def retranslate(self) -> None:
        self.previewCard.setTitle(tr("content.dm_preview_title"))
        self.petCard.setTitle(tr("content.pet_tasks_title"))
        self.composerPlaceholder.setText(tr("content.dm_composer_placeholder"))
        self._pet_rows["msg"][1].setText(tr("content.pet_task_msg"))
        self._pet_rows["msg"][2].setText(tr("content.pet_pt_1"))
        self._pet_rows["video"][1].setText(tr("content.pet_task_video"))
        self._pet_rows["video"][2].setText(tr("content.pet_pt_2"))
        self._pet_rows["photo"][1].setText(tr("content.pet_task_photo"))
        self._pet_rows["photo"][2].setText(tr("content.pet_pt_4"))
        if self._last_args:
            args = {k: v for k, v in self._last_args.items() if not k.startswith("_")}
            self.update_preview(**args)


class ResetConfirmDialog(MessageBoxBase):
    """Safe destructive reset modal clearly distinguishing local wipe vs. remote GitHub safety."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(tr("settings.reset_modal_title"), self)
        self.viewLayout.addWidget(self.titleLabel)

        self.scopeBox = QFrame(self)
        self.scopeBox.setObjectName("ResetScopeBox")
        box_lay = QVBoxLayout(self.scopeBox)
        box_lay.setContentsMargins(14, 12, 14, 12)
        box_lay.setSpacing(8)

        dark = isDarkTheme()
        del_fg = "#ff99a4" if dark else "#b42318"
        safe_fg = "#6ccb5f" if dark else "#0f7b0f"
        bg = "rgba(255, 255, 255, 0.04)" if dark else "#f5f7fa"
        border = "rgba(255, 255, 255, 0.09)" if dark else "#e2e6ec"
        self.scopeBox.setStyleSheet(
            f"QFrame#ResetScopeBox {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )

        self.scopeLabels: list[BodyLabel] = []
        for glyph, key, color, bold in (
            ("✕", "settings.reset_scope_1", del_fg, False),
            ("✕", "settings.reset_scope_2", del_fg, False),
            ("✕", "settings.reset_scope_3", del_fg, False),
            ("✓", "settings.reset_scope_safe", safe_fg, True),
        ):
            lbl = BodyLabel(f"{glyph}  {tr(key)}", self.scopeBox)
            lbl.setWordWrap(True)
            weight = "600" if bold else "500"
            lbl.setStyleSheet(f"color: {color}; font-weight: {weight}; background: transparent;")
            box_lay.addWidget(lbl)
            self.scopeLabels.append(lbl)

        self.viewLayout.addWidget(self.scopeBox)

        self.cancelButton.setText(escape_qt_mnemonic(tr("common.cancel")))
        self.yesButton.setText(escape_qt_mnemonic(tr("settings.reset_confirm_btn")))
        btn_bg = "#d13438" if dark else "#c42b1c"
        btn_hover = "#e04347" if dark else "#a92417"
        btn_press = "#b52a2e" if dark else "#8e1e13"
        self.yesButton.setStyleSheet(
            f"PrimaryPushButton {{"
            f" background-color: {btn_bg};"
            f" border: 1px solid {btn_bg};"
            f" border-radius: 5px;"
            f" color: #ffffff;"
            f" padding: 5px 14px;"
            f"}}"
            f"PrimaryPushButton:hover {{ background-color: {btn_hover}; border-color: {btn_hover}; }}"
            f"PrimaryPushButton:pressed {{ background-color: {btn_press}; border-color: {btn_press}; }}"
        )
        apply_fluent_dialog_footer(self)
        self.widget.setMinimumWidth(460)


class ConsolidatedDeployDialog(MessageBoxBase):
    """Single fallback override modal when deploying with unresolved preflight warnings."""

    def __init__(
        self,
        parent: QWidget,
        warnings: list[dict[str, Any]],
        *,
        has_duplicates: bool = False,
    ) -> None:
        super().__init__(parent)
        self.choice: str = "pause" if has_duplicates else "keep"
        self._has_duplicates = bool(has_duplicates)

        self.titleLabel = SubtitleLabel(
            tr("automation.deploy_warn_title", count=max(1, len(warnings))),
            self,
        )
        self.viewLayout.addWidget(self.titleLabel)

        self.warnBox = QFrame(self)
        self.warnBox.setObjectName("ConsolidatedWarnBox")
        w_lay = QVBoxLayout(self.warnBox)
        w_lay.setContentsMargins(14, 12, 14, 12)
        w_lay.setSpacing(6)

        dark = isDarkTheme()
        warn_bg = "rgba(252, 225, 0, 0.08)" if dark else "#fef9ec"
        warn_border = "rgba(252, 225, 0, 0.30)" if dark else "#e6ca86"
        warn_fg = "#fce100" if dark else "#9d5d00"
        self.warnBox.setStyleSheet(
            f"QFrame#ConsolidatedWarnBox {{"
            f" background-color: {warn_bg};"
            f" border: 1px solid {warn_border};"
            f" border-radius: 8px;"
            f"}}"
        )

        self.warningLabels: list[BodyLabel] = []
        for item in warnings:
            txt = str(item.get("text") or "").strip()
            if not txt:
                continue
            lbl = BodyLabel(f"• {txt}", self.warnBox)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {warn_fg}; background: transparent;")
            w_lay.addWidget(lbl)
            self.warningLabels.append(lbl)

        self.viewLayout.addWidget(self.warnBox)

        self.cancelButton.setText(escape_qt_mnemonic(tr("common.cancel")))
        self.keepBtn: PushButton | None = None
        if has_duplicates:
            self.yesButton.setText(escape_qt_mnemonic(tr("cloud.dup_deploy_pause_btn")))
            self.keepBtn = PushButton(escape_qt_mnemonic(tr("cloud.dup_deploy_keep_btn")), self)
            self.keepBtn.clicked.connect(self._on_keep_clicked)
            apply_fluent_dialog_footer(
                self,
                right_widgets=[self.cancelButton, self.keepBtn, self.yesButton],
            )
        else:
            self.yesButton.setText(escape_qt_mnemonic(tr("cloud.dup_deploy_keep_btn")))
            apply_fluent_dialog_footer(self)

        self.widget.setMinimumWidth(500)

    def _on_keep_clicked(self) -> None:
        self.choice = "keep"
        self.accept()


__all__ = [
    "ConsolidatedDeployDialog",
    "DMPreviewPanel",
    "PreflightRow",
    "PreflightSummaryTile",
    "ResetConfirmDialog",
    "StepPill",
    "apply_fluent_dialog_footer",
]
