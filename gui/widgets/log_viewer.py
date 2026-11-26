"""Redacting monospace diagnostic log viewer widget."""
from __future__ import annotations

import platform
import sys
from typing import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PlainTextEdit,
    PushButton,
    StrongBodyLabel,
    TransparentPushButton,
)

from gui.services.i18n import tr
from gui.services.storage import redact_secrets
from gui.services.theme import escape_qt_mnemonic, tabular_mono_font
from gui.version import APP_VERSION
from gui.widgets.split_row import ResponsiveSplitRow
from gui.widgets.status_pill import StatusPill


class LogViewer(CardWidget):
    """Monospace read-only log console with secret redaction on append and copy."""

    def __init__(
        self,
        parent: QWidget | None = None,
        secrets_provider: Callable[[], Iterable[str]] | None = None,
        summary_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._secrets_provider = secrets_provider
        self._summary_provider = summary_provider
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(10)

        left_grp = QWidget(self)
        left_lay = QHBoxLayout(left_grp)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)
        self.titleLabel = StrongBodyLabel(tr("run.live_logs_title"), left_grp)
        self.redactedBadge = StatusPill(tr("run.redacted_badge"), "neutral", left_grp)
        self.countLabel = CaptionLabel("", left_grp)
        left_lay.addWidget(self.titleLabel, 0, Qt.AlignVCenter)
        left_lay.addWidget(self.redactedBadge, 0, Qt.AlignVCenter)
        left_lay.addWidget(self.countLabel, 0, Qt.AlignVCenter)

        right_grp = QWidget(self)
        right_lay = QHBoxLayout(right_grp)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        self.clearBtn = TransparentPushButton(
            FIF.DELETE, escape_qt_mnemonic(tr("common.clear")), right_grp
        )
        self.copyBtn = PushButton(
            FIF.COPY, escape_qt_mnemonic(tr("diag.copy_report")), right_grp
        )
        right_lay.addWidget(self.clearBtn, 0, Qt.AlignVCenter)
        right_lay.addWidget(self.copyBtn, 0, Qt.AlignVCenter)

        self.headerRow = ResponsiveSplitRow(left_grp, right_grp, h_spacing=8, v_spacing=6, parent=self)

        self.editor = PlainTextEdit(self)
        self.editor.setReadOnly(True)
        self.editor.setMaximumBlockCount(5000)
        self.editor.setMinimumWidth(0)
        self.editor.setMinimumHeight(210)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.editor.setPlaceholderText(tr("diag.empty"))
        self.editor.setFont(tabular_mono_font(10))

        root.addWidget(self.headerRow)
        root.addWidget(self.editor, 1)

        self.clearBtn.clicked.connect(self.clear_logs)
        self.copyBtn.clicked.connect(self.copy_diagnostic_report)
        self._update_count()

    def _extra_secrets(self) -> list[str]:
        if self._secrets_provider is None:
            return []
        try:
            return [str(s) for s in self._secrets_provider() if s]
        except Exception:
            return []

    def _update_count(self) -> None:
        text = self.editor.toPlainText().strip()
        lines = len(text.splitlines()) if text else 0
        self.countLabel.setText(f"({lines})" if lines else "")

    def append_log(self, line: str) -> None:
        if line is None:
            return
        safe = redact_secrets(str(line).rstrip("\r\n"), extra_secrets=self._extra_secrets())
        if not safe:
            return
        self.editor.appendPlainText(safe)
        sb = self.editor.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())
        self._update_count()

    def set_logs(self, text: str) -> None:
        safe = redact_secrets(str(text or ""), extra_secrets=self._extra_secrets())
        self.editor.setPlainText(safe)
        sb = self.editor.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())
        self._update_count()

    def clear_logs(self) -> None:
        self.editor.clear()
        self._update_count()

    def build_diagnostic_report(self) -> str:
        header_lines = [
            "=== TikTok Streak Diagnostic Report ===",
            f"App: v{APP_VERSION} | OS: {platform.platform()} | Python: {sys.version.split()[0]} | Frozen: {getattr(sys, 'frozen', False)}",
        ]
        if self._summary_provider is not None:
            try:
                extra_summary = self._summary_provider()
                if extra_summary:
                    header_lines.append(extra_summary)
            except Exception:
                pass
        header_lines.append("")
        logs = self.editor.toPlainText().strip() or "(no logs recorded)"
        combined = "\n".join(header_lines) + logs
        return redact_secrets(combined, extra_secrets=self._extra_secrets())

    def copy_diagnostic_report(self) -> None:
        report = self.build_diagnostic_report()
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(report)
        InfoBar.success(
            tr("common.copied"),
            tr("diag.copy_report"),
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("run.live_logs_title"))
        self.redactedBadge.setState("neutral", tr("run.redacted_badge"))
        self.clearBtn.setText(escape_qt_mnemonic(tr("common.clear")))
        self.copyBtn.setText(escape_qt_mnemonic(tr("diag.copy_report")))
        self.editor.setPlaceholderText(tr("diag.empty"))


__all__ = ["LogViewer"]
