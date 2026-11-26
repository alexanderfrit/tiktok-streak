"""Fluent modal dialog for selecting and importing cookies from an installed browser profile."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    InfoLevel,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
    TransparentPushButton,
    isDarkTheme,
)

from gui.services.browsers import list_sources
from gui.services.i18n import tr
from gui.services.theme import INNER_RADIUS, escape_qt_mnemonic
from gui.widgets.fluent_redesign import apply_fluent_dialog_footer
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController


class BrowserImportDialog(MessageBoxBase):
    """Self-contained modal picker for direct Chrome / Edge / Firefox / Zen cookie import."""

    def __init__(
        self,
        parent: QWidget,
        *,
        account_name: str = "Main",
        sources: list[dict[str, Any]] | None = None,
        scan_completed: bool = False,
        workers: WorkerController | None = None,
    ) -> None:
        super().__init__(parent)
        self._workers = workers
        self.scan_completed: bool = bool(scan_completed)
        self.latest_sources: list[dict[str, Any]] = list(sources or [])
        self.selected_source: dict[str, Any] | None = None
        self.switch_to_browser_login: bool = False

        self.titleLabel = SubtitleLabel(tr("accounts.import_dlg_title"), self)
        self.subtitleLabel = BodyLabel(
            tr("accounts.import_dlg_subtitle", account=account_name or "Main"), self
        )
        self.subtitleLabel.setWordWrap(True)

        # Profile selector + Refresh + Spinner
        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(8)
        self.browserCombo = ComboBox(self)
        self.browserCombo.setMinimumWidth(0)
        self.browserCombo.currentIndexChanged.connect(self._update_browser_badge)

        self.refreshBrowsersBtn = PushButton(
            FIF.SYNC, escape_qt_mnemonic(tr("common.refresh")), self
        )
        self.refreshBrowsersBtn.clicked.connect(self.refresh_browsers)

        self.scanSpinner = IndeterminateProgressRing(self)
        self.scanSpinner.setFixedSize(18, 18)
        self.scanSpinner.stop()
        self.scanSpinner.hide()

        picker_row.addWidget(self.browserCombo, 1)
        picker_row.addWidget(self.refreshBrowsersBtn, 0, Qt.AlignVCenter)
        picker_row.addWidget(self.scanSpinner, 0, Qt.AlignVCenter)

        # Live profile status badge + path/hint note
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self.browserBadge = StatusPill(tr("accounts.scan_not_run"), "neutral", self)
        self.browserNote = CaptionLabel("", self)
        self.browserNote.setWordWrap(True)
        status_row.addWidget(self.browserBadge, 0, Qt.AlignLeft | Qt.AlignVCenter)
        status_row.addWidget(self.browserNote, 1, Qt.AlignVCenter)

        # Contextual amber banner + 1-click escape hatch when a v20 profile is chosen
        self.v20Banner = QFrame(self)
        self.v20Banner.setObjectName("BrowserImportV20Banner")
        dark = isDarkTheme()
        warn_bg = "rgba(252, 225, 0, 0.08)" if dark else "#fef9ec"
        warn_border = "rgba(252, 225, 0, 0.30)" if dark else "#e6ca86"
        warn_fg = "#fce100" if dark else "#9d5d00"
        self.v20Banner.setStyleSheet(
            f"QFrame#BrowserImportV20Banner {{"
            f" background-color: {warn_bg};"
            f" border: 1px dashed {warn_border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        v20_lay = QHBoxLayout(self.v20Banner)
        v20_lay.setContentsMargins(10, 8, 8, 8)
        v20_lay.setSpacing(8)
        self.v20BannerLabel = CaptionLabel(tr("accounts.v20_badge"), self.v20Banner)
        self.v20BannerLabel.setStyleSheet(f"color: {warn_fg}; background: transparent;")
        self.v20BannerLabel.setWordWrap(True)
        self.useBrowserLoginBtn = TransparentPushButton(
            FIF.GLOBE,
            escape_qt_mnemonic(tr("accounts.sign_in_browser")),
            self.v20Banner,
        )
        self.useBrowserLoginBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.useBrowserLoginBtn.clicked.connect(self._switch_to_browser_login)
        v20_lay.addWidget(self.v20BannerLabel, 1, Qt.AlignVCenter)
        v20_lay.addWidget(self.useBrowserLoginBtn, 0, Qt.AlignVCenter)
        self.v20Banner.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addLayout(picker_row)
        self.viewLayout.addLayout(status_row)
        self.viewLayout.addWidget(self.v20Banner)

        self.cancelButton.setText(escape_qt_mnemonic(tr("common.cancel")))
        self.yesButton.setText(escape_qt_mnemonic(tr("accounts.import_dlg_btn")))
        apply_fluent_dialog_footer(self)
        self.widget.setMinimumWidth(520)

        self.populate_sources(self.latest_sources, scan_completed=self.scan_completed)
        if not self.scan_completed and self._workers is not None:
            self.refresh_browsers()

    def _switch_to_browser_login(self) -> None:
        self.switch_to_browser_login = True
        self.reject()

    def refresh_browsers(self) -> None:
        if self._workers is None or self._workers.is_running("scan_browsers_dlg"):
            return
        self.refreshBrowsersBtn.setEnabled(False)
        self.scanSpinner.show()
        self.scanSpinner.start()
        self.browserBadge.setState("info", tr("common.loading"))
        self.browserBadge.adjustSize()
        self._workers.start_task(
            list_sources,
            enrich=True,
            task_key="scan_browsers_dlg",
            on_finished=self._on_scan_finished,
            on_error=self._on_scan_error,
        )

    def _on_scan_finished(self, sources: list[dict[str, Any]]) -> None:
        self.refreshBrowsersBtn.setEnabled(True)
        self.scanSpinner.stop()
        self.scanSpinner.hide()
        self.populate_sources(sources, scan_completed=True)

    def _on_scan_error(self, err_msg: str) -> None:
        self.refreshBrowsersBtn.setEnabled(True)
        self.scanSpinner.stop()
        self.scanSpinner.hide()
        self.scan_completed = True
        self.browserBadge.setState(InfoLevel.WARNING, tr("common.warning"))
        self.browserBadge.adjustSize()
        self.browserNote.setText(str(err_msg or ""))
        self.yesButton.setEnabled(False)

    def populate_sources(
        self,
        sources: list[dict[str, Any]] | None,
        *,
        scan_completed: bool = True,
    ) -> None:
        self.scan_completed = bool(scan_completed)
        self.latest_sources = list(sources or [])
        prev = self.browserCombo.currentData()
        prev_id = prev.get("id") if isinstance(prev, dict) else None

        self.browserCombo.blockSignals(True)
        self.browserCombo.clear()
        best_idx = 0
        best_score = -1
        for idx, src in enumerate(self.latest_sources):
            suffix = ""
            score = 1
            if src.get("v20_blocked"):
                suffix = " [v20]"
                score = 0
            elif src.get("locked"):
                suffix = " [Locked]"
                score = 0
            elif src.get("has_tiktok"):
                suffix = " [TikTok]"
                score = 2
            self.browserCombo.addItem(f"{src['label']}{suffix}", userData=src)
            if score > best_score:
                best_score = score
                best_idx = idx

        restored = False
        if prev_id:
            for i in range(self.browserCombo.count()):
                d = self.browserCombo.itemData(i)
                if isinstance(d, dict) and d.get("id") == prev_id:
                    self.browserCombo.setCurrentIndex(i)
                    restored = True
                    break
        if not restored and self.browserCombo.count() > 0:
            self.browserCombo.setCurrentIndex(best_idx)
        self.browserCombo.blockSignals(False)
        self._update_browser_badge()

    def _update_browser_badge(self, *_args: Any) -> None:
        src = self.browserCombo.currentData()
        if not isinstance(src, dict):
            badge_key = (
                "accounts.no_tiktok_badge" if self.scan_completed else "accounts.scan_not_run"
            )
            note_key = (
                "accounts.import_dlg_no_profiles"
                if self.scan_completed
                else "accounts.guided_login_hint"
            )
            self.browserBadge.setState("neutral", tr(badge_key))
            self.browserBadge.adjustSize()
            self.browserNote.setText(tr(note_key))
            self.v20Banner.hide()
            self.yesButton.setEnabled(False)
            return

        if src.get("v20_blocked"):
            self.browserBadge.setState(InfoLevel.WARNING, tr("accounts.v20_badge"))
            self.browserNote.setText(str(src.get("cookies_path", "")))
            self.v20Banner.show()
            self.yesButton.setEnabled(False)
        elif src.get("locked"):
            self.browserBadge.setState(InfoLevel.ERROR, tr("accounts.browser_locked_badge"))
            self.browserNote.setText(tr("err.browser_locked"))
            self.v20Banner.hide()
            self.yesButton.setEnabled(False)
        elif src.get("has_tiktok", 0) > 0:
            self.browserBadge.setState(InfoLevel.SUCCESS, tr("accounts.has_tiktok_badge"))
            self.browserNote.setText(str(src.get("cookies_path", "")))
            self.v20Banner.hide()
            self.yesButton.setEnabled(True)
        else:
            self.browserBadge.setState("neutral", tr("accounts.no_tiktok_badge"))
            self.browserNote.setText(str(src.get("cookies_path", "")))
            self.v20Banner.hide()
            self.yesButton.setEnabled(True)
        self.browserBadge.adjustSize()

    def validate(self) -> bool:
        src = self.browserCombo.currentData()
        if not isinstance(src, dict):
            return False
        if src.get("v20_blocked") or src.get("locked"):
            return False
        self.selected_source = src
        return True


__all__ = ["BrowserImportDialog"]
