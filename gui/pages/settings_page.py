"""Settings page for appearance, language, Telegram alerts, and workspace sync."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    ExpandGroupSettingCard,
    FluentIcon as FIF,
    HyperlinkButton,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SettingCard,
    SettingCardGroup,
    SmoothScrollArea,
    SwitchButton,
    TitleLabel,
)

from gui.services.github_api import telegram_find_chat_id, telegram_test
from gui.services.i18n import friendly_error, set_language, tr
from gui.services.storage import (
    AccountProfile,
    AppState,
    ConfigStore,
    SecretStore,
    export_local_workspace,
    import_local_workspace,
)
from gui.services.theme import escape_qt_mnemonic, get_windows_personalization
try:
    from gui.services.validators import TIMEZONE_CHOICES, get_timezone_info
except ImportError:  # pragma: no cover
    TIMEZONE_CHOICES = (
        {"id": "Asia/Jakarta", "short": "WIB", "label": "WIB — Jakarta / Western Indonesia (UTC+7)", "offset_minutes": 420},
    )

    def get_timezone_info(tz_id: str | None) -> dict[str, Any]:  # type: ignore[misc]
        return TIMEZONE_CHOICES[0]
from gui.widgets.security_dialog import SecurityPrivacyDialog
from gui.workers import WorkerController


class _ExpandGroupRow(QWidget):
    """Sub-row inside an ExpandGroupSettingCard with title, caption, and right controls."""

    def __init__(self, title: str, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(48, 12, 16, 12)
        self.hBoxLayout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.titleLabel = BodyLabel(title, self)
        self.contentLabel = CaptionLabel(content, self)
        self.contentLabel.setWordWrap(True)
        text_col.addWidget(self.titleLabel)
        text_col.addWidget(self.contentLabel)

        self.hBoxLayout.addLayout(text_col, 1)

    def setTitle(self, text: str) -> None:
        self.titleLabel.setText(text)

    def setContent(self, text: str) -> None:
        self.contentLabel.setText(text)


class SettingsPage(SmoothScrollArea):
    """Application settings: theme, accent, language, Telegram, and workspace I/O."""

    state_changed = Signal()
    theme_changed = Signal(str)
    accent_changed = Signal(str)
    workspace_reloaded = Signal(object)

    def __init__(
        self,
        state: AppState,
        config_store: ConfigStore,
        secret_store: SecretStore,
        worker_controller: WorkerController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settings_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._loading = False
        self._saving_from_self = False

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(250)
        self._save_debounce.timeout.connect(self._on_debounced_save)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(28, 22, 28, 28)
        self.vBoxLayout.setSpacing(18)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        self._build_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        self.titleLabel = TitleLabel(tr("settings.title"), self.view)
        self.vBoxLayout.addWidget(self.titleLabel)

        # 1. Appearance & Language
        self.appearanceGroup = SettingCardGroup(tr("settings.appearance_group"), self.view)

        self.themeCard = SettingCard(
            FIF.BRUSH, tr("settings.theme"), None, self.appearanceGroup
        )
        self.themeCombo = ComboBox(self.themeCard)
        self.themeCombo.setMinimumWidth(180)
        self.themeCombo.addItem(tr("settings.theme_system"), userData="system")
        self.themeCombo.addItem(tr("settings.theme_light"), userData="light")
        self.themeCombo.addItem(tr("settings.theme_dark"), userData="dark")
        self.themeCombo.currentIndexChanged.connect(self._on_theme_changed)
        self.themeCard.hBoxLayout.addWidget(self.themeCombo, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.themeCard.hBoxLayout.addSpacing(16)

        self.accentCard = SettingCard(
            FIF.PALETTE, tr("settings.accent_mode"), None, self.appearanceGroup
        )
        self.accentSwatch = QFrame(self.accentCard)
        self.accentSwatch.setFixedSize(16, 16)
        self.accentCombo = ComboBox(self.accentCard)
        self.accentCombo.setMinimumWidth(220)
        self.accentCombo.addItem(tr("settings.accent_system"), userData="system")
        self.accentCombo.addItem(tr("settings.accent_tiktok"), userData="tiktok")
        self.accentCombo.currentIndexChanged.connect(self._on_accent_changed)
        self.accentCard.hBoxLayout.addWidget(self.accentSwatch, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.accentCard.hBoxLayout.addSpacing(8)
        self.accentCard.hBoxLayout.addWidget(self.accentCombo, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.accentCard.hBoxLayout.addSpacing(16)

        self.langCard = SettingCard(
            FIF.LANGUAGE, tr("settings.language"), None, self.appearanceGroup
        )
        self.langCombo = ComboBox(self.langCard)
        self.langCombo.setMinimumWidth(180)
        self.langCombo.addItem("English", userData="en")
        self.langCombo.addItem("Bahasa Indonesia", userData="id")
        self.langCombo.currentIndexChanged.connect(self._on_language_changed)
        self.langCard.hBoxLayout.addWidget(self.langCombo, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.langCard.hBoxLayout.addSpacing(16)

        self.timezoneCard = SettingCard(
            FIF.GLOBE, tr("settings.timezone"), None, self.appearanceGroup
        )
        self.timezoneCombo = ComboBox(self.timezoneCard)
        self.timezoneCombo.setMinimumWidth(285)
        self.timezoneCombo.setMaximumWidth(340)
        for tz in TIMEZONE_CHOICES:
            self.timezoneCombo.addItem(tz["label"], userData=tz["id"])
        self.timezoneCombo.currentIndexChanged.connect(self._on_timezone_changed)
        self.timezoneCard.hBoxLayout.addWidget(self.timezoneCombo, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.timezoneCard.hBoxLayout.addSpacing(16)

        self.appearanceGroup.addSettingCard(self.themeCard)
        self.appearanceGroup.addSettingCard(self.accentCard)
        self.appearanceGroup.addSettingCard(self.langCard)
        self.appearanceGroup.addSettingCard(self.timezoneCard)
        self.vBoxLayout.addWidget(self.appearanceGroup)

        # 2. Telegram Alerts
        self.telegramGroup = SettingCardGroup(tr("settings.telegram_group"), self.view)

        self.tgEnableCard = SettingCard(
            FIF.RINGER, tr("cloud.telegram_enable"), None, self.telegramGroup
        )
        self.tgEnableSwitch = SwitchButton(self.tgEnableCard)
        self.tgEnableSwitch.checkedChanged.connect(self._on_tg_enabled_changed)
        self.tgEnableCard.hBoxLayout.addWidget(self.tgEnableSwitch, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgEnableCard.hBoxLayout.addSpacing(16)

        self.tgTokenCard = SettingCard(
            FIF.FINGERPRINT, tr("cloud.telegram_token"), None, self.telegramGroup
        )
        self.botFatherLink = HyperlinkButton(
            "https://t.me/BotFather", escape_qt_mnemonic(tr("settings.open_botfather")), self.tgTokenCard
        )
        self.tgTokenEdit = PasswordLineEdit(self.tgTokenCard)
        self.tgTokenEdit.setMinimumWidth(160)
        self.tgTokenEdit.setMaximumWidth(250)
        self.tgTokenEdit.setPlaceholderText("123456789:ABC...")
        self.tgTokenEdit.textChanged.connect(self._on_tg_token_changed)
        self.tgTokenCard.hBoxLayout.addWidget(self.botFatherLink, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgTokenCard.hBoxLayout.addSpacing(8)
        self.tgTokenCard.hBoxLayout.addWidget(self.tgTokenEdit, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgTokenCard.hBoxLayout.addSpacing(16)

        self.tgChatCard = SettingCard(
            FIF.CHAT, tr("cloud.telegram_chat_id"), None, self.telegramGroup
        )
        self.tgChatEdit = LineEdit(self.tgChatCard)
        self.tgChatEdit.setMinimumWidth(110)
        self.tgChatEdit.setMaximumWidth(170)
        self.tgChatEdit.setPlaceholderText("123456789")
        self.tgChatEdit.textChanged.connect(self._on_tg_chat_changed)
        self.tgDetectBtn = PushButton(
            FIF.SEARCH, escape_qt_mnemonic(tr("cloud.telegram_detect")), self.tgChatCard
        )
        self.tgTestBtn = PrimaryPushButton(
            FIF.SEND, escape_qt_mnemonic(tr("cloud.telegram_test")), self.tgChatCard
        )
        self.tgDetectBtn.clicked.connect(self._detect_chat_id)
        self.tgTestBtn.clicked.connect(self._send_telegram_test)
        self.tgChatCard.hBoxLayout.addWidget(self.tgChatEdit, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgChatCard.hBoxLayout.addSpacing(8)
        self.tgChatCard.hBoxLayout.addWidget(self.tgDetectBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgChatCard.hBoxLayout.addSpacing(8)
        self.tgChatCard.hBoxLayout.addWidget(self.tgTestBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.tgChatCard.hBoxLayout.addSpacing(16)

        self.telegramGroup.addSettingCard(self.tgEnableCard)
        self.telegramGroup.addSettingCard(self.tgTokenCard)
        self.telegramGroup.addSettingCard(self.tgChatCard)
        self.vBoxLayout.addWidget(self.telegramGroup)

        # 3. Account, Privacy & Data
        self.workspaceGroup = SettingCardGroup(tr("settings.workspace_group"), self.view)

        self.ghAccountCard = SettingCard(
            FIF.GITHUB, "GitHub", tr("schedule.not_signed_in"), self.workspaceGroup
        )
        self.ghSignoutBtn = PushButton(
            FIF.CLOSE, escape_qt_mnemonic(tr("cloud.signout")), self.ghAccountCard
        )
        self.ghSignoutBtn.clicked.connect(self._sign_out_github)
        self.ghAccountCard.hBoxLayout.addWidget(self.ghSignoutBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.ghAccountCard.hBoxLayout.addSpacing(16)

        self.securityCard = SettingCard(
            FIF.INFO, tr("settings.security_card_title"), tr("settings.security_card_desc"), self.workspaceGroup
        )
        self.openSecurityBtn = PushButton(
            FIF.INFO, escape_qt_mnemonic(tr("security.how_protected_btn")), self.securityCard
        )
        self.openSecurityBtn.clicked.connect(self._open_security_dialog)
        self.securityCard.hBoxLayout.addWidget(self.openSecurityBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.securityCard.hBoxLayout.addSpacing(16)

        self.configFolderCard = SettingCard(
            FIF.DOCUMENT, tr("settings.open_config_dir"), str(self._config_store.config_dir), self.workspaceGroup
        )
        self.openConfigDirBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("settings.open_config_dir")), self.configFolderCard
        )
        self.openConfigDirBtn.clicked.connect(self._open_config_dir)
        self.configFolderCard.hBoxLayout.addWidget(self.openConfigDirBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.configFolderCard.hBoxLayout.addSpacing(16)

        # Collapsible optional Developer / CLI (.env, accounts.json) drawer
        self.cliExportCard = ExpandGroupSettingCard(
            FIF.CODE, tr("settings.cli_export_title"), tr("settings.cli_export_desc"), self.workspaceGroup
        )

        self.workspaceDirCard = _ExpandGroupRow(
            tr("settings.workspace_dir"), tr("settings.workspace_dir_desc"), self.cliExportCard
        )
        self.workspaceDirEdit = LineEdit(self.workspaceDirCard)
        self.workspaceDirEdit.setMinimumWidth(200)
        self.workspaceDirEdit.setMaximumWidth(280)
        self.workspaceDirEdit.textChanged.connect(self._on_workspace_dir_changed)
        self.workspaceBrowseBtn = PushButton(
            FIF.FOLDER_ADD, escape_qt_mnemonic(tr("common.browse")), self.workspaceDirCard
        )
        self.workspaceBrowseBtn.clicked.connect(self._browse_workspace)
        self.workspaceDirCard.hBoxLayout.addWidget(self.workspaceDirEdit, 0, Qt.AlignVCenter)
        self.workspaceDirCard.hBoxLayout.addWidget(self.workspaceBrowseBtn, 0, Qt.AlignVCenter)

        self.workspaceSyncCard = _ExpandGroupRow(
            tr("settings.export_workspace"), tr("settings.export_workspace_desc"), self.cliExportCard
        )
        self.syncWorkspaceBtn = PushButton(
            FIF.SAVE, escape_qt_mnemonic(tr("settings.sync_now")), self.workspaceSyncCard
        )
        self.importWorkspaceBtn = PushButton(
            FIF.DOWNLOAD, escape_qt_mnemonic(tr("settings.import_now")), self.workspaceSyncCard
        )
        self.syncWorkspaceBtn.clicked.connect(self._sync_workspace_now)
        self.importWorkspaceBtn.clicked.connect(self._import_workspace_now)
        self.workspaceSyncCard.hBoxLayout.addWidget(self.syncWorkspaceBtn, 0, Qt.AlignVCenter)
        self.workspaceSyncCard.hBoxLayout.addWidget(self.importWorkspaceBtn, 0, Qt.AlignVCenter)

        self.cliExportCard.addGroupWidget(self.workspaceDirCard)
        self.cliExportCard.addGroupWidget(self.workspaceSyncCard)

        self.resetCard = SettingCard(
            FIF.DELETE, tr("settings.reset_all"), tr("settings.clear_secrets"), self.workspaceGroup
        )
        self.resetAllBtn = PushButton(
            FIF.DELETE, escape_qt_mnemonic(tr("settings.reset_all")), self.resetCard
        )
        self.resetAllBtn.clicked.connect(self._confirm_reset_all)
        self.resetCard.hBoxLayout.addWidget(self.resetAllBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.resetCard.hBoxLayout.addSpacing(16)

        self.workspaceGroup.addSettingCard(self.ghAccountCard)
        self.workspaceGroup.addSettingCard(self.securityCard)
        self.workspaceGroup.addSettingCard(self.configFolderCard)
        self.workspaceGroup.addSettingCard(self.cliExportCard)
        self.workspaceGroup.addSettingCard(self.resetCard)
        self.vBoxLayout.addWidget(self.workspaceGroup)
        self.vBoxLayout.addStretch(1)

    def _default_workspace_dir(self) -> str:
        if self._state.workspace_dir.strip():
            return self._state.workspace_dir.strip()
        return str(self._config_store.config_dir / "workspace")

    def _sync_line_edit(self, edit: LineEdit, value: str, force: bool = False) -> None:
        if not force and (self._saving_from_self or edit.hasFocus()):
            return
        if edit.text() != value:
            edit.setText(value)

    def _update_accent_swatch(self) -> None:
        mode = str(getattr(self._state, "accent_mode", "system") or "system")
        if mode == "tiktok":
            color = "#fe2c55"
        else:
            pers = get_windows_personalization() or {}
            color = str(pers.get("accent_color") or "#0078d4")
        self.accentSwatch.setStyleSheet(
            f"background-color: {color}; border-radius: 5px; border: 1px solid rgba(128, 128, 128, 0.35);"
        )
        self.accentSwatch.setToolTip(color)

    def refresh_from_state(self, state: AppState | None = None) -> None:
        force_inputs = state is not None and state is not self._state
        if state is not None:
            self._state = state
        st = self._state
        self._loading = True
        try:
            theme_val = "system" if st.theme == "auto" else st.theme
            idx_t = self.themeCombo.findData(theme_val)
            self.themeCombo.setCurrentIndex(idx_t if idx_t >= 0 else 0)

            accent_val = str(getattr(st, "accent_mode", "system") or "system")
            idx_a = self.accentCombo.findData(accent_val)
            self.accentCombo.setCurrentIndex(idx_a if idx_a >= 0 else 0)
            self._update_accent_swatch()

            idx_l = self.langCombo.findData(st.language)
            self.langCombo.setCurrentIndex(idx_l if idx_l >= 0 else 0)

            tz_val = get_timezone_info(getattr(st, "timezone", None))["id"]
            idx_tz = self.timezoneCombo.findData(tz_val)
            self.timezoneCombo.setCurrentIndex(idx_tz if idx_tz >= 0 else 0)

            self.tgEnableSwitch.setChecked(bool(st.telegram_enabled))
            self._sync_line_edit(self.tgTokenEdit, self._secrets.get_telegram_token(), force=force_inputs)
            self._sync_line_edit(self.tgChatEdit, st.telegram_chat_id, force=force_inputs)

            has_gh = bool(self._secrets.get_github_token())
            if has_gh and st.github_login:
                self.ghAccountCard.setContent(tr("cloud.signed_in_as", login=st.github_login))
            elif has_gh:
                self.ghAccountCard.setContent(tr("common.ready"))
            else:
                self.ghAccountCard.setContent(tr("schedule.not_signed_in"))
            self.ghSignoutBtn.setEnabled(has_gh)

            self._sync_line_edit(self.workspaceDirEdit, self._default_workspace_dir(), force=force_inputs)
        finally:
            self._loading = False

    def _on_debounced_save(self) -> None:
        self._saving_from_self = True
        try:
            self.state_changed.emit()
        finally:
            self._saving_from_self = False

    def flush_edits(self) -> None:
        if self._save_debounce.isActive():
            self._save_debounce.stop()
            self.state_changed.emit()

    def _on_theme_changed(self, _idx: int) -> None:
        if self._loading:
            return
        theme = str(self.themeCombo.currentData() or "system")
        self._state.theme = theme
        self.theme_changed.emit(theme)
        self.state_changed.emit()

    def _on_accent_changed(self, _idx: int) -> None:
        if self._loading:
            return
        mode = str(self.accentCombo.currentData() or "system")
        self._state.accent_mode = mode
        self._update_accent_swatch()
        self.accent_changed.emit(mode)
        self.state_changed.emit()

    def _on_language_changed(self, _idx: int) -> None:
        if self._loading:
            return
        lang = str(self.langCombo.currentData() or "en")
        self._state.language = lang
        set_language(lang)
        self.state_changed.emit()

    def _on_timezone_changed(self, _idx: int) -> None:
        if self._loading:
            return
        tz_id = str(self.timezoneCombo.currentData() or "Asia/Jakarta")
        self._state.timezone = tz_id
        self.state_changed.emit()

    def _on_tg_enabled_changed(self, checked: bool) -> None:
        if not self._loading:
            self._state.telegram_enabled = bool(checked)
            self.state_changed.emit()

    def _on_tg_token_changed(self, text: str) -> None:
        if self._loading:
            return
        self._secrets.set_telegram_token(text.strip())
        self._save_debounce.start()

    def _on_tg_chat_changed(self, text: str) -> None:
        if self._loading:
            return
        self._state.telegram_chat_id = text.strip()
        self._save_debounce.start()

    def _detect_chat_id(self) -> None:
        tok = self.tgTokenEdit.text().strip()
        if not tok:
            InfoBar.warning(
                tr("common.warning"),
                tr("cloud.telegram_token"),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self.tgDetectBtn.setEnabled(False)
        self._workers.start_task(
            telegram_find_chat_id,
            tok,
            task_key="tg_detect",
            on_finished=self._on_tg_detected,
            on_error=self._on_tg_err,
        )

    def _on_tg_detected(self, res: dict[str, Any]) -> None:
        self.tgDetectBtn.setEnabled(True)
        if res.get("ok") and res.get("chat_id"):
            cid = str(res["chat_id"])
            self.tgChatEdit.setText(cid)
            self._state.telegram_chat_id = cid
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                f"Chat ID: {cid}",
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            msg = friendly_error(res.get("error") or "Failed to detect Chat ID")
            InfoBar.error(
                tr("common.error"),
                msg,
                duration=6000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _send_telegram_test(self) -> None:
        tok = self.tgTokenEdit.text().strip()
        cid = self.tgChatEdit.text().strip()
        if not tok:
            InfoBar.warning(
                tr("common.warning"),
                tr("cloud.telegram_token"),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self.tgTestBtn.setEnabled(False)
        self._workers.start_task(
            telegram_test,
            tok,
            cid,
            task_key="tg_test",
            on_finished=self._on_tg_tested,
            on_error=self._on_tg_err,
        )

    def _on_tg_tested(self, res: dict[str, Any]) -> None:
        self.tgTestBtn.setEnabled(True)
        if res.get("ok"):
            if res.get("chat_id") and not self._state.telegram_chat_id:
                self.tgChatEdit.setText(str(res["chat_id"]))
                self._state.telegram_chat_id = str(res["chat_id"])
            self._state.telegram_enabled = True
            self.tgEnableSwitch.setChecked(True)
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                f"Chat ID: {res.get('chat_id', '')}",
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            InfoBar.error(
                tr("common.error"),
                friendly_error(res.get("error") or "Telegram test failed"),
                duration=6000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_tg_err(self, err: str) -> None:
        self.tgDetectBtn.setEnabled(True)
        self.tgTestBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _sign_out_github(self) -> None:
        self._secrets.delete_github_token()
        self._state.github_login = ""
        self.refresh_from_state()
        self.state_changed.emit()
        InfoBar.info(
            tr("cloud.signout"),
            tr("schedule.not_signed_in"),
            duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_workspace_dir_changed(self, text: str) -> None:
        if not self._loading:
            self._state.workspace_dir = text.strip()
            self._save_debounce.start()

    def _browse_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("settings.workspace_dir"),
            self.workspaceDirEdit.text() or str(Path.home()),
        )
        if folder:
            self.workspaceDirEdit.setText(folder)
            self._state.workspace_dir = folder
            self.state_changed.emit()

    def _sync_workspace_now(self) -> None:
        target = self.workspaceDirEdit.text().strip() or self._default_workspace_dir()
        try:
            res = export_local_workspace(self._state, self._secrets, target)
            self._state.workspace_dir = target
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                f"{len(res.get('files', []))} files -> {target}",
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        except Exception as exc:
            InfoBar.error(
                tr("common.error"),
                friendly_error(exc),
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _import_workspace_now(self) -> None:
        target = self.workspaceDirEdit.text().strip() or self._default_workspace_dir()
        try:
            res = import_local_workspace(target)
        except Exception as exc:
            InfoBar.error(
                tr("common.error"),
                friendly_error(exc),
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        imported_state: AppState = res["state"]
        # Preserve current language, theme, accent, and GitHub settings while updating bot config.
        imported_state.language = self._state.language
        imported_state.theme = self._state.theme
        imported_state.accent_mode = getattr(self._state, "accent_mode", "system")
        imported_state.github_login = self._state.github_login
        imported_state.github_repo_name = self._state.github_repo_name
        imported_state.github_private = self._state.github_private
        imported_state.cloud_cron = self._state.cloud_cron
        imported_state.local_times = list(self._state.local_times)
        if hasattr(imported_state, "timezone") and hasattr(self._state, "timezone"):
            imported_state.timezone = self._state.timezone
        if hasattr(imported_state, "cloud_times") and hasattr(self._state, "cloud_times"):
            imported_state.cloud_times = list(self._state.cloud_times)
        if not imported_state.accounts:
            imported_state.accounts = [AccountProfile(name="Main")]

        sec = res.get("secrets") or {}
        if sec.get("telegram_token"):
            self._secrets.set_telegram_token(sec["telegram_token"])
        for aid, cookie in (sec.get("accounts") or {}).items():
            self._secrets.set_account_cookie(aid, cookie)

        self.workspace_reloaded.emit(imported_state)
        InfoBar.success(
            tr("common.success"),
            f"{len(res.get('files', []))} files imported",
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _open_config_dir(self) -> None:
        self._config_store.config_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_store.config_dir)))

    def _open_security_dialog(self) -> None:
        dlg = SecurityPrivacyDialog(self.window(), config_dir=self._config_store.config_dir)
        dlg.exec()

    def _confirm_reset_all(self) -> None:
        box = MessageBox(
            tr("settings.reset_all"),
            tr("settings.reset_all_confirm"),
            self.window(),
        )
        box.yesButton.setText(tr("common.delete"))
        box.cancelButton.setText(tr("common.cancel"))
        if not box.exec():
            return

        account_ids = [a.id for a in self._state.accounts]
        self._secrets.clear_all(account_ids=account_ids)
        self._config_store.reset()
        fresh = AppState(
            language=self._state.language,
            theme=self._state.theme,
            accounts=[AccountProfile(name="Main")],
        )
        fresh.accent_mode = getattr(self._state, "accent_mode", "system")
        self.workspace_reloaded.emit(fresh)
        InfoBar.success(
            tr("common.success"),
            tr("settings.reset_all"),
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("settings.title"))
        self.appearanceGroup.titleLabel.setText(tr("settings.appearance_group"))
        self.themeCard.setTitle(tr("settings.theme"))
        self.themeCombo.setItemText(0, tr("settings.theme_system"))
        self.themeCombo.setItemText(1, tr("settings.theme_light"))
        self.themeCombo.setItemText(2, tr("settings.theme_dark"))
        self.accentCard.setTitle(tr("settings.accent_mode"))
        self.accentCombo.setItemText(0, tr("settings.accent_system"))
        self.accentCombo.setItemText(1, tr("settings.accent_tiktok"))
        self.langCard.setTitle(tr("settings.language"))
        self.timezoneCard.setTitle(tr("settings.timezone"))

        self.telegramGroup.titleLabel.setText(tr("settings.telegram_group"))
        self.tgEnableCard.setTitle(tr("cloud.telegram_enable"))
        self.tgTokenCard.setTitle(tr("cloud.telegram_token"))
        self.botFatherLink.setText(escape_qt_mnemonic(tr("settings.open_botfather")))
        self.tgChatCard.setTitle(tr("cloud.telegram_chat_id"))
        self.tgDetectBtn.setText(escape_qt_mnemonic(tr("cloud.telegram_detect")))
        self.tgTestBtn.setText(escape_qt_mnemonic(tr("cloud.telegram_test")))

        self.workspaceGroup.titleLabel.setText(tr("settings.workspace_group"))
        self.ghSignoutBtn.setText(escape_qt_mnemonic(tr("cloud.signout")))
        self.cliExportCard.setTitle(tr("settings.cli_export_title"))
        self.cliExportCard.setContent(tr("settings.cli_export_desc"))
        self.workspaceDirCard.setTitle(tr("settings.workspace_dir"))
        self.workspaceDirCard.setContent(tr("settings.workspace_dir_desc"))
        self.workspaceBrowseBtn.setText(escape_qt_mnemonic(tr("common.browse")))
        self.workspaceSyncCard.setTitle(tr("settings.export_workspace"))
        self.workspaceSyncCard.setContent(tr("settings.export_workspace_desc"))
        self.syncWorkspaceBtn.setText(escape_qt_mnemonic(tr("settings.sync_now")))
        self.importWorkspaceBtn.setText(escape_qt_mnemonic(tr("settings.import_now")))
        self.configFolderCard.setTitle(tr("settings.open_config_dir"))
        self.openConfigDirBtn.setText(escape_qt_mnemonic(tr("settings.open_config_dir")))
        self.securityCard.setTitle(tr("settings.security_card_title"))
        self.securityCard.setContent(tr("settings.security_card_desc"))
        self.openSecurityBtn.setText(escape_qt_mnemonic(tr("security.how_protected_btn")))
        self.resetCard.setTitle(tr("settings.reset_all"))
        self.resetCard.setContent(tr("settings.clear_secrets"))
        self.resetAllBtn.setText(escape_qt_mnemonic(tr("settings.reset_all")))
        if hasattr(self.cliExportCard, "_adjustViewSize"):
            self.cliExportCard._adjustViewSize()
        self.refresh_from_state()


__all__ = ["SettingsPage"]
