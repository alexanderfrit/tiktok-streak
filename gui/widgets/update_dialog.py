"""WinUI 3 modal dialog for software update release notes, verified download, and EXE swap."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    MessageBoxBase,
    ProgressBar,
    PushButton,
    SubtitleLabel,
    TextBrowser,
    isDarkTheme,
)

from gui.services.i18n import tr
from gui.services.storage import default_config_dir
from gui.services.theme import escape_qt_mnemonic
from gui.services.updater import (
    can_self_update_executable,
    download_update_asset,
    launch_windows_self_update,
    markdown_to_update_html,
)
from gui.version import APP_VERSION
from gui.workers import WorkerController


def _fmt_mb(num_bytes: int) -> str:
    mb = max(0, int(num_bytes)) / (1024 * 1024)
    return f"{mb:.1f} MB"


def _download_update_task(
    url: str,
    dest_path: str,
    expected_sha256: str,
    expected_size: int,
    cancel_event: threading.Event,
    progress_cb=None,
) -> dict[str, Any]:
    def _on_prog(dl: int, total: int) -> None:
        if progress_cb is not None:
            progress_cb({"downloaded": dl, "total": total})

    path = download_update_asset(
        url=url,
        dest_path=dest_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        progress_cb=_on_prog,
        cancel_event=cancel_event,
    )
    return {"path": str(path)}


class UpdateDialog(MessageBoxBase):
    """Modal update dialog with SHA-256 verified download and automatic EXE swap."""

    def __init__(
        self,
        parent: QWidget,
        update_info: dict[str, Any],
        worker_controller: WorkerController,
        download_dir: Path | None = None,
        start_download_cb: Callable[[], None] | None = None,
        cancel_download_cb: Callable[[], None] | None = None,
        download_state: str = "idle",
        downloaded_bytes: int = 0,
        total_bytes: int = 0,
        download_error: str = "",
    ) -> None:
        super().__init__(parent)
        self._info = dict(update_info or {})
        self._workers = worker_controller
        self._download_dir = Path(download_dir) if download_dir else (default_config_dir() / "updates")
        self._start_download_cb = start_download_cb
        self._cancel_download_cb = cancel_download_cb
        self._cancel_event = threading.Event()
        self._downloading = False
        self._download_state = "idle"
        self._task_key = f"app_update_download_{id(self)}"

        self.user_skipped_version = False
        self.install_launched = False
        self.downloaded_path: Path | None = None

        current_ver = str(self._info.get("current_version") or APP_VERSION).lstrip("vV")
        latest_ver = str(self._info.get("latest_version") or current_ver).lstrip("vV")
        self._latest_ver = latest_ver
        self._download_url = str(self._info.get("download_url") or "").strip()
        self._html_url = str(self._info.get("html_url") or "").strip()
        self._can_install_exe = bool(can_self_update_executable() and self._download_url)

        asset_size = int(self._info.get("asset_size") or 0)
        sub_text = tr("update.dlg_subtitle", current=current_ver, latest=latest_ver)
        if asset_size > 0:
            sub_text = f"{sub_text}  •  {_fmt_mb(asset_size)}"

        self.titleLabel = SubtitleLabel(tr("update.dlg_title", latest=latest_ver), self)
        self.subtitleLabel = BodyLabel(sub_text, self)
        self.subtitleLabel.setWordWrap(True)

        notes = str(self._info.get("release_notes") or "").strip() or tr("update.dlg_no_notes")
        dark = isDarkTheme()
        self.notesEdit = TextBrowser(self)
        self.notesEdit.setReadOnly(True)
        self.notesEdit.setOpenExternalLinks(True)
        self.notesEdit.document().setDocumentMargin(12)
        self.notesEdit.setHtml(markdown_to_update_html(notes, dark=dark))
        self.notesEdit.setMinimumHeight(220)
        self.notesEdit.setMaximumHeight(340)
        border_col = "rgba(255, 255, 255, 0.12)" if dark else "rgba(0, 0, 0, 0.10)"
        bg_col = "rgba(255, 255, 255, 0.04)" if dark else "rgba(0, 0, 0, 0.025)"
        self.notesEdit.setStyleSheet(
            f"TextBrowser {{ background-color: {bg_col}; border: 1px solid {border_col}; "
            "border-radius: 8px; padding: 2px; }}"
        )

        self.sourceNoteLabel = CaptionLabel(tr("update.dlg_source_note"), self)
        self.sourceNoteLabel.setWordWrap(True)
        self.sourceNoteLabel.setVisible(not self._can_install_exe)

        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.hide()

        self.statusLabel = CaptionLabel("", self)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addWidget(self.notesEdit)
        self.viewLayout.addWidget(self.sourceNoteLabel)
        self.viewLayout.addWidget(self.progressBar)
        self.viewLayout.addWidget(self.statusLabel)

        # Customize MessageBoxBase buttons without auto-closing before download completes
        try:
            self.yesButton.clicked.disconnect()
        except Exception:
            pass
        try:
            self.cancelButton.clicked.disconnect()
        except Exception:
            pass

        primary_txt = (
            tr("update.dlg_download_install")
            if self._can_install_exe
            else tr("update.dlg_open_release")
        )
        self.yesButton.setText(escape_qt_mnemonic(primary_txt))
        self.cancelButton.setText(escape_qt_mnemonic(tr("update.btn_later")))

        self.skipBtn = PushButton(escape_qt_mnemonic(tr("update.btn_skip")), self)
        self.skipBtn.clicked.connect(self._on_skip_clicked)
        self.buttonLayout.insertWidget(1, self.skipBtn)

        self.yesButton.clicked.connect(self._on_primary_clicked)
        self.cancelButton.clicked.connect(self._on_cancel_clicked)

        self.widget.setMinimumWidth(600)
        self.sync_download_state(
            download_state,
            downloaded=downloaded_bytes,
            total=total_bytes or int(self._info.get("asset_size") or 0),
            error=download_error,
        )

    def sync_download_state(
        self,
        state: str,
        downloaded: int = 0,
        total: int = 0,
        error: str = "",
    ) -> None:
        """Synchronize progress bar and action buttons with active download state."""
        norm = str(state or "idle").strip().lower()
        if norm not in ("idle", "downloading", "installing", "error"):
            norm = "idle"
        self._download_state = norm
        self._downloading = norm == "downloading"

        if norm == "downloading":
            self.yesButton.setEnabled(True)
            self.yesButton.setText(escape_qt_mnemonic(tr("update.btn_background")))
            self.skipBtn.setVisible(False)
            self.cancelButton.setEnabled(True)
            self.cancelButton.setText(escape_qt_mnemonic(tr("update.btn_cancel_download")))
            eff_total = int(total or self._info.get("asset_size") or 0)
            if eff_total > 0:
                pct = max(0, min(100, int((int(downloaded) * 100) / eff_total)))
                self.progressBar.setRange(0, 100)
                self.progressBar.setValue(pct)
                total_s = _fmt_mb(eff_total)
            else:
                self.progressBar.setRange(0, 0)
                total_s = "?"
            self.progressBar.show()
            self.statusLabel.setStyleSheet("")
            self.statusLabel.setText(
                tr("update.dlg_downloading", downloaded=_fmt_mb(int(downloaded)), total=total_s)
            )
            self.statusLabel.show()
            return

        if norm == "installing":
            self.yesButton.setEnabled(False)
            self.skipBtn.setVisible(False)
            self.cancelButton.setEnabled(False)
            self.progressBar.setRange(0, 100)
            self.progressBar.setValue(100)
            self.progressBar.show()
            self.statusLabel.setStyleSheet("")
            self.statusLabel.setText(tr("update.dlg_verifying"))
            self.statusLabel.show()
            return

        primary_txt = (
            tr("update.dlg_download_install")
            if self._can_install_exe
            else tr("update.dlg_open_release")
        )
        self.yesButton.setEnabled(True)
        self.yesButton.setText(escape_qt_mnemonic(primary_txt))
        self.skipBtn.setEnabled(True)
        self.skipBtn.setVisible(True)
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText(escape_qt_mnemonic(tr("update.btn_later")))
        self.progressBar.hide()

        if norm == "error" and str(error or "").strip():
            self.statusLabel.setStyleSheet("color: #d13438;")
            self.statusLabel.setText(str(error))
            self.statusLabel.show()
        else:
            self.statusLabel.setStyleSheet("")
            self.statusLabel.clear()
            self.statusLabel.hide()

    def _on_skip_clicked(self) -> None:
        if self._download_state == "downloading":
            return
        self.user_skipped_version = True
        self._cancel_event.set()
        self.reject()

    def _on_cancel_clicked(self) -> None:
        if self._download_state == "downloading":
            if self._cancel_download_cb is not None:
                self._cancel_download_cb()
            else:
                self._cancel_event.set()
                self.sync_download_state("idle")
            return
        self.reject()

    def _on_primary_clicked(self) -> None:
        if not self._can_install_exe:
            if self._html_url:
                QDesktopServices.openUrl(QUrl(self._html_url))
            self.accept()
            return

        if self._download_state == "downloading":
            # Keep download running in background and let user work in MainWindow.
            self.done(0)
            return

        if self._download_state == "installing":
            return

        if self._start_download_cb is not None:
            self.sync_download_state(
                "downloading", downloaded=0, total=int(self._info.get("asset_size") or 0)
            )
            self._start_download_cb()
            return

        self._cancel_event = threading.Event()
        self.sync_download_state(
            "downloading", downloaded=0, total=int(self._info.get("asset_size") or 0)
        )

        dest_name = f"TikTokStreakSetup-v{self._latest_ver}.exe"
        dest_path = self._download_dir / dest_name
        self._workers.start_task(
            _download_update_task,
            self._download_url,
            str(dest_path),
            str(self._info.get("asset_sha256") or ""),
            int(self._info.get("asset_size") or 0),
            self._cancel_event,
            task_key=self._task_key,
            cancel_event=self._cancel_event,
            pass_progress_cb=True,
            on_progress=self._on_download_progress,
            on_finished=self._on_download_finished,
            on_error=self._on_download_error,
        )

    def _on_download_progress(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        dl = int(payload.get("downloaded") or 0)
        total = int(payload.get("total") or 0)
        self.sync_download_state("downloading", downloaded=dl, total=total)

    def _on_download_finished(self, payload: Any) -> None:
        self._downloading = False
        if self._cancel_event.is_set():
            return
        path_str = str((payload or {}).get("path") or "").strip()
        if not path_str:
            self._on_download_error("Downloaded file path is missing.")
            return
        self.downloaded_path = Path(path_str)
        self.sync_download_state("installing")
        try:
            launch_windows_self_update(self.downloaded_path)
            self.install_launched = True
            self.accept()
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception as exc:
            self._on_download_error(str(exc))

    def _on_download_error(self, err: str) -> None:
        self._downloading = False
        if self._cancel_event.is_set():
            return
        self.sync_download_state("error", error=str(err))

    def reject(self) -> None:
        if self._start_download_cb is None:
            self._cancel_event.set()
        super().reject()

    def closeEvent(self, event) -> None:
        if self._start_download_cb is None:
            self._cancel_event.set()
        super().closeEvent(event)


__all__ = ["UpdateDialog", "_download_update_task"]
