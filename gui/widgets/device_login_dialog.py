"""Fluent modal dialog for GitHub OAuth Device Flow authorization."""
from __future__ import annotations

import threading
import uuid
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    SubtitleLabel,
)

from gui.services.github_api import GitHub, device_flow_poll, has_required_scopes
from gui.services.i18n import tr
from gui.workers import WorkerController, WorkerHandle


def _poll_and_fetch_user(
    device_code: str,
    interval: int,
    expires_in: int,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    token = device_flow_poll(
        device_code=device_code,
        interval=interval,
        expires_in=expires_in,
        cancel_event=cancel_event,
    )
    me = GitHub(token).whoami()
    return {"token": token, "user": me}


class DeviceLoginDialog(MessageBoxBase):
    """Modal GitHub OAuth Device Flow dialog with clipboard copy & cancelable poll."""

    def __init__(
        self,
        parent: QWidget,
        worker_controller: WorkerController,
        user_code: str,
        verification_uri: str,
        device_code: str,
        interval: int = 5,
        expires_in: int = 900,
    ) -> None:
        super().__init__(parent)
        self._workers = worker_controller
        self.user_code = str(user_code or "").strip()
        self.verification_uri = str(verification_uri or "https://github.com/login/device").strip()
        self.device_code = str(device_code or "").strip()
        self.interval = max(1, int(interval or 5))
        self.expires_in = max(30, int(expires_in or 900))
        self.cancel_event = threading.Event()
        self._handle: WorkerHandle | None = None
        self._task_key = f"github_device_flow_poll_{id(self)}_{uuid.uuid4().hex[:8]}"
        self._closed = False

        self.result_token: str = ""
        self.result_user: dict[str, Any] = {}

        self.titleLabel = SubtitleLabel(tr("device_dlg.title"), self)
        self.subtitleLabel = BodyLabel(tr("device_dlg.subtitle"), self)
        self.subtitleLabel.setWordWrap(True)

        self.codeEdit = LineEdit(self)
        self.codeEdit.setReadOnly(True)
        self.codeEdit.setAlignment(Qt.AlignCenter)
        self.codeEdit.setText(self.user_code)
        code_font = QFont("Consolas", 20)
        code_font.setBold(True)
        code_font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self.codeEdit.setFont(code_font)
        self.codeEdit.setMinimumHeight(48)

        self.copyOpenBtn = PrimaryPushButton(FIF.GLOBE, tr("device_dlg.copy_open"), self)
        self.copyOpenBtn.clicked.connect(self._copy_and_open)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 4, 0, 0)
        status_row.setSpacing(10)
        self.spinner = IndeterminateProgressRing(self)
        self.spinner.setFixedSize(22, 22)
        self.statusLabel = CaptionLabel(tr("device_dlg.waiting"), self)
        self.statusLabel.setWordWrap(True)
        status_row.addWidget(self.spinner, 0, Qt.AlignVCenter)
        status_row.addWidget(self.statusLabel, 1, Qt.AlignVCenter)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addWidget(self.codeEdit)
        self.viewLayout.addWidget(self.copyOpenBtn)
        self.viewLayout.addLayout(status_row)

        self.hideYesButton()
        self.cancelButton.setText(tr("common.cancel"))
        self.cancelButton.clicked.connect(self._on_cancel_clicked)
        self.widget.setMinimumWidth(460)

        self._start_polling()

    def _copy_and_open(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.user_code)
        QDesktopServices.openUrl(QUrl(self.verification_uri))
        InfoBar.success(
            tr("common.copied"),
            tr("device_dlg.copied_hint", code=self.user_code),
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def _start_polling(self) -> None:
        self._handle = self._workers.start_task(
            _poll_and_fetch_user,
            self.device_code,
            self.interval,
            self.expires_in,
            self.cancel_event,
            task_key=self._task_key,
            cancel_event=self.cancel_event,
            on_finished=self._on_poll_finished,
            on_error=self._on_poll_error,
        )

    def _on_poll_finished(self, payload: dict[str, Any]) -> None:
        if self._closed or self.cancel_event.is_set():
            return
        token = str(payload.get("token") or "").strip()
        user = dict(payload.get("user") or {})
        scopes = user.get("scopes") or []
        if user.get("scopes_header_present") and not has_required_scopes(scopes):
            self._on_poll_error(tr("err.http_403"))
            return

        self.result_token = token
        self.result_user = user
        self.spinner.stop()
        self.spinner.hide()
        login = user.get("login", "")
        self.statusLabel.setText(tr("device_dlg.authorized", login=login))
        self.accept()

    def _on_poll_error(self, err_msg: str) -> None:
        if self._closed or self.cancel_event.is_set():
            return
        self.spinner.stop()
        self.spinner.hide()
        self.statusLabel.setStyleSheet("color: #d13438;")
        self.statusLabel.setText(err_msg)
        self.cancelButton.setText(tr("common.close"))

    def _on_cancel_clicked(self) -> None:
        self.cancel_event.set()

    def reject(self) -> None:
        self._closed = True
        self.cancel_event.set()
        super().reject()

    def closeEvent(self, event) -> None:
        self._closed = True
        self.cancel_event.set()
        super().closeEvent(event)


__all__ = ["DeviceLoginDialog"]
