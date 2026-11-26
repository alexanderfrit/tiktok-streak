"""Fluent modal dialog for pasting TikTok cookies with live parse preview."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon as FIF,
    InfoBadge,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    MessageBoxBase,
    PlainTextEdit,
    SubtitleLabel,
    TransparentPushButton,
    isDarkTheme,
)

from gui.services.i18n import tr
from gui.services.theme import INNER_RADIUS, escape_qt_mnemonic
from gui.widgets.fluent_redesign import StepPill, apply_fluent_dialog_footer

try:
    from gui.services.tiktok_auth import check_cloud_cookie_readiness, extract_session_summary
except ImportError:  # pragma: no cover
    from gui.services.tiktok_auth import extract_session_summary

    def check_cloud_cookie_readiness(raw_or_dict):  # type: ignore[misc]
        summary = extract_session_summary(raw_or_dict)
        cookies = summary.get("cookies") or {}
        has_regional = any(bool(cookies.get(k)) for k in ("tt-target-idc", "store-idc"))
        sessionid = summary.get("sessionid") or ""
        count = int(summary.get("count") or len(cookies))
        return {
            "cloud_ready": bool(sessionid and has_regional),
            "is_bare_session": bool(sessionid and not has_regional and count <= 4),
            "missing_regional": [k for k in ("tt-target-idc", "store-idc") if not cookies.get(k)],
            "count": count,
        }


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
        self.switch_to_browser_login: bool = False

        self.titleLabel = SubtitleLabel(tr("cookie_dlg.title"), self)

        # Evenly distributed 3-pill step strip replacing the 52-word wall of text
        steps_row = QHBoxLayout()
        steps_row.setContentsMargins(0, 0, 0, 0)
        steps_row.setSpacing(8)
        self.step1Pill = StepPill(tr("cookie_dlg.step_1"), "done", expanding=True, parent=self)
        self.step2Pill = StepPill(tr("cookie_dlg.step_2"), "current", expanding=True, parent=self)
        self.step3Pill = StepPill(tr("cookie_dlg.step_3"), "pending", expanding=True, parent=self)
        self.stepPills = [self.step1Pill, self.step2Pill, self.step3Pill]
        steps_row.addWidget(self.step1Pill, 1)
        steps_row.addWidget(self.step2Pill, 1)
        steps_row.addWidget(self.step3Pill, 1)

        self.textEdit = PlainTextEdit(self)
        self.textEdit.setPlaceholderText(tr("cookie_dlg.placeholder"))
        self.textEdit.setMinimumHeight(124)
        if initial_text:
            self.textEdit.setPlainText(initial_text)

        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(8)
        self.previewBadge = InfoBadge.attension(tr("cookie_dlg.preview_empty"), self)
        self.previewDetail = CaptionLabel("", self)
        self.previewDetail.setWordWrap(True)
        preview_row.addWidget(self.previewBadge, 0, Qt.AlignLeft | Qt.AlignVCenter)
        preview_row.addWidget(self.previewDetail, 1, Qt.AlignVCenter)

        # Concise bare-cookie warning banner with 1-click escape hatch to Guided Browser Login
        self.bareWarningBanner = QFrame(self)
        self.bareWarningBanner.setObjectName("BareCookieBanner")
        dark = isDarkTheme()
        warn_bg = "rgba(252, 225, 0, 0.08)" if dark else "#fef9ec"
        warn_border = "rgba(252, 225, 0, 0.30)" if dark else "#e6ca86"
        warn_fg = "#fce100" if dark else "#9d5d00"
        self.bareWarningBanner.setStyleSheet(
            f"QFrame#BareCookieBanner {{"
            f" background-color: {warn_bg};"
            f" border: 1px dashed {warn_border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        bw_lay = QHBoxLayout(self.bareWarningBanner)
        bw_lay.setContentsMargins(10, 7, 8, 7)
        bw_lay.setSpacing(8)
        self.bareWarningLabel = CaptionLabel(tr("cookie_dlg.bare_warning"), self.bareWarningBanner)
        self.bareWarningLabel.setStyleSheet(f"color: {warn_fg}; background: transparent;")
        self.bareWarningLabel.setWordWrap(True)
        self.useBrowserLoginBtn = TransparentPushButton(
            FIF.GLOBE,
            escape_qt_mnemonic(tr("cookie_dlg.use_browser_btn")),
            self.bareWarningBanner,
        )
        self.useBrowserLoginBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.useBrowserLoginBtn.clicked.connect(self._switch_to_browser_login)
        bw_lay.addWidget(self.bareWarningLabel, 1, Qt.AlignVCenter)
        bw_lay.addWidget(self.useBrowserLoginBtn, 0, Qt.AlignVCenter)
        self.bareWarningBanner.hide()
        self.bareWarningLabel.hide()

        self.errorLabel = CaptionLabel("", self)
        self.errorLabel.setStyleSheet("color: #d13438;")
        self.errorLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addLayout(steps_row)
        self.viewLayout.addWidget(self.textEdit)
        self.viewLayout.addLayout(preview_row)
        self.viewLayout.addWidget(self.bareWarningBanner)
        self.viewLayout.addWidget(self.errorLabel)

        self.yesButton.setText(escape_qt_mnemonic(tr("cookie_dlg.save_btn")))
        self.cancelButton.setText(escape_qt_mnemonic(tr("common.cancel")))
        apply_fluent_dialog_footer(self)
        self.widget.setMinimumWidth(520)

        self.textEdit.textChanged.connect(self._update_preview)
        self._update_preview()

    def _switch_to_browser_login(self) -> None:
        self.switch_to_browser_login = True
        self.reject()

    def _set_bare_warning_visible(self, visible: bool) -> None:
        self.bareWarningBanner.setVisible(visible)
        self.bareWarningLabel.setVisible(visible)

    def _update_preview(self) -> None:
        raw = self.textEdit.toPlainText().strip()
        if not raw:
            self.step1Pill.set_state("done")
            self.step2Pill.set_state("current")
            self.step3Pill.set_state("pending")
            self.previewBadge.setLevel(InfoLevel.ATTENTION)
            self.previewBadge.setText(tr("cookie_dlg.preview_empty"))
            self.previewBadge.adjustSize()
            self.previewDetail.setText("")
            self._set_bare_warning_visible(False)
            self.errorLabel.hide()
            return

        summary = extract_session_summary(raw)
        if summary["ok"]:
            self.step1Pill.set_state("done")
            self.step2Pill.set_state("done")
            self.step3Pill.set_state("done")
            masked = _mask_token(summary["sessionid"])
            self.previewBadge.setLevel(InfoLevel.SUCCESS)
            self.previewBadge.setText(
                tr("cookie_dlg.preview_valid", count=summary["count"], preview=masked)
            )
            self.previewBadge.adjustSize()
            keys = " · ".join(list(summary["cookies"].keys())[:5])
            self.previewDetail.setText(keys)
            readiness = check_cloud_cookie_readiness(summary["cookies"])
            self._set_bare_warning_visible(bool(readiness.get("is_bare_session")))
            self.errorLabel.hide()
        else:
            self.step1Pill.set_state("done")
            self.step2Pill.set_state("done")
            self.step3Pill.set_state("current")
            self.previewBadge.setLevel(InfoLevel.WARNING)
            self.previewBadge.setText(tr("cookie_dlg.preview_empty"))
            self.previewBadge.adjustSize()
            self.previewDetail.setText("")
            self._set_bare_warning_visible(False)

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
