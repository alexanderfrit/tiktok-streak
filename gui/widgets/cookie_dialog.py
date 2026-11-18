"""Fluent modal dialog for pasting TikTok cookies with live parse preview."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    InfoBadge,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    MessageBoxBase,
    PlainTextEdit,
    SubtitleLabel,
)

from gui.services.i18n import tr
from gui.services.tiktok_auth import extract_session_summary


def _mask_token(token: str) -> str:
    s = str(token or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


class CookieDialog(MessageBoxBase):
    """Modal dialog guiding users to paste a TikTok session cookie safely."""

    def __init__(self, parent: QWidget, initial_text: str = "") -> None:
        super().__init__(parent)
        self.cookie_payload: str = ""
        self.parsed_summary: dict = {}

        self.titleLabel = SubtitleLabel(tr("cookie_dlg.title"), self)
        self.instructionsLabel = BodyLabel(tr("cookie_dlg.steps"), self)
        self.instructionsLabel.setWordWrap(True)

        self.textEdit = PlainTextEdit(self)
        self.textEdit.setPlaceholderText(tr("cookie_dlg.placeholder"))
        self.textEdit.setMinimumHeight(140)
        if initial_text:
            self.textEdit.setPlainText(initial_text)

        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(8)
        self.previewBadge = InfoBadge.attension(tr("cookie_dlg.preview_empty"), self)
        self.previewDetail = CaptionLabel("", self)
        self.previewDetail.setWordWrap(True)
        preview_row.addWidget(self.previewBadge, 0, Qt.AlignLeft)
        preview_row.addWidget(self.previewDetail, 1, Qt.AlignVCenter)

        self.errorLabel = CaptionLabel("", self)
        self.errorLabel.setStyleSheet("color: #d13438;")
        self.errorLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.instructionsLabel)
        self.viewLayout.addWidget(self.textEdit)
        self.viewLayout.addLayout(preview_row)
        self.viewLayout.addWidget(self.errorLabel)

        self.yesButton.setText(tr("common.save"))
        self.cancelButton.setText(tr("common.cancel"))
        self.widget.setMinimumWidth(540)

        self.textEdit.textChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self) -> None:
        raw = self.textEdit.toPlainText().strip()
        if not raw:
            self.previewBadge.setLevel(InfoLevel.ATTENTION)
            self.previewBadge.setText(tr("cookie_dlg.preview_empty"))
            self.previewBadge.adjustSize()
            self.previewDetail.setText("")
            self.errorLabel.hide()
            return

        summary = extract_session_summary(raw)
        if summary["ok"]:
            masked = _mask_token(summary["sessionid"])
            self.previewBadge.setLevel(InfoLevel.SUCCESS)
            self.previewBadge.setText(
                tr("cookie_dlg.preview_valid", count=summary["count"], preview=masked)
            )
            self.previewBadge.adjustSize()
            keys = ", ".join(list(summary["cookies"].keys())[:5])
            self.previewDetail.setText(keys)
            self.errorLabel.hide()
        else:
            self.previewBadge.setLevel(InfoLevel.WARNING)
            self.previewBadge.setText(tr("cookie_dlg.preview_empty"))
            self.previewBadge.adjustSize()
            self.previewDetail.setText("")

    def validate(self) -> bool:
        raw = self.textEdit.toPlainText().strip()
        summary = extract_session_summary(raw)
        if not summary["ok"]:
            msg = tr("cookie_dlg.invalid_msg")
            self.errorLabel.setText(msg)
            self.errorLabel.show()
            InfoBar.warning(
                tr("common.warning"),
                msg,
                duration=3000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return False

        self.parsed_summary = summary
        self.cookie_payload = summary["cookie_text"]
        return True


__all__ = ["CookieDialog"]
