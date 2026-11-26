"""Message & Media page with equal-height Fluent editor and live DM & Streak Pet preview."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FlowLayout,
    FluentIcon as FIF,
    HeaderCardWidget,
    ImageLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PushButton,
    SmoothScrollArea,
    SwitchButton,
    TitleLabel,
    TransparentPushButton,
)

# Backward-compatible re-exports for external callers and unit tests
from gui.services.cloud_deploy import (
    _build_cloud_secrets,
    _deploy_github_task,
    _discover_bot_repos_task,
    _parse_repo_input,
    _pause_multiple_bot_repos_task,
    _toggle_bot_repo_workflow_task,
    _validate_pat_task,
)
from gui.services.i18n import tr
from gui.services.local_runner import _resolve_asset_path
from gui.services.storage import (
    AppState,
    ConfigStore,
    DEFAULT_PHOTO_PATH,
    DEFAULT_VIDEO_URL,
    SecretStore,
)
from gui.services.theme import PAGE_MARGINS, escape_qt_mnemonic
from gui.services.validators import fetch_tiktok_video_preview, validate_video_url
from gui.widgets.fluent_redesign import DMPreviewPanel
from gui.widgets.fluent_spin_box import FluentSpinBox
from gui.widgets.split_row import ResponsiveSplitRow
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController


class ContentPage(SmoothScrollArea):
    """Configure daily streak message, video share card, and photo card with live preview."""

    state_changed = Signal()
    log_emitted = Signal(str)
    navigate_requested = Signal(str)

    def __init__(
        self,
        state: AppState,
        config_store: ConfigStore,
        secret_store: SecretStore,
        worker_controller: WorkerController,
        smoke_test: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("content_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._loading = False
        self._smoke_test = smoke_test
        self._video_verify_state: str = "unverified"
        self._verified_video_url: str = ""
        self._verified_video_author: str = ""
        self._verified_video_nick: str = ""
        self._video_verify_error: str = ""
        self._video_preview_cache: dict[str, dict[str, Any]] = {}
        self._is_stacked: bool = False

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(250)
        self._save_debounce.timeout.connect(self._emit_state_changed)

        self._preview_fetch_timer = QTimer(self)
        self._preview_fetch_timer.setSingleShot(True)
        self._preview_fetch_timer.setInterval(600)
        self._preview_fetch_timer.timeout.connect(self._fetch_video_preview_media)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGINS)
        self.vBoxLayout.setSpacing(14)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        self._build_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        # Header: Title + subtitle left, live Pet growth badge + Next: Automation CTA right
        title_box = QWidget(self.view)
        title_col = QVBoxLayout(title_box)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)
        self.titleLabel = TitleLabel(tr("content.title"), title_box)
        self.subtitleLabel = BodyLabel(tr("content.subtitle"), title_box)
        self.subtitleLabel.setWordWrap(True)
        self.subtitleLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_col.addWidget(self.titleLabel)
        title_col.addWidget(self.subtitleLabel)

        badge_wrap = QWidget(self.view)
        badge_lay = QHBoxLayout(badge_wrap)
        badge_lay.setContentsMargins(0, 0, 0, 0)
        badge_lay.setSpacing(0)
        self.petGrowthBadge = StatusPill(tr("content.pet_pts_badge", pts=7), "success", badge_wrap)
        badge_lay.addStretch(1)
        badge_lay.addWidget(self.petGrowthBadge, 0, Qt.AlignVCenter)

        self.nextAutomationBtn = PushButton(
            FIF.RIGHT_ARROW, escape_qt_mnemonic(tr("content.next_automation")), self.view
        )
        self.nextAutomationBtn.clicked.connect(lambda: self.navigate_requested.emit("automation"))
        right_box = ResponsiveSplitRow(
            badge_wrap, self.nextAutomationBtn, h_spacing=10, v_spacing=6, left_min_width=None, parent=self.view
        )

        self.headerSplit = ResponsiveSplitRow(
            title_box, right_box, h_spacing=12, v_spacing=8, left_min_width=220, parent=self.view
        )
        self.vBoxLayout.addWidget(self.headerSplit)

        # Responsive Equal-height Fluent Split: Left Editor Stack (stretch 1) + Right 1:1 DM Preview
        self.splitHost = QWidget(self.view)
        self.splitGrid = QGridLayout(self.splitHost)
        self.splitGrid.setContentsMargins(0, 0, 0, 0)
        self.splitGrid.setHorizontalSpacing(14)
        self.splitGrid.setVerticalSpacing(14)

        self.leftColWidget = QWidget(self.splitHost)
        self.leftColWidget.setMinimumWidth(0)
        left_col = QVBoxLayout(self.leftColWidget)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(14)

        # 1. Streak Message Card
        self.msgCard = HeaderCardWidget(self.view)
        self.msgCard.setTitle(tr("content.message_label"))
        self.msgCard.headerView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.msgCard.setMinimumWidth(0)
        self.msgAlwaysPill = StatusPill(tr("content.always_sent"), "neutral", self.msgCard)
        self.msgCard.headerLayout.addStretch(1)
        self.msgCard.headerLayout.addWidget(self.msgAlwaysPill, 0, Qt.AlignVCenter)

        msg_body = QVBoxLayout()
        msg_body.setContentsMargins(0, 0, 0, 0)
        msg_body.setSpacing(6)
        self.msgEdit = PlainTextEdit(self.msgCard)
        self.msgEdit.setFixedHeight(74)
        self.msgEdit.textChanged.connect(self._on_message_changed)
        self.msgHintLabel = CaptionLabel(tr("content.message_hint"), self.msgCard)
        self.msgHintLabel.setWordWrap(True)
        msg_body.addWidget(self.msgEdit)
        msg_body.addWidget(self.msgHintLabel)
        self.msgCard.viewLayout.addLayout(msg_body)
        left_col.addWidget(self.msgCard)

        # 2. Video Share Card
        self.videoCard = HeaderCardWidget(self.view)
        self.videoCard.setTitle(tr("content.video_toggle"))
        self.videoCard.headerView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.videoCard.setMinimumWidth(0)
        self.videoBadge = StatusPill("", "info", self.videoCard)
        self.videoSwitch = SwitchButton(self.videoCard)
        self.videoSwitch.checkedChanged.connect(self._on_video_toggled)
        self.videoCard.headerLayout.addStretch(1)
        self.videoCard.headerLayout.addWidget(self.videoBadge, 0, Qt.AlignVCenter)
        self.videoCard.headerLayout.addSpacing(10)
        self.videoCard.headerLayout.addWidget(self.videoSwitch, 0, Qt.AlignVCenter)

        vid_body = QVBoxLayout()
        vid_body.setContentsMargins(0, 0, 0, 0)
        vid_body.setSpacing(10)

        vid_url_grid = QGridLayout()
        vid_url_grid.setContentsMargins(0, 0, 0, 0)
        vid_url_grid.setHorizontalSpacing(8)
        vid_url_grid.setVerticalSpacing(6)
        vid_url_grid.setColumnStretch(0, 1)
        self.videoUrlEdit = LineEdit(self.videoCard)
        self.videoUrlEdit.setMinimumWidth(0)
        self.videoUrlEdit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.videoUrlEdit.setPlaceholderText(DEFAULT_VIDEO_URL)
        self.videoUrlEdit.textChanged.connect(self._on_video_url_changed)
        self.resolveShortBtn = PushButton(
            FIF.LINK, escape_qt_mnemonic(tr("content.video_resolve")), self.videoCard
        )
        self.resolveShortBtn.clicked.connect(self._resolve_short_link)
        vid_url_grid.addWidget(self.videoUrlEdit, 0, 0)
        vid_url_grid.addWidget(self.resolveShortBtn, 0, 1)

        vid_meta_row = QHBoxLayout()
        vid_meta_row.setContentsMargins(0, 0, 0, 0)
        vid_meta_row.setSpacing(10)
        self.videoHintCaption = CaptionLabel(tr("content.video_hint"), self.videoCard)
        self.videoHintCaption.setWordWrap(True)
        self.videoHintCaption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.shareTimesLabel = BodyLabel(tr("content.share_times_label"), self.videoCard)
        self.shareTimesSpin = FluentSpinBox(self.videoCard)
        self.shareTimesSpin.setRange(1, 10)
        self.shareTimesSpin.setFixedWidth(96)
        self.shareTimesSpin.valueChanged.connect(self._on_share_times_changed)
        vid_meta_row.addWidget(self.videoHintCaption, 1, Qt.AlignVCenter)
        vid_meta_row.addWidget(self.shareTimesLabel, 0, Qt.AlignVCenter)
        vid_meta_row.addWidget(self.shareTimesSpin, 0, Qt.AlignVCenter)

        self.videoHintLabel = CaptionLabel("", self.videoCard)
        self.videoHintLabel.setWordWrap(True)
        self.videoHintLabel.hide()

        vid_body.addLayout(vid_url_grid)
        vid_body.addLayout(vid_meta_row)
        vid_body.addWidget(self.videoHintLabel)
        self.videoCard.viewLayout.addLayout(vid_body)
        left_col.addWidget(self.videoCard)

        # 3. Photo Card
        self.photoCard = HeaderCardWidget(self.view)
        self.photoCard.setTitle(tr("content.photo_toggle"))
        self.photoCard.headerView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.photoCard.setMinimumWidth(0)
        self.photoSwitch = SwitchButton(self.photoCard)
        self.photoSwitch.checkedChanged.connect(self._on_photo_toggled)
        self.photoCard.headerLayout.addStretch(1)
        self.photoCard.headerLayout.addWidget(self.photoSwitch, 0, Qt.AlignVCenter)

        photo_row = QHBoxLayout()
        photo_row.setContentsMargins(0, 0, 0, 0)
        photo_row.setSpacing(14)
        self.photoPreview = ImageLabel(self.photoCard)
        self.photoPreview.setBorderRadius(8, 8, 8, 8)
        self.photoPreview.setFixedSize(68, 68)

        photo_right = QVBoxLayout()
        photo_right.setContentsMargins(0, 0, 0, 0)
        photo_right.setSpacing(8)
        self.photoStatusLabel = BodyLabel("", self.photoCard)
        self.photoStatusLabel.setWordWrap(True)
        self.photoStatusLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        photo_btns = FlowLayout(isTight=True)
        photo_btns.setContentsMargins(0, 0, 0, 0)
        photo_btns.setHorizontalSpacing(8)
        photo_btns.setVerticalSpacing(6)
        self.choosePhotoBtn = PushButton(
            FIF.PHOTO, escape_qt_mnemonic(tr("content.photo_select")), self.photoCard
        )
        self.resetPhotoBtn = TransparentPushButton(
            FIF.ROTATE, escape_qt_mnemonic(tr("content.photo_reset")), self.photoCard
        )
        self.choosePhotoBtn.clicked.connect(self._choose_photo)
        self.resetPhotoBtn.clicked.connect(self._use_default_photo)
        photo_btns.addWidget(self.choosePhotoBtn)
        photo_btns.addWidget(self.resetPhotoBtn)

        photo_right.addWidget(self.photoStatusLabel)
        photo_right.addLayout(photo_btns)
        photo_right.addStretch(1)

        photo_row.addWidget(self.photoPreview, 0, Qt.AlignVCenter)
        photo_row.addLayout(photo_right, 1)
        self.photoCard.viewLayout.addLayout(photo_row)
        left_col.addWidget(self.photoCard)

        # Right Column: Equal-height 1:1 TikTok DM Preview & Streak Pet Tasks
        self.dmPreviewPanel = DMPreviewPanel(self.splitHost)

        self.splitGrid.addWidget(self.leftColWidget, 0, 0)
        self.splitGrid.addWidget(self.dmPreviewPanel, 0, 1)
        self.splitGrid.setColumnStretch(0, 1)
        self.splitGrid.setColumnStretch(1, 0)
        self.vBoxLayout.addWidget(self.splitHost)
        self.vBoxLayout.addStretch(1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        vp_w = self.viewport().width() if self.viewport() else event.size().width()
        stacked = vp_w > 0 and vp_w < 760
        if stacked != self._is_stacked:
            self._is_stacked = stacked
            self.splitGrid.removeWidget(self.leftColWidget)
            self.splitGrid.removeWidget(self.dmPreviewPanel)
            if stacked:
                self.dmPreviewPanel.set_stacked_mode(True)
                self.splitGrid.addWidget(self.leftColWidget, 0, 0)
                self.splitGrid.addWidget(self.dmPreviewPanel, 1, 0)
                self.splitGrid.setColumnStretch(0, 1)
                self.splitGrid.setColumnStretch(1, 0)
            else:
                self.dmPreviewPanel.set_stacked_mode(False)
                self.splitGrid.addWidget(self.leftColWidget, 0, 0)
                self.splitGrid.addWidget(self.dmPreviewPanel, 0, 1)
                self.splitGrid.setColumnStretch(0, 1)
                self.splitGrid.setColumnStretch(1, 0)
            self.splitHost.updateGeometry()
            self.splitGrid.activate()
            self.vBoxLayout.activate()

    # -- Load & Sync -----------------------------------------------------
    def _first_sender_handle(self) -> str:
        acc = self._state.accounts[0] if self._state.accounts else None
        if acc is None:
            return ""
        return str(acc.username or acc.name or "").strip()

    def _first_friend_handle(self) -> str:
        active = (
            list(self._state.accounts)
            if self._state.multi_account
            else ([self._state.accounts[0]] if self._state.accounts else [])
        )
        for acc in active:
            if acc.friends:
                return str(acc.friends[0])
        return ""

    def _update_live_preview(self) -> None:
        cur_url = self.videoUrlEdit.text().strip()
        cached = self._video_preview_cache.get(cur_url)
        eff_author = (
            (cached or {}).get("author_nickname")
            or (cached or {}).get("author")
            or self._verified_video_nick
            or self._verified_video_author
        )
        pts = self.dmPreviewPanel.update_preview(
            message=self.msgEdit.toPlainText(),
            video_enabled=self.videoSwitch.isChecked(),
            video_url=cur_url,
            share_times=int(self.shareTimesSpin.value()),
            photo_enabled=self.photoSwitch.isChecked(),
            photo_path=self._state.photo_path,
            friend_handle=self._first_friend_handle(),
            video_author=str(eff_author or ""),
            video_verified=(self._video_verify_state == "available"),
            sender_handle=self._first_sender_handle(),
        )
        if self.videoSwitch.isChecked() and isinstance(cached, dict):
            self.dmPreviewPanel.set_video_media(
                cover_bytes=cached.get("cover_bytes"),
                avatar_bytes=cached.get("avatar_bytes"),
                author_nickname=str(eff_author or ""),
            )
        else:
            self.dmPreviewPanel.clear_video_media()

        if (
            not self._smoke_test
            and self.videoSwitch.isChecked()
            and cur_url
            and cur_url not in self._video_preview_cache
            and validate_video_url(cur_url, resolve_short=False)["valid"]
        ):
            self._preview_fetch_timer.start()

        level = "success" if pts >= 5 else ("info" if pts >= 1 else "warning")
        self.petGrowthBadge.setState(level, tr("content.pet_pts_badge", pts=pts))
        self.petGrowthBadge.adjustSize()

    def _fetch_video_preview_media(self, meta: dict[str, Any] | None = None) -> None:
        raw = self.videoUrlEdit.text().strip()
        if not raw or not self.videoSwitch.isChecked() or self._smoke_test:
            return
        if raw in self._video_preview_cache and self._video_preview_cache[raw].get("cover_bytes"):
            return
        self._workers.start_task(
            fetch_tiktok_video_preview,
            raw,
            meta=meta,
            task_key="fetch_video_preview",
            on_finished=lambda res, req_url=raw: self._on_video_preview_fetched(req_url, res),
            on_error=lambda _err: None,
        )

    def _on_video_preview_fetched(self, req_url: str, res: dict[str, Any]) -> None:
        if not isinstance(res, dict):
            return
        res_url = str(res.get("url") or req_url).strip()
        if res.get("cover_bytes") or res.get("avatar_bytes") or res.get("author_nickname"):
            self._video_preview_cache[req_url] = res
            if res_url:
                self._video_preview_cache[res_url] = res
        cur = self.videoUrlEdit.text().strip()
        if cur not in (req_url, res_url):
            return
        self.dmPreviewPanel.set_video_media(
            cover_bytes=res.get("cover_bytes"),
            avatar_bytes=res.get("avatar_bytes"),
            author_nickname=str(res.get("author_nickname") or res.get("author") or ""),
        )

    def refresh_from_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        self._loading = True
        try:
            if self.msgEdit.toPlainText() != st.message:
                self.msgEdit.setPlainText(st.message)

            self.videoSwitch.setChecked(bool(st.video_enabled))
            if self.videoUrlEdit.text() != st.video_url:
                self.videoUrlEdit.setText(st.video_url)
            self.shareTimesSpin.setValue(max(1, min(10, int(st.share_times or 2))))
            self._sync_video_controls()

            self.photoSwitch.setChecked(bool(st.photo_enabled))
            self._sync_photo_preview()
            self._update_live_preview()
        finally:
            self._loading = False

    def _emit_state_changed(self) -> None:
        self.state_changed.emit()

    def _schedule_save(self) -> None:
        if not self._loading:
            self._save_debounce.start()

    def flush_edits(self) -> None:
        if self._save_debounce.isActive():
            self._save_debounce.stop()
            self.state_changed.emit()

    # -- Message / Video / Photo handlers --------------------------------
    def _on_message_changed(self) -> None:
        if not self._loading:
            self._state.message = self.msgEdit.toPlainText()
            self._schedule_save()
        self._update_live_preview()

    def _on_video_toggled(self, checked: bool) -> None:
        if not self._loading:
            self._state.video_enabled = bool(checked)
            self.state_changed.emit()
        self._sync_video_controls()
        self._update_live_preview()

    def _on_video_url_changed(self, text: str) -> None:
        clean = text.strip()
        if clean != self._verified_video_url:
            self._video_verify_state = "unverified"
            self._verified_video_author = ""
            self._verified_video_nick = ""
            self._video_verify_error = ""
        if not self._loading:
            self._state.video_url = clean
            self._schedule_save()
        self._sync_video_controls()
        self._update_live_preview()

    def _on_share_times_changed(self, val: int) -> None:
        if not self._loading:
            self._state.share_times = int(val)
            self._schedule_save()
        self._update_live_preview()

    def _sync_video_controls(self) -> None:
        enabled = self.videoSwitch.isChecked()
        self.videoUrlEdit.setEnabled(enabled)
        self.resolveShortBtn.setEnabled(enabled)
        self.shareTimesSpin.setEnabled(enabled)

        if not enabled:
            self.videoBadge.setState("neutral", tr("common.disabled"))
            self.videoHintLabel.setText(tr("content.video_disabled"))
            self.videoHintLabel.hide()
            return

        cur_url = self.videoUrlEdit.text().strip()
        res = validate_video_url(cur_url, resolve_short=False)
        if not res["valid"]:
            self.videoBadge.setState("warning", tr("common.warning"))
            err_txt = str(res.get("error") or "")
            self.videoHintLabel.setText(err_txt)
            self.videoHintLabel.setVisible(bool(err_txt))
            return

        if cur_url == self._verified_video_url:
            if self._video_verify_state == "available":
                badge_txt = (
                    tr("content.video_verified_author", author=self._verified_video_author)
                    if self._verified_video_author
                    else tr("content.video_valid", aweme_id=res["aweme_id"])
                )
                self.videoBadge.setState("success", badge_txt)
                self.videoHintLabel.hide()
                return
            if self._video_verify_state in ("unavailable", "private", "share_disabled"):
                self.videoBadge.setState("error", tr("content.video_unavailable"))
                self.videoHintLabel.setText(self._video_verify_error)
                self.videoHintLabel.setVisible(bool(self._video_verify_error))
                return
            if self._video_verify_state == "offline_warning":
                self.videoBadge.setState("warning", tr("content.video_verify_offline"))
                self.videoHintLabel.setText(self._video_verify_error)
                self.videoHintLabel.setVisible(bool(self._video_verify_error))
                return

        self.videoBadge.setState("neutral", tr("content.video_unverified"))
        self.videoHintLabel.hide()

    def _resolve_short_link(self) -> None:
        raw = self.videoUrlEdit.text().strip()
        if not raw:
            return
        self.resolveShortBtn.setEnabled(False)
        self.resolveShortBtn.setText(escape_qt_mnemonic(tr("accounts.verifying")))
        self._workers.start_task(
            validate_video_url,
            raw,
            resolve_short=True,
            verify_availability=True,
            task_key="resolve_short_link",
            on_finished=self._on_short_resolved,
            on_error=self._on_short_error,
        )

    def _on_short_resolved(self, res: dict[str, Any]) -> None:
        self.resolveShortBtn.setEnabled(self.videoSwitch.isChecked())
        self.resolveShortBtn.setText(escape_qt_mnemonic(tr("content.video_resolve")))
        avail = str(res.get("availability") or "")
        cur_url = self.videoUrlEdit.text().strip()

        if avail == "available" and res.get("valid") and res.get("verified"):
            clean_url = str(res.get("normalized_url") or cur_url).strip()
            author = str(res.get("author") or "").strip()
            author_nick = str(res.get("author_nickname") or author).strip()
            self._video_verify_state = "available"
            self._verified_video_url = clean_url
            self._verified_video_author = author
            self._verified_video_nick = author_nick
            self._video_verify_error = ""
            if clean_url and self.videoUrlEdit.text().strip() != clean_url:
                self.videoUrlEdit.setText(clean_url)
            else:
                self._sync_video_controls()
                self._update_live_preview()
            if not self._smoke_test and res.get("cover_url"):
                self._fetch_video_preview_media(meta=res)
            msg = (
                tr("content.video_verified_author", author=author)
                if author
                else tr("content.video_valid", aweme_id=res.get("aweme_id") or "")
            )
            InfoBar.success(
                tr("common.success"),
                msg,
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if avail in ("unavailable", "private", "share_disabled"):
            err_msg = str(res.get("error") or tr("content.video_unavailable"))
            self._video_verify_state = avail
            self._verified_video_url = cur_url
            self._verified_video_author = str(res.get("author") or "").strip()
            self._video_verify_error = err_msg
            self._sync_video_controls()
            self._update_live_preview()
            InfoBar.error(
                tr("content.video_unavailable"),
                err_msg,
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if avail == "unverified" and res.get("valid"):
            warn_msg = str(res.get("error") or tr("content.video_verify_offline"))
            self._video_verify_state = "offline_warning"
            self._verified_video_url = cur_url
            self._verified_video_author = ""
            self._video_verify_error = warn_msg
            self._sync_video_controls()
            self._update_live_preview()
            InfoBar.warning(
                tr("content.video_verify_offline"),
                warn_msg,
                duration=4500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        self._video_verify_state = "unverified"
        self._verified_video_url = ""
        self._verified_video_author = ""
        self._video_verify_error = ""
        self._sync_video_controls()
        self._update_live_preview()
        InfoBar.warning(
            tr("common.warning"),
            str(res.get("error") or ""),
            duration=4500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_short_error(self, err: str) -> None:
        self.resolveShortBtn.setEnabled(self.videoSwitch.isChecked())
        self.resolveShortBtn.setText(escape_qt_mnemonic(tr("content.video_resolve")))
        InfoBar.error(
            tr("common.error"),
            err,
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_photo_toggled(self, checked: bool) -> None:
        if not self._loading:
            self._state.photo_enabled = bool(checked)
            self.state_changed.emit()
        self._sync_photo_preview()
        self._update_live_preview()

    def _choose_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("content.photo_select"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if not path:
            return
        self._state.photo_path = path
        self._state.photo_enabled = True
        self._loading = True
        self.photoSwitch.setChecked(True)
        self._loading = False
        self._sync_photo_preview()
        self._update_live_preview()
        self.state_changed.emit()

    def _use_default_photo(self) -> None:
        self._state.photo_path = ""
        self._state.photo_enabled = True
        self._loading = True
        self.photoSwitch.setChecked(True)
        self._loading = False
        self._sync_photo_preview()
        self._update_live_preview()
        self.state_changed.emit()

    def _clear_reset_photo(self) -> None:
        self._state.photo_path = ""
        self._state.photo_enabled = False
        self._loading = True
        self.photoSwitch.setChecked(False)
        self._loading = False
        self._sync_photo_preview()
        self._update_live_preview()
        self.state_changed.emit()

    def _sync_photo_preview(self) -> None:
        enabled = self.photoSwitch.isChecked()
        self.choosePhotoBtn.setEnabled(True)
        self.resetPhotoBtn.setEnabled(True)

        if not enabled:
            self.photoPreview.setImage(None)
            self.photoStatusLabel.setText(tr("content.photo_disabled"))
            return

        custom = self._state.photo_path.strip()
        target = _resolve_asset_path(custom or DEFAULT_PHOTO_PATH)
        if Path(target).is_file():
            self.photoPreview.setImage(target)
            self.photoPreview.scaledToHeight(64)
        else:
            self.photoPreview.setImage(None)

        if custom:
            self.photoStatusLabel.setText(tr("content.photo_custom", path=Path(custom).name))
        else:
            self.photoStatusLabel.setText(tr("content.photo_default"))

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("content.title"))
        self.subtitleLabel.setText(tr("content.subtitle"))
        self.nextAutomationBtn.setText(escape_qt_mnemonic(tr("content.next_automation")))
        self.msgCard.setTitle(tr("content.message_label"))
        self.msgAlwaysPill.setState("neutral", tr("content.always_sent"))
        self.msgHintLabel.setText(tr("content.message_hint"))
        self.videoCard.setTitle(tr("content.video_toggle"))
        self.resolveShortBtn.setText(escape_qt_mnemonic(tr("content.video_resolve")))
        self.videoHintCaption.setText(tr("content.video_hint"))
        self.shareTimesLabel.setText(tr("content.share_times_label"))
        self.photoCard.setTitle(tr("content.photo_toggle"))
        self.choosePhotoBtn.setText(escape_qt_mnemonic(tr("content.photo_select")))
        self.resetPhotoBtn.setText(escape_qt_mnemonic(tr("content.photo_reset")))
        self._sync_video_controls()
        self._sync_photo_preview()
        self.dmPreviewPanel.retranslate()
        self._update_live_preview()


__all__ = [
    "ContentPage",
    "_build_cloud_secrets",
    "_deploy_github_task",
    "_discover_bot_repos_task",
    "_parse_repo_input",
    "_pause_multiple_bot_repos_task",
    "_toggle_bot_repo_workflow_task",
    "_validate_pat_task",
]
