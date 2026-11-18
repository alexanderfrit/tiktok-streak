"""Windows Personalization theme engine, design tokens, typography, and vector app icon."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

CARD_RADIUS = 10
INNER_RADIUS = 6
CONTROL_RADIUS = 6
PILL_RADIUS = 10
PAGE_MARGINS = (28, 22, 28, 28)
CARD_SPACING = 16
DEFAULT_TIKTOK_ACCENT = "#fe2c55"
DEFAULT_WINDOWS_ACCENT = "#0078d4"

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


class DesignTokens:
    """Shared Fluent geometry, spacing, and default accent constants."""

    CARD_RADIUS: int = CARD_RADIUS
    INNER_RADIUS: int = INNER_RADIUS
    CONTROL_RADIUS: int = CONTROL_RADIUS
    PILL_RADIUS: int = PILL_RADIUS
    PAGE_MARGINS: tuple[int, int, int, int] = PAGE_MARGINS
    CARD_SPACING: int = CARD_SPACING
    DEFAULT_TIKTOK_ACCENT: str = DEFAULT_TIKTOK_ACCENT
    DEFAULT_WINDOWS_ACCENT: str = DEFAULT_WINDOWS_ACCENT


def _normalize_hex_color(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw or not raw.startswith("#"):
        return None
    m = _HEX_COLOR_RE.match(raw)
    if not m:
        return None
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits.lower()}"


def get_windows_personalization() -> dict[str, Any]:
    """Read Windows accent color, dark mode, and transparency preferences safely."""
    result: dict[str, Any] = {
        "accent_color": DEFAULT_WINDOWS_ACCENT,
        "dark_mode": False,
        "transparency": True,
    }
    if sys.platform != "win32":
        return result

    accent_found = False
    try:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\DWM",
            ) as dwm_key:
                try:
                    val, _ = winreg.QueryValueEx(dwm_key, "AccentColor")
                    if isinstance(val, int):
                        # DWM AccentColor DWORD is stored as 0xAABBGGRR.
                        r = val & 0xFF
                        g = (val >> 8) & 0xFF
                        b = (val >> 16) & 0xFF
                        result["accent_color"] = f"#{r:02x}{g:02x}{b:02x}"
                        accent_found = True
                except OSError:
                    pass

                if not accent_found:
                    try:
                        val, _ = winreg.QueryValueEx(dwm_key, "ColorizationColor")
                        if isinstance(val, int):
                            # ColorizationColor DWORD is stored as 0xAARRGGBB.
                            r = (val >> 16) & 0xFF
                            g = (val >> 8) & 0xFF
                            b = val & 0xFF
                            result["accent_color"] = f"#{r:02x}{g:02x}{b:02x}"
                            accent_found = True
                    except OSError:
                        pass
        except OSError:
            pass

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as pers_key:
                try:
                    light_val, _ = winreg.QueryValueEx(pers_key, "AppsUseLightTheme")
                    if isinstance(light_val, int):
                        result["dark_mode"] = light_val == 0
                except OSError:
                    pass
                try:
                    trans_val, _ = winreg.QueryValueEx(pers_key, "EnableTransparency")
                    if isinstance(trans_val, int):
                        result["transparency"] = trans_val == 1
                except OSError:
                    pass
        except OSError:
            pass
    except Exception:
        pass

    if not accent_found:
        try:
            from qframelesswindow.utils import getSystemAccentColor

            color = getSystemAccentColor()
            if color is not None and color.isValid():
                result["accent_color"] = color.name().lower()
        except Exception:
            pass

    return result


def resolve_accent_color(accent_mode: str = "system", custom_hex: str = "") -> str:
    """Resolve the active accent hex color for ``system``, ``tiktok``, or ``custom``."""
    mode = str(accent_mode or "system").strip().lower()
    if mode == "tiktok":
        return DEFAULT_TIKTOK_ACCENT
    if mode == "custom":
        normalized = _normalize_hex_color(custom_hex)
        if normalized is not None:
            return normalized
    return str(get_windows_personalization().get("accent_color") or DEFAULT_WINDOWS_ACCENT)


def escape_qt_mnemonic(text: str) -> str:
    """Escape literal ampersands so Qt buttons/checkboxes do not swallow them as mnemonics."""
    return str(text or "").replace("&", "&&")


def tabular_mono_font(point_size: int = 10) -> QFont:
    """Return a crisp fixed-pitch monospace font suitable for logs and timestamps."""
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    if point_size and point_size > 0:
        font.setPointSize(int(point_size))
    return font


def _scaled_points(points: list[tuple[float, float]], size: int) -> QPainterPath:
    scale = size / 100.0
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(QPointF(points[0][0] * scale, points[0][1] * scale))
    for x, y in points[1:]:
        path.lineTo(QPointF(x * scale, y * scale))
    path.closeSubpath()
    return path


def _resolve_icon_asset() -> Path | None:
    """Locate bundled transparent PNG or ICO mascot icon if present."""
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    bases.append(Path(__file__).resolve().parents[2])
    for base in bases:
        for rel in ("assets/app_icon.png", "assets/app_icon.ico"):
            candidate = base / rel
            if candidate.is_file():
                return candidate
    return None


def create_app_icon(accent_hex: str | None = None, dark: bool = True) -> QIcon:
    """Return multi-resolution app icon from bundled mascot artwork (with vector fallback)."""
    asset_path = _resolve_icon_asset()
    if asset_path is not None:
        base_pm = QPixmap(str(asset_path))
        if not base_pm.isNull():
            icon = QIcon()
            for size in _ICON_SIZES:
                scaled = base_pm.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                canvas = QPixmap(size, size)
                canvas.fill(Qt.GlobalColor.transparent)
                painter = QPainter(canvas)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                x = (size - scaled.width()) // 2
                y = (size - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.end()
                icon.addPixmap(canvas)
            if not icon.isNull():
                return icon

    resolved_hex = _normalize_hex_color(accent_hex) or resolve_accent_color("system")
    accent = QColor(resolved_hex)
    if not accent.isValid():
        accent = QColor(DEFAULT_WINDOWS_ACCENT)

    icon = QIcon()
    # Normalized 0..100 coordinates for a balanced geometric flame-bolt silhouette.
    outer_bolt = [
        (56.0, 16.0),
        (31.0, 52.0),
        (48.0, 52.0),
        (41.0, 84.0),
        (71.0, 44.0),
        (53.0, 44.0),
    ]
    inner_spark = [
        (54.5, 25.0),
        (40.0, 48.0),
        (51.5, 48.0),
        (47.0, 71.0),
        (63.0, 47.0),
        (50.5, 47.0),
    ]

    for size in _ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        margin = max(1.0, size * 0.0625)
        tile_rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
        radius = size * 0.22

        # Tile surface gradient
        tile_grad = QLinearGradient(tile_rect.topLeft(), tile_rect.bottomRight())
        if dark:
            tile_grad.setColorAt(0.0, QColor("#20242e"))
            tile_grad.setColorAt(1.0, QColor("#12141b"))
        else:
            tile_grad.setColorAt(0.0, QColor("#ffffff"))
            tile_grad.setColorAt(1.0, QColor("#edf1f7"))

        painter.setBrush(tile_grad)
        if size >= 24:
            border_color = QColor(accent)
            border_color.setAlpha(110 if dark else 95)
            painter.setPen(QPen(border_color, max(1.0, size * 0.025)))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(tile_rect, radius, radius)

        # Geometric flame/bolt motif
        bolt_path = _scaled_points(outer_bolt, size)
        bolt_grad = QLinearGradient(
            QPointF(size * 0.62, size * 0.16),
            QPointF(size * 0.36, size * 0.84),
        )
        bolt_grad.setColorAt(0.0, accent.lighter(122))
        bolt_grad.setColorAt(0.55, accent)
        bolt_grad.setColorAt(1.0, accent.darker(118))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bolt_grad)
        painter.drawPath(bolt_path)

        if size >= 32:
            spark_path = _scaled_points(inner_spark, size)
            spark_color = QColor("#ffffff")
            spark_color.setAlpha(68 if dark else 90)
            painter.setBrush(spark_color)
            painter.drawPath(spark_path)

        painter.end()
        icon.addPixmap(pixmap)

    return icon


__all__ = [
    "CARD_RADIUS",
    "CARD_SPACING",
    "CONTROL_RADIUS",
    "DEFAULT_TIKTOK_ACCENT",
    "DEFAULT_WINDOWS_ACCENT",
    "DesignTokens",
    "INNER_RADIUS",
    "PAGE_MARGINS",
    "PILL_RADIUS",
    "create_app_icon",
    "escape_qt_mnemonic",
    "get_windows_personalization",
    "resolve_accent_color",
    "tabular_mono_font",
]
