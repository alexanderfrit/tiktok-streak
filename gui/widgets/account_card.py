"""Fluent card widget representing one TikTok account configuration."""
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
    CheckBox,
    FlowLayout,
    FluentIcon as FIF,
    HeaderCardWidget,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TransparentPushButton,
    TransparentToolButton,
)

from gui.services.i18n import tr
from gui.services.storage import AccountProfile
from gui.services.theme import escape_qt_mnemonic
try:
    from gui.services.tiktok_auth import check_cloud_cookie_readiness
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
from gui.services.validators import (
    export_friends_csv,
    import_friends_csv,
    normalize_friends,
)
from gui.widgets.chip_list import ChipFlowWidget
from gui.widgets.fluent_spin_box import FluentSpinBox
from gui.widgets.status_pill import StatusPill


def _inbox_badge_state(profile: AccountProfile, has_cookie: bool) -> tuple[str, InfoLevel | str]:
    method = (profile.auth_method or "").strip().lower()
    if method in ("business_suite", "guided_blocked"):
        return "blocked", InfoLevel.ERROR
    if method in ("expired", "verify_expired"):
        return "expired", InfoLevel.WARNING
    if profile.inbox_verified and has_cookie:
        return "verified", InfoLevel.SUCCESS
    return "unchecked", "neutral"


class AccountCard(HeaderCardWidget):
    """Card for editing a single TikTok account's auth, friends, and overrides."""

    changed = Signal(str)
    request_guided_login = Signal(str)
    request_import_browser = Signal(str)
    request_paste_cookie = Signal(str)
    request_verify_inbox = Signal(str)
    request_remove = Signal(str)

    def __init__(
        self,
        profile: AccountProfile,
        has_cookie: bool = False,
        multi_account: bool = False,
        can_remove: bool = False,
        cookie_payload: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self._has_cookie = bool(has_cookie)
        self._multi_account = bool(multi_account)
        self._can_remove = bool(can_remove)
        self._loading = True
        self._overrides_expanded = False
        self._verifying = False
        self._cookie_count: int | None = None
        self._cookie_cloud_ready: bool | None = None
        self._last_add_warnings: list[str] = []

        self._friends_debounce = QTimer(self)
        self._friends_debounce.setSingleShot(True)
        self._friends_debounce.setInterval(250)
        self._friends_debounce.timeout.connect(self._commit_friends)

        self._build_ui()
        self.load_profile(
            profile,
            has_cookie=has_cookie,
            multi_account=multi_account,
            can_remove=can_remove,
            cookie_payload=cookie_payload,
        )

    def _build_ui(self) -> None:
        # Header: Title left, identity/status badges + Verify Session + Remove right
        self.headerView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.usernameBadge = StatusPill("", "neutral", self, show_dot=False)
        self.inboxBadge = StatusPill("", "neutral", self)
        self.verifyInboxBtn = PushButton(
            FIF.CHECKBOX, escape_qt_mnemonic(tr("accounts.verify_inbox")), self
        )
        self.verifyInboxBtn.setFixedHeight(30)
        self.verifyInboxBtn.clicked.connect(lambda: self.request_verify_inbox.emit(self.profile.id))
        self.removeBtn = TransparentToolButton(FIF.DELETE, self)
        self.removeBtn.setToolTip(tr("common.delete"))
        self.removeBtn.clicked.connect(lambda: self.request_remove.emit(self.profile.id))

        self.headerLayout.addSpacing(8)
        self.headerLayout.addWidget(self.usernameBadge, 0, Qt.AlignVCenter)
        self.headerLayout.addSpacing(6)
        self.headerLayout.addWidget(self.inboxBadge, 0, Qt.AlignVCenter)
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.verifyInboxBtn, 0, Qt.AlignVCenter)
        self.headerLayout.addSpacing(6)
        self.headerLayout.addWidget(self.removeBtn, 0, Qt.AlignVCenter)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        # Account label row
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        self.nameCaption = BodyLabel(tr("accounts.account_name"), self)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setMinimumWidth(200)
        self.nameEdit.setMaximumWidth(260)
        self.nameEdit.textChanged.connect(self._on_name_changed)
        self.authDetailLabel = CaptionLabel("", self)
        self.authDetailLabel.setWordWrap(True)
        self.verifySpinner = IndeterminateProgressRing(self)
        self.verifySpinner.setFixedSize(18, 18)
        self.verifySpinner.stop()
        self.verifySpinner.hide()
        self.verifyStatusLabel = CaptionLabel("", self)
        self.verifyStatusLabel.setWordWrap(True)
        self.verifyStatusLabel.hide()

        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(6)
        detail_row.addWidget(self.verifySpinner, 0, Qt.AlignVCenter)
        detail_row.addWidget(self.verifyStatusLabel, 0, Qt.AlignVCenter)
        detail_row.addWidget(self.authDetailLabel, 1, Qt.AlignVCenter)

        name_row.addWidget(self.nameCaption)
        name_row.addWidget(self.nameEdit)
        name_row.addSpacing(8)
        name_row.addLayout(detail_row, 1)
        body.addLayout(name_row)

        # Crisp 3-button Auth action row (Verify Session moved cleanly to card header)
        auth_flow = FlowLayout(isTight=True)
        auth_flow.setContentsMargins(0, 0, 0, 0)
        auth_flow.setHorizontalSpacing(8)
        auth_flow.setVerticalSpacing(8)
        self.guidedLoginBtn = PrimaryPushButton(
            FIF.GLOBE, escape_qt_mnemonic(tr("accounts.sign_in_browser")), self
        )
        self.importBrowserBtn = PushButton(
            FIF.DOWNLOAD, escape_qt_mnemonic(tr("accounts.import_browser")), self
        )
        self.pasteCookieBtn = PushButton(
            FIF.PASTE, escape_qt_mnemonic(tr("accounts.paste_cookie")), self
        )

        self.guidedLoginBtn.clicked.connect(lambda: self.request_guided_login.emit(self.profile.id))
        self.importBrowserBtn.clicked.connect(lambda: self.request_import_browser.emit(self.profile.id))
        self.pasteCookieBtn.clicked.connect(lambda: self.request_paste_cookie.emit(self.profile.id))

        auth_flow.addWidget(self.guidedLoginBtn)
        auth_flow.addWidget(self.importBrowserBtn)
        auth_flow.addWidget(self.pasteCookieBtn)
        body.addLayout(auth_flow)

        # Friends section (tag/chip input bar + removable friend chips + CSV import/export)
        friends_header = QHBoxLayout()
        friends_header.setSpacing(8)
        self.friendsLabel = StrongBodyLabel(tr("content.friends_label"), self)
        self.friendsCountBadge = StatusPill("0", "neutral", self, show_dot=False)
        self.importCsvBtn = TransparentPushButton(
            FIF.FOLDER_ADD, escape_qt_mnemonic(tr("content.import_csv")), self
        )
        self.exportCsvBtn = TransparentPushButton(
            FIF.SAVE_AS, escape_qt_mnemonic(tr("content.export_csv")), self
        )

        self.importCsvBtn.clicked.connect(self._import_csv)
        self.exportCsvBtn.clicked.connect(self._export_csv)

        friends_header.addWidget(self.friendsLabel, 0, Qt.AlignVCenter)
        friends_header.addWidget(self.friendsCountBadge, 0, Qt.AlignVCenter)
        friends_header.addStretch(1)
        friends_header.addWidget(self.importCsvBtn, 0, Qt.AlignVCenter)
        friends_header.addWidget(self.exportCsvBtn, 0, Qt.AlignVCenter)
        body.addLayout(friends_header)

        friend_input_row = QHBoxLayout()
        friend_input_row.setContentsMargins(0, 0, 0, 0)
        friend_input_row.setSpacing(8)
        self.friendInputEdit = LineEdit(self)
        self.friendInputEdit.setMinimumWidth(0)
        self.friendInputEdit.setClearButtonEnabled(True)
        self.friendInputEdit.setPlaceholderText(tr("content.friend_input_placeholder"))
        self.friendInputEdit.returnPressed.connect(self._add_friend_from_input)
        self.addFriendBtn = PushButton(
            FIF.ADD, escape_qt_mnemonic(tr("content.add_friend_btn")), self
        )
        self.addFriendBtn.clicked.connect(self._add_friend_from_input)
        friend_input_row.addWidget(self.friendInputEdit, 1)
        friend_input_row.addWidget(self.addFriendBtn)
        body.addLayout(friend_input_row)

        self.friendsChipBox = ChipFlowWidget(
            empty_text=tr("content.friends_empty"),
            parent=self,
        )
        self.friendsChipBox.set_remove_tooltip(tr("common.remove"))
        self.friendsChipBox.item_removed.connect(self._remove_friend_chip)
        body.addWidget(self.friendsChipBox)

        # Hidden backing PlainTextEdit kept in two-way sync for backward compatibility
        self.friendsEdit = PlainTextEdit(self)
        self.friendsEdit.setPlaceholderText(tr("content.friends_placeholder"))
        self.friendsEdit.hide()
        self.friendsEdit.textChanged.connect(self._on_friends_text_changed)

        self.friendsWarningLabel = CaptionLabel("", self)
        self.friendsWarningLabel.setStyleSheet("color: #c77c00;")
        self.friendsWarningLabel.setWordWrap(True)
        self.friendsWarningLabel.hide()
        body.addWidget(self.friendsWarningLabel)

        # Per-account overrides container (visible in multi-account mode; drawer collapsed by default)
        self.overridesContainer = QWidget(self)
        ov_layout = QVBoxLayout(self.overridesContainer)
        ov_layout.setContentsMargins(0, 6, 0, 0)
        ov_layout.setSpacing(8)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(8)
        self.overridesToggleBtn = PushButton(
            FIF.CHEVRON_RIGHT, escape_qt_mnemonic(tr("accounts.override_toggle")), self.overridesContainer
        )
        self.overridesToggleBtn.clicked.connect(self._toggle_overrides_drawer)
        toggle_row.addWidget(self.overridesToggleBtn)
        toggle_row.addStretch(1)
        ov_layout.addLayout(toggle_row)

        self.overridesDrawer = QWidget(self.overridesContainer)
        drawer_lay = QVBoxLayout(self.overridesDrawer)
        drawer_lay.setContentsMargins(0, 4, 0, 0)
        drawer_lay.setSpacing(8)

        self.overridesHint = CaptionLabel(tr("accounts.override_hint"), self.overridesDrawer)
        self.overridesHint.setWordWrap(True)
        drawer_lay.addWidget(self.overridesHint)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        # Row 0: Message override
        self.ovMsgCheck = CheckBox(escape_qt_mnemonic(tr("accounts.override_message")), self.overridesDrawer)
        self.ovMsgEdit = LineEdit(self.overridesDrawer)
        self.ovMsgEdit.setMinimumWidth(0)
        self.ovMsgCheck.stateChanged.connect(self._on_override_changed)
        self.ovMsgEdit.textChanged.connect(self._on_override_changed)
        grid.addWidget(self.ovMsgCheck, 0, 0)
        grid.addWidget(self.ovMsgEdit, 0, 1, 1, 3)

        # Row 1: Video override
        self.ovVideoCheck = CheckBox(escape_qt_mnemonic(tr("accounts.override_video")), self.overridesDrawer)
        self.ovVideoDisableCheck = CheckBox(
            escape_qt_mnemonic(tr("accounts.disable_for_account")), self.overridesDrawer
        )
        self.ovVideoEdit = LineEdit(self.overridesDrawer)
        self.ovVideoEdit.setMinimumWidth(0)
        self.ovVideoEdit.setPlaceholderText("https://www.tiktok.com/@user/video/...")
        shares_box = QHBoxLayout()
        shares_box.setContentsMargins(0, 0, 0, 0)
        shares_box.setSpacing(6)
        self.ovSharesLabel = CaptionLabel(tr("accounts.override_shares"), self.overridesDrawer)
        self.ovSharesSpin = FluentSpinBox(self.overridesDrawer)
        self.ovSharesSpin.setRange(0, 10)
        shares_box.addWidget(self.ovSharesLabel)
        shares_box.addWidget(self.ovSharesSpin)
        self.ovVideoCheck.stateChanged.connect(self._on_override_changed)
        self.ovVideoDisableCheck.stateChanged.connect(self._on_override_changed)
        self.ovVideoEdit.textChanged.connect(self._on_override_changed)
        self.ovSharesSpin.valueChanged.connect(self._on_override_changed)
        grid.addWidget(self.ovVideoCheck, 1, 0)
        grid.addWidget(self.ovVideoEdit, 1, 1)
        grid.addLayout(shares_box, 1, 2)
        grid.addWidget(self.ovVideoDisableCheck, 1, 3)

        # Row 2: Photo override
        self.ovPhotoCheck = CheckBox(escape_qt_mnemonic(tr("accounts.override_photo")), self.overridesDrawer)
        self.ovPhotoDisableCheck = CheckBox(
            escape_qt_mnemonic(tr("accounts.disable_for_account")), self.overridesDrawer
        )
        self.ovPhotoEdit = LineEdit(self.overridesDrawer)
        self.ovPhotoEdit.setMinimumWidth(0)
        self.ovPhotoEdit.setPlaceholderText("assets/custom.png")
        self.ovPhotoBrowseBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("common.browse")), self.overridesDrawer
        )
        self.ovPhotoCheck.stateChanged.connect(self._on_override_changed)
        self.ovPhotoDisableCheck.stateChanged.connect(self._on_override_changed)
        self.ovPhotoEdit.textChanged.connect(self._on_override_changed)
        self.ovPhotoBrowseBtn.clicked.connect(self._browse_override_photo)
        grid.addWidget(self.ovPhotoCheck, 2, 0)
        grid.addWidget(self.ovPhotoEdit, 2, 1)
        grid.addWidget(self.ovPhotoBrowseBtn, 2, 2)
        grid.addWidget(self.ovPhotoDisableCheck, 2, 3)

        drawer_lay.addLayout(grid)
        self.overridesDrawer.hide()
        ov_layout.addWidget(self.overridesDrawer)
        body.addWidget(self.overridesContainer)

        self.viewLayout.addLayout(body)

    def load_profile(
        self,
        profile: AccountProfile,
        has_cookie: bool | None = None,
        multi_account: bool | None = None,
        can_remove: bool | None = None,
        cookie_payload: str | None = None,
    ) -> None:
        self._loading = True
        try:
            self.profile = profile
            if has_cookie is not None:
                self._has_cookie = bool(has_cookie)
                if not self._has_cookie:
                    self._cookie_count = None
                    self._cookie_cloud_ready = None
            if cookie_payload is not None:
                self._apply_cookie_summary(cookie_payload)
            if multi_account is not None:
                self._multi_account = bool(multi_account)
            if can_remove is not None:
                self._can_remove = bool(can_remove)

            self.setTitle(profile.name or "Main")
            if self.nameEdit.text() != (profile.name or ""):
                self.nameEdit.setText(profile.name or "Main")

            friends_txt = "\n".join(f"@{f}" for f in profile.friends)
            if self.friendsEdit.toPlainText().strip() != friends_txt.strip():
                self.friendsEdit.setPlainText(friends_txt)

            # Overrides
            has_msg_ov = profile.message_override is not None
            self.ovMsgCheck.setChecked(has_msg_ov)
            self.ovMsgEdit.setText(profile.message_override or "")

            has_vid_ov = profile.video_url_override is not None or profile.share_times_override is not None
            self.ovVideoCheck.setChecked(has_vid_ov)
            vid_disabled = profile.video_url_override == "" or (
                profile.video_url_override is not None and profile.video_url_override.lower() in ("none", "off", "disabled")
            )
            self.ovVideoDisableCheck.setChecked(bool(has_vid_ov and vid_disabled))
            self.ovVideoEdit.setText("" if vid_disabled else (profile.video_url_override or ""))
            self.ovSharesSpin.setValue(profile.share_times_override if profile.share_times_override is not None else 2)

            has_ph_ov = profile.photo_path_override is not None
            self.ovPhotoCheck.setChecked(has_ph_ov)
            ph_disabled = profile.photo_path_override == "" or (
                profile.photo_path_override is not None and profile.photo_path_override.lower() in ("none", "off", "disabled")
            )
            self.ovPhotoDisableCheck.setChecked(bool(has_ph_ov and ph_disabled))
            self.ovPhotoEdit.setText("" if ph_disabled else (profile.photo_path_override or ""))

            self._sync_override_enabled_states()
            has_active_override = (
                profile.message_override is not None
                or profile.video_url_override is not None
                or profile.share_times_override is not None
                or profile.photo_path_override is not None
            )
            self._set_overrides_expanded(has_active_override)
            self.overridesContainer.setVisible(self._multi_account)
            self.removeBtn.setVisible(self._can_remove)
            self._refresh_badges()
            self._update_friends_preview()
        finally:
            self._loading = False

    def _set_overrides_expanded(self, expanded: bool) -> None:
        self._overrides_expanded = bool(expanded)
        self.overridesDrawer.setVisible(self._overrides_expanded)
        self.overridesToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._overrides_expanded else FIF.CHEVRON_RIGHT
        )

    def _toggle_overrides_drawer(self) -> None:
        self._set_overrides_expanded(not self._overrides_expanded)

    def _apply_cookie_summary(self, cookie_payload: str | None) -> None:
        cleaned = str(cookie_payload or "").strip()
        if not cleaned:
            self._cookie_count = 0
            self._cookie_cloud_ready = None
            return
        readiness = check_cloud_cookie_readiness(cleaned)
        count = int(readiness.get("count") or 0)
        self._cookie_count = count
        self._cookie_cloud_ready = bool(readiness.get("cloud_ready", False)) if count > 0 else None

    def set_cookie_summary(self, cookie_payload: str | None) -> None:
        self._apply_cookie_summary(cookie_payload)
        self._refresh_badges()

    def set_cookie_present(self, has_cookie: bool) -> None:
        self._has_cookie = bool(has_cookie)
        if not self._has_cookie:
            self._cookie_count = None
            self._cookie_cloud_ready = None
        self._refresh_badges()

    def set_verifying(self, active: bool, status_text: str = "") -> None:
        self._verifying = bool(active)
        if self._verifying:
            self.verifySpinner.show()
            self.verifySpinner.start()
            msg = str(status_text or tr("accounts.verifying")).strip()
            self.verifyStatusLabel.setText(msg)
            self.verifyStatusLabel.show()
            self.authDetailLabel.hide()
            self.verifyInboxBtn.setEnabled(False)
            self.verifyInboxBtn.setText(escape_qt_mnemonic(tr("accounts.verifying")))
            self.inboxBadge.setState("info", tr("accounts.verifying"), show_dot=True)
            self.inboxBadge.adjustSize()
        else:
            self.verifySpinner.stop()
            self.verifySpinner.hide()
            cleaned = str(status_text or "").strip()
            self.verifyStatusLabel.setText(cleaned)
            self.verifyStatusLabel.setVisible(bool(cleaned))
            self.authDetailLabel.show()
            self.verifyInboxBtn.setText(escape_qt_mnemonic(tr("accounts.verify_inbox")))
            self._refresh_badges()

    def set_verify_status(self, text: str) -> None:
        cleaned = str(text or "").strip()
        self.verifyStatusLabel.setText(cleaned)
        self.verifyStatusLabel.setVisible(bool(cleaned))

    def _refresh_badges(self) -> None:
        # usernameBadge is an identity tag (neutral without dot when handle exists)
        if self._has_cookie and self.profile.username:
            self.usernameBadge.setState("neutral", f"@{self.profile.username.lstrip('@')}", show_dot=False)
        elif self._has_cookie:
            self.usernameBadge.setState("neutral", tr("accounts.has_tiktok_badge"), show_dot=False)
        else:
            self.usernameBadge.setState(InfoLevel.WARNING, tr("accounts.no_session"), show_dot=True)
        self.usernameBadge.adjustSize()

        if self._verifying:
            self.inboxBadge.setState("info", tr("accounts.verifying"), show_dot=True)
        else:
            state_key, level = _inbox_badge_state(self.profile, self._has_cookie)
            if state_key == "verified" and self._cookie_cloud_ready is False:
                self.inboxBadge.setState(InfoLevel.WARNING, tr("accounts.local_only_badge"), show_dot=True)
            elif state_key == "verified" and self._cookie_cloud_ready is True:
                self.inboxBadge.setState(InfoLevel.SUCCESS, tr("accounts.cloud_ready_badge"), show_dot=True)
            else:
                inbox_key = {
                    "verified": "accounts.inbox_verified",
                    "blocked": "accounts.inbox_blocked",
                    "expired": "accounts.inbox_expired",
                }.get(state_key, "accounts.inbox_unchecked")
                self.inboxBadge.setState(level, tr(inbox_key), show_dot=True)
        self.inboxBadge.adjustSize()

        detail_parts = []
        if self.profile.auth_method:
            detail_parts.append(self.profile.auth_method)
        if self.profile.last_checked:
            detail_parts.append(self.profile.last_checked)
        if self._has_cookie and self._cookie_count and self._cookie_count > 0 and self._cookie_cloud_ready is not None:
            key = "accounts.cookies_cloud_ready" if self._cookie_cloud_ready else "accounts.cookies_local_only"
            detail_parts.append(tr(key, count=self._cookie_count))
        self.authDetailLabel.setText(" • ".join(detail_parts))
        self.verifyInboxBtn.setEnabled(self._has_cookie and not self._verifying)

    def _sync_override_enabled_states(self) -> None:
        self.ovMsgEdit.setEnabled(self.ovMsgCheck.isChecked())

        vid_on = self.ovVideoCheck.isChecked()
        vid_dis = self.ovVideoDisableCheck.isChecked()
        self.ovVideoDisableCheck.setEnabled(vid_on)
        self.ovVideoEdit.setEnabled(vid_on and not vid_dis)
        self.ovSharesSpin.setEnabled(vid_on and not vid_dis)

        ph_on = self.ovPhotoCheck.isChecked()
        ph_dis = self.ovPhotoDisableCheck.isChecked()
        self.ovPhotoDisableCheck.setEnabled(ph_on)
        self.ovPhotoEdit.setEnabled(ph_on and not ph_dis)
        self.ovPhotoBrowseBtn.setEnabled(ph_on and not ph_dis)

    def _on_name_changed(self, text: str) -> None:
        if self._loading:
            return
        cleaned = text.strip() or "Main"
        self.profile.name = cleaned
        self.setTitle(cleaned)
        self.changed.emit(self.profile.id)

    def _add_friend_from_input(self) -> None:
        raw = self.friendInputEdit.text().strip()
        if not raw:
            return
        new_handles, warnings = normalize_friends(raw)
        self._last_add_warnings = list(warnings)
        if new_handles:
            current_handles, _ = normalize_friends(self.friendsEdit.toPlainText())
            merged, _ = normalize_friends("\n".join([*current_handles, *new_handles]))
            self.friendInputEdit.clear()
            self.friendsEdit.setPlainText("\n".join(f"@{h}" for h in merged))
            self._commit_friends()
        else:
            self._update_friends_preview()

    def _remove_friend_chip(self, handle_key: str) -> None:
        target = str(handle_key or "").strip().lstrip("@").lower()
        if not target:
            return
        current_handles, _ = normalize_friends(self.friendsEdit.toPlainText())
        remaining = [h for h in current_handles if h.lower() != target]
        self._last_add_warnings = []
        self.friendsEdit.setPlainText("\n".join(f"@{h}" for h in remaining))
        self._commit_friends()

    def _on_friends_text_changed(self) -> None:
        self._update_friends_preview()
        if not self._loading:
            self._friends_debounce.start()

    def _update_friends_preview(self) -> list[str]:
        handles, warnings = normalize_friends(self.friendsEdit.toPlainText())
        all_warnings = [*warnings, *(w for w in self._last_add_warnings if w not in warnings)]
        self.friendsChipBox.set_items([(h, f"@{h}") for h in handles])
        self.friendsCountBadge.setState(
            "neutral",
            tr("accounts.friends_count", count=len(handles)),
            show_dot=False,
        )
        self.friendsCountBadge.adjustSize()

        if all_warnings:
            self.friendsWarningLabel.setText(" • ".join(all_warnings[:2]))
            self.friendsWarningLabel.show()
        else:
            self.friendsWarningLabel.hide()
        return handles

    def _commit_friends(self) -> None:
        handles = self._update_friends_preview()
        if self.profile.friends != handles:
            self.profile.friends = handles
            self.changed.emit(self.profile.id)

    def flush_edits(self) -> None:
        """Synchronously apply any debounced or pending friend input edits to ``self.profile``."""
        pending = self.friendInputEdit.text().strip()
        if pending:
            new_handles, warnings = normalize_friends(pending)
            if new_handles and not warnings:
                self.friendInputEdit.clear()
                current_handles, _ = normalize_friends(self.friendsEdit.toPlainText())
                merged, _ = normalize_friends("\n".join([*current_handles, *new_handles]))
                self.friendsEdit.setPlainText("\n".join(f"@{h}" for h in merged))

        if self._friends_debounce.isActive():
            self._friends_debounce.stop()
            self._commit_friends()
        else:
            self.profile.friends = self._update_friends_preview()

    def _on_override_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._sync_override_enabled_states()

        self.profile.message_override = self.ovMsgEdit.text() if self.ovMsgCheck.isChecked() else None

        if not self.ovVideoCheck.isChecked():
            self.profile.video_url_override = None
            self.profile.share_times_override = None
        elif self.ovVideoDisableCheck.isChecked():
            self.profile.video_url_override = ""
            self.profile.share_times_override = 0
        else:
            raw_vid = self.ovVideoEdit.text().strip()
            self.profile.video_url_override = raw_vid if raw_vid else None
            self.profile.share_times_override = int(self.ovSharesSpin.value())

        if not self.ovPhotoCheck.isChecked():
            self.profile.photo_path_override = None
        elif self.ovPhotoDisableCheck.isChecked():
            self.profile.photo_path_override = ""
        else:
            self.profile.photo_path_override = self.ovPhotoEdit.text().strip()

        self.changed.emit(self.profile.id)

    def _browse_override_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("content.photo_select"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if path:
            self.ovPhotoCheck.setChecked(True)
            self.ovPhotoDisableCheck.setChecked(False)
            self.ovPhotoEdit.setText(path)

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("content.import_csv"),
            "",
            "CSV / Text (*.csv *.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            imported = import_friends_csv(path)
        except Exception as exc:
            InfoBar.error(
                tr("common.error"),
                str(exc),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        combined = list(self.profile.friends) + imported
        merged, _ = normalize_friends("\n".join(combined))
        self.profile.friends = merged
        self._last_add_warnings = []
        self.friendsEdit.setPlainText("\n".join(f"@{f}" for f in merged))
        self._update_friends_preview()
        self.changed.emit(self.profile.id)
        InfoBar.success(
            tr("common.success"),
            tr("accounts.friends_count", count=len(merged)),
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _export_csv(self) -> None:
        self.flush_edits()
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("content.export_csv"),
            str(Path.home() / "friends.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            out = export_friends_csv(path, self.profile.friends)
            InfoBar.success(
                tr("common.success"),
                str(out),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        except Exception as exc:
            InfoBar.error(
                tr("common.error"),
                str(exc),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def retranslate(self) -> None:
        self.removeBtn.setToolTip(tr("common.delete"))
        self.nameCaption.setText(tr("accounts.account_name"))
        self.guidedLoginBtn.setText(escape_qt_mnemonic(tr("accounts.sign_in_browser")))
        self.importBrowserBtn.setText(escape_qt_mnemonic(tr("accounts.import_browser")))
        self.pasteCookieBtn.setText(escape_qt_mnemonic(tr("accounts.paste_cookie")))
        verify_key = "accounts.verifying" if self._verifying else "accounts.verify_inbox"
        self.verifyInboxBtn.setText(escape_qt_mnemonic(tr(verify_key)))
        self.friendsLabel.setText(tr("content.friends_label"))
        self.importCsvBtn.setText(escape_qt_mnemonic(tr("content.import_csv")))
        self.exportCsvBtn.setText(escape_qt_mnemonic(tr("content.export_csv")))
        self.friendInputEdit.setPlaceholderText(tr("content.friend_input_placeholder"))
        self.addFriendBtn.setText(escape_qt_mnemonic(tr("content.add_friend_btn")))
        self.friendsChipBox.set_empty_text(tr("content.friends_empty"))
        self.friendsChipBox.set_remove_tooltip(tr("common.remove"))
        self.friendsEdit.setPlaceholderText(tr("content.friends_placeholder"))
        self.overridesToggleBtn.setText(escape_qt_mnemonic(tr("accounts.override_toggle")))
        self.overridesHint.setText(tr("accounts.override_hint"))
        self.ovMsgCheck.setText(escape_qt_mnemonic(tr("accounts.override_message")))
        self.ovVideoCheck.setText(escape_qt_mnemonic(tr("accounts.override_video")))
        self.ovSharesLabel.setText(tr("accounts.override_shares"))
        self.ovVideoDisableCheck.setText(escape_qt_mnemonic(tr("accounts.disable_for_account")))
        self.ovPhotoCheck.setText(escape_qt_mnemonic(tr("accounts.override_photo")))
        self.ovPhotoDisableCheck.setText(escape_qt_mnemonic(tr("accounts.disable_for_account")))
        self.ovPhotoBrowseBtn.setText(escape_qt_mnemonic(tr("common.browse")))
        self._refresh_badges()
        self._update_friends_preview()


__all__ = ["AccountCard"]
