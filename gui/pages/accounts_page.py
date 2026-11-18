"""Accounts page for TikTok authentication, browser import, and friend lists."""
from __future__ import annotations

from datetime import datetime
import inspect
import threading
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    TitleLabel,
)

from gui.services.browsers import list_sources, read_source
from gui.services.i18n import friendly_error, tr
from gui.services.storage import AccountProfile, ConfigStore, SecretStore, AppState
from gui.services.theme import PAGE_MARGINS, escape_qt_mnemonic
from gui.services.tiktok_auth import (
    check_session_api,
    guided_browser_login,
    verify_inbox_headless,
)
from gui.widgets.account_card import AccountCard
from gui.widgets.cookie_dialog import CookieDialog
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _import_and_check_browser(source: dict[str, Any]) -> dict[str, Any]:
    extracted = read_source(source)
    cookie_text = extracted.get("cookie_text") or extracted.get("sessionid") or ""
    if not cookie_text:
        return {
            "ok": False,
            "empty": True,
            "error": "No TikTok login cookies found in the selected browser profile.",
        }
    api_res = check_session_api(cookie_text)
    return {
        "ok": True,
        "cookie_text": cookie_text,
        "sessionid": extracted.get("sessionid", ""),
        "count": len(extracted.get("cookies") or {}),
        "api": api_res,
        "source_label": source.get("browser_label") or source.get("label") or "browser",
    }


def _check_pasted_cookie(cookie_text: str) -> dict[str, Any]:
    return check_session_api(cookie_text)


class AccountsPage(SmoothScrollArea):
    """Manage single/multi TikTok accounts, cookies, inbox verification, and friends."""

    state_changed = Signal()
    log_emitted = Signal(str)

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
        self.setObjectName("accounts_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._sources: list[dict[str, Any]] = []
        self._scan_completed = False
        self._cards: list[AccountCard] = []
        self._login_cancel: threading.Event | None = None
        self._login_grab_now = threading.Event()

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGINS)
        self.vBoxLayout.setSpacing(16)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        self._build_ui()
        self.refresh_from_state()
        if not smoke_test:
            self.scan_browsers()

    def _build_ui(self) -> None:
        # Page header + mode toggle
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.titleLabel = TitleLabel(tr("accounts.title"), self.view)
        self.hintLabel = CaptionLabel(tr("accounts.guided_login_hint"), self.view)
        self.hintLabel.setWordWrap(True)
        title_col.addWidget(self.titleLabel)
        title_col.addWidget(self.hintLabel)

        self.modeSegment = SegmentedWidget(self.view)
        self.modeSegment.addItem("single", tr("accounts.single_mode"))
        self.modeSegment.addItem("multi", tr("accounts.multi_mode"))
        self.modeSegment.currentItemChanged.connect(self._on_mode_changed)

        header_row.addLayout(title_col, 1)
        header_row.addWidget(self.modeSegment, 0, Qt.AlignTop)
        self.vBoxLayout.addLayout(header_row)

        # Browser selector card
        self.browserCard = CardWidget(self.view)
        b_lay = QVBoxLayout(self.browserCard)
        b_lay.setContentsMargins(16, 14, 16, 14)
        b_lay.setSpacing(8)

        b_top = QHBoxLayout()
        b_top.setSpacing(10)
        self.browserTitle = StrongBodyLabel(tr("accounts.browser_profile"), self.browserCard)
        self.browserCombo = ComboBox(self.browserCard)
        self.browserCombo.setMinimumWidth(0)
        self.browserCombo.currentIndexChanged.connect(self._update_browser_badge)

        self.refreshBrowsersBtn = PushButton(FIF.SYNC, escape_qt_mnemonic(tr("common.refresh")), self.browserCard)
        self.refreshBrowsersBtn.clicked.connect(self.scan_browsers)

        b_top.addWidget(self.browserTitle)
        b_top.addWidget(self.browserCombo, 1)
        b_top.addWidget(self.refreshBrowsersBtn)
        b_lay.addLayout(b_top)

        b_status = QHBoxLayout()
        b_status.setSpacing(8)
        self.browserBadge = StatusPill(tr("accounts.scan_not_run"), "neutral", self.browserCard)
        self.browserNote = CaptionLabel(tr("accounts.guided_login_hint"), self.browserCard)
        self.browserNote.setWordWrap(True)
        b_status.addWidget(self.browserBadge, 0, Qt.AlignLeft)
        b_status.addWidget(self.browserNote, 1, Qt.AlignVCenter)
        b_lay.addLayout(b_status)
        self.vBoxLayout.addWidget(self.browserCard)

        # Active guided login progress banner (hidden when idle)
        self.activeTaskCard = CardWidget(self.view)
        t_lay = QHBoxLayout(self.activeTaskCard)
        t_lay.setContentsMargins(16, 12, 16, 12)
        t_lay.setSpacing(10)
        self.taskSpinner = IndeterminateProgressRing(self.activeTaskCard)
        self.taskSpinner.setFixedSize(20, 20)
        self.taskLabel = BodyLabel("", self.activeTaskCard)
        self.taskLabel.setWordWrap(True)
        self.taskGrabBtn = PrimaryPushButton(
            FIF.ACCEPT, escape_qt_mnemonic(tr("accounts.grab_session_now")), self.activeTaskCard
        )
        self.taskGrabBtn.clicked.connect(self._grab_guided_login_now)
        self.taskCancelBtn = PushButton(FIF.CANCEL, escape_qt_mnemonic(tr("common.cancel")), self.activeTaskCard)
        self.taskCancelBtn.clicked.connect(self._cancel_guided_login)
        t_lay.addWidget(self.taskSpinner)
        t_lay.addWidget(self.taskLabel, 1)
        t_lay.addWidget(self.taskGrabBtn)
        t_lay.addWidget(self.taskCancelBtn)
        self.activeTaskCard.hide()
        self.vBoxLayout.addWidget(self.activeTaskCard)

        # Toolbar for multi-account / hidden-account reminder
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.multiHiddenHint = CaptionLabel("", self.view)
        self.multiHiddenHint.setWordWrap(True)
        self._toolbarSpacer = QWidget(self.view)
        self._toolbarSpacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addAccountBtn = PushButton(FIF.ADD, escape_qt_mnemonic(tr("accounts.add_account")), self.view)
        self.addAccountBtn.clicked.connect(self._add_account)
        toolbar.addWidget(self.multiHiddenHint, 1)
        toolbar.addWidget(self._toolbarSpacer, 1)
        toolbar.addWidget(self.addAccountBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.vBoxLayout.addLayout(toolbar)

        # Container for AccountCard items
        self.cardsHost = QWidget(self.view)
        self.cardsLayout = QVBoxLayout(self.cardsHost)
        self.cardsLayout.setContentsMargins(0, 0, 0, 0)
        self.cardsLayout.setSpacing(14)
        self.vBoxLayout.addWidget(self.cardsHost)
        self.vBoxLayout.addStretch(1)

    def _ensure_at_least_one_account(self) -> None:
        if not self._state.accounts:
            self._state.accounts.append(AccountProfile(name="Main"))

    def _find_account(self, account_id: str) -> AccountProfile | None:
        for acc in self._state.accounts:
            if acc.id == account_id:
                return acc
        return None

    def _find_card(self, account_id: str) -> AccountCard | None:
        for card in self._cards:
            if card.profile.id == account_id:
                return card
        return None

    def refresh_from_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        self._ensure_at_least_one_account()

        target_key = "multi" if self._state.multi_account else "single"
        if self.modeSegment.currentRouteKey() != target_key:
            self.modeSegment.blockSignals(True)
            self.modeSegment.setCurrentItem(target_key)
            self.modeSegment.blockSignals(False)

        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        self._ensure_at_least_one_account()
        for card in self._cards:
            card.flush_edits()
            self.cardsLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        is_multi = bool(self._state.multi_account)
        visible_accounts = list(self._state.accounts) if is_multi else [self._state.accounts[0]]

        self.addAccountBtn.setVisible(is_multi)
        self._toolbarSpacer.setVisible(is_multi)
        self._update_multi_hidden_hint()

        can_remove = is_multi and len(self._state.accounts) > 1
        for acc in visible_accounts:
            has_cookie = bool(self._secrets.get_account_cookie(acc.id))
            card = AccountCard(
                profile=acc,
                has_cookie=has_cookie,
                multi_account=is_multi,
                can_remove=can_remove,
                parent=self.cardsHost,
            )
            card.changed.connect(self._on_card_changed)
            card.request_guided_login.connect(self._start_guided_login)
            card.request_import_browser.connect(self._start_import_browser)
            card.request_paste_cookie.connect(self._open_paste_dialog)
            card.request_verify_inbox.connect(self._start_verify_inbox)
            card.request_remove.connect(self._remove_account)
            self.cardsLayout.addWidget(card)
            self._cards.append(card)

    def flush_edits(self) -> None:
        for card in self._cards:
            card.flush_edits()

    def _on_mode_changed(self, route_key: str) -> None:
        is_multi = route_key == "multi"
        if self._state.multi_account == is_multi:
            return
        self.flush_edits()
        self._state.multi_account = is_multi
        self._rebuild_cards()
        self.state_changed.emit()

    def _on_card_changed(self, _account_id: str) -> None:
        self.state_changed.emit()

    def _add_account(self) -> None:
        self.flush_edits()
        idx = len(self._state.accounts) + 1
        new_acc = AccountProfile(name=f"Account {idx}")
        self._state.accounts.append(new_acc)
        self._rebuild_cards()
        self.state_changed.emit()

    def _remove_account(self, account_id: str) -> None:
        if len(self._state.accounts) <= 1:
            return
        self.flush_edits()
        self._state.accounts = [a for a in self._state.accounts if a.id != account_id]
        self._secrets.delete_account_cookie(account_id)
        self._rebuild_cards()
        self.state_changed.emit()

    # -- Browser scanning ------------------------------------------------
    def scan_browsers(self) -> None:
        if self._workers.is_running("scan_browsers"):
            return
        self.refreshBrowsersBtn.setEnabled(False)
        self.browserBadge.setState("info", tr("common.loading"))
        self.browserBadge.adjustSize()
        self._workers.start_task(
            list_sources,
            enrich=True,
            task_key="scan_browsers",
            on_finished=self._on_browsers_scanned,
            on_error=self._on_browser_scan_error,
        )

    def _on_browsers_scanned(self, sources: list[dict[str, Any]]) -> None:
        self.refreshBrowsersBtn.setEnabled(True)
        self._scan_completed = True
        self._sources = list(sources or [])
        prev_id = self.browserCombo.currentData()
        self.browserCombo.blockSignals(True)
        self.browserCombo.clear()
        for src in self._sources:
            suffix = ""
            if src.get("v20_blocked"):
                suffix = " [v20]"
            elif src.get("locked"):
                suffix = " [Locked]"
            elif src.get("has_tiktok"):
                suffix = " [TikTok]"
            self.browserCombo.addItem(f"{src['label']}{suffix}", userData=src)
        if prev_id and isinstance(prev_id, dict):
            for i in range(self.browserCombo.count()):
                d = self.browserCombo.itemData(i)
                if isinstance(d, dict) and d.get("id") == prev_id.get("id"):
                    self.browserCombo.setCurrentIndex(i)
                    break
        self.browserCombo.blockSignals(False)
        self._update_browser_badge()

    def _on_browser_scan_error(self, err_msg: str) -> None:
        self.refreshBrowsersBtn.setEnabled(True)
        self._scan_completed = True
        self.browserBadge.setState(InfoLevel.WARNING, tr("common.warning"))
        self.browserBadge.adjustSize()
        self.browserNote.setText(err_msg)

    def _update_browser_badge(self, *_args: Any) -> None:
        src = self.browserCombo.currentData()
        if not isinstance(src, dict):
            badge_key = "accounts.no_tiktok_badge" if self._scan_completed else "accounts.scan_not_run"
            self.browserBadge.setState("neutral", tr(badge_key))
            self.browserBadge.adjustSize()
            self.browserNote.setText(tr("accounts.guided_login_hint"))
            return

        if src.get("v20_blocked"):
            self.browserBadge.setState(InfoLevel.WARNING, tr("accounts.v20_badge"))
            self.browserNote.setText(tr("err.v20"))
        elif src.get("locked"):
            self.browserBadge.setState(InfoLevel.ERROR, tr("accounts.browser_locked_badge"))
            self.browserNote.setText(tr("err.browser_locked"))
        elif src.get("has_tiktok", 0) > 0:
            self.browserBadge.setState(InfoLevel.SUCCESS, tr("accounts.has_tiktok_badge"))
            self.browserNote.setText(src.get("cookies_path", ""))
        else:
            self.browserBadge.setState("neutral", tr("accounts.no_tiktok_badge"))
            self.browserNote.setText(src.get("cookies_path", ""))
        self.browserBadge.adjustSize()

    # -- Auth flows ------------------------------------------------------
    def _start_guided_login(self, account_id: str) -> None:
        if self._workers.is_running("guided_login"):
            InfoBar.info(
                tr("common.warning"),
                tr("common.loading"),
                duration=2500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        acc = self._find_account(account_id)
        if acc is None:
            return

        profile_dir = self._config_store.config_dir / "browser_profiles" / acc.id
        self._login_cancel = threading.Event()
        self._login_grab_now = threading.Event()
        self.taskGrabBtn.setEnabled(True)
        self.taskLabel.setText(f"[{acc.name}] {tr('accounts.guided_login')}...")
        self.taskSpinner.start()
        self.activeTaskCard.show()

        task_kwargs: dict[str, Any] = {
            "browser_kind": "auto",
            "task_key": "guided_login",
            "cancel_event": self._login_cancel,
            "pass_cancel_event": True,
            "pass_status_cb": True,
            "on_log": self._on_guided_status,
            "on_finished": lambda res, aid=account_id: self._on_guided_finished(aid, res),
            "on_error": self._on_guided_error,
        }
        try:
            sig = inspect.signature(guided_browser_login)
            if "manual_grab_event" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                task_kwargs["manual_grab_event"] = self._login_grab_now
        except (TypeError, ValueError):
            pass

        self._workers.start_task(
            guided_browser_login,
            profile_dir,
            **task_kwargs,
        )

    def _grab_guided_login_now(self) -> None:
        self._login_grab_now.set()
        self.taskGrabBtn.setEnabled(False)

    def _cancel_guided_login(self) -> None:
        if self._login_cancel is not None:
            self._login_cancel.set()

    def _on_guided_status(self, msg: str) -> None:
        self.taskLabel.setText(msg)
        self.log_emitted.emit(msg)

    def _on_guided_finished(self, account_id: str, res: dict[str, Any]) -> None:
        self.taskSpinner.stop()
        self.activeTaskCard.hide()
        acc = self._find_account(account_id)
        card = self._find_card(account_id)
        if acc is None:
            return

        if res.get("ok"):
            cookie_text = str(res.get("cookie_text") or res.get("sessionid") or "").strip()
            self._secrets.set_account_cookie(acc.id, cookie_text)
            acc.username = str(res.get("username") or acc.username or "").strip()
            acc.inbox_verified = bool(res.get("inbox_verified", True))
            acc.auth_method = f"guided ({res.get('browser', 'browser')})"
            acc.last_checked = _now_label()
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                tr("accounts.verified", username=acc.username or acc.name),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if res.get("business_suite"):
            acc.inbox_verified = False
            acc.auth_method = "business_suite"
            acc.last_checked = _now_label()
            if card:
                card.load_profile(acc)
            self.state_changed.emit()
            InfoBar.error(
                tr("accounts.inbox_blocked"),
                tr("err.inbox_unavailable"),
                duration=7000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if res.get("cancelled"):
            InfoBar.info(
                tr("common.cancel"),
                str(res.get("error") or tr("common.cancel")),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        InfoBar.error(
            tr("common.error"),
            friendly_error(res.get("error")),
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_guided_error(self, err_msg: str) -> None:
        self.taskSpinner.stop()
        self.activeTaskCard.hide()
        InfoBar.error(
            tr("common.error"),
            err_msg,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _start_import_browser(self, account_id: str) -> None:
        src = self.browserCombo.currentData()
        if not isinstance(src, dict):
            InfoBar.warning(
                tr("common.warning"),
                tr("accounts.no_tiktok_badge"),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        if src.get("v20_blocked"):
            InfoBar.warning(
                tr("common.warning"),
                tr("err.v20"),
                duration=6000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        self._workers.start_task(
            _import_and_check_browser,
            src,
            task_key=f"import_browser_{account_id}",
            on_finished=lambda res, aid=account_id: self._on_import_finished(aid, res),
            on_error=lambda err: InfoBar.error(
                tr("common.error"),
                err,
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            ),
        )

    def _on_import_finished(self, account_id: str, res: dict[str, Any]) -> None:
        acc = self._find_account(account_id)
        card = self._find_card(account_id)
        if acc is None:
            return
        if not res.get("ok"):
            InfoBar.warning(
                tr("common.warning"),
                friendly_error(res.get("error")),
                duration=4500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        cookie_text = str(res["cookie_text"])
        api = res.get("api") or {}
        if api.get("ok"):
            self._secrets.set_account_cookie(acc.id, cookie_text)
            acc.username = str(api.get("username") or "").strip()
            acc.inbox_verified = False
            acc.auth_method = str(res.get("source_label") or "browser")
            acc.last_checked = _now_label()
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                tr("accounts.verified", username=acc.username or acc.name),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        elif api.get("waf_blocked"):
            # Keep extracted cookies and advise verifying inbox directly.
            self._secrets.set_account_cookie(acc.id, cookie_text)
            acc.inbox_verified = False
            acc.auth_method = str(res.get("source_label") or "browser")
            acc.last_checked = _now_label()
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.warning(
                tr("common.warning"),
                tr("err.waf"),
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            InfoBar.error(
                tr("common.error"),
                friendly_error(api.get("error") or "Session expired"),
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _open_paste_dialog(self, account_id: str) -> None:
        acc = self._find_account(account_id)
        if acc is None:
            return
        dlg = CookieDialog(self.window())
        if not dlg.exec():
            return

        cookie_payload = dlg.cookie_payload
        self._secrets.set_account_cookie(acc.id, cookie_payload)
        acc.auth_method = "manual"
        acc.inbox_verified = False
        acc.last_checked = _now_label()
        card = self._find_card(account_id)
        if card:
            card.load_profile(acc, has_cookie=True)
        self.state_changed.emit()

        # Verify session username asynchronously
        self._workers.start_task(
            _check_pasted_cookie,
            cookie_payload,
            task_key=f"check_cookie_{account_id}",
            on_finished=lambda api, aid=account_id: self._on_paste_checked(aid, api),
            on_error=lambda err: InfoBar.warning(
                tr("common.warning"),
                err,
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            ),
        )

    def _on_paste_checked(self, account_id: str, api: dict[str, Any]) -> None:
        acc = self._find_account(account_id)
        card = self._find_card(account_id)
        if acc is None:
            return
        if api.get("ok"):
            acc.username = str(api.get("username") or "").strip()
            acc.last_checked = _now_label()
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                tr("accounts.verified", username=acc.username),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        elif api.get("waf_blocked"):
            InfoBar.warning(
                tr("common.warning"),
                tr("err.waf"),
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            acc.auth_method = "expired"
            acc.inbox_verified = False
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.error(
                tr("accounts.inbox_expired"),
                friendly_error(api.get("error") or "Session expired"),
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _start_verify_inbox(self, account_id: str) -> None:
        acc = self._find_account(account_id)
        if acc is None:
            return
        cookie_payload = self._secrets.get_account_cookie(acc.id)
        if not cookie_payload:
            InfoBar.warning(
                tr("common.warning"),
                tr("accounts.no_session"),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        card = self._find_card(account_id)
        if card:
            card.verifyInboxBtn.setEnabled(False)
        self._workers.start_task(
            verify_inbox_headless,
            cookie_payload,
            task_key=f"verify_inbox_{account_id}",
            on_finished=lambda res, aid=account_id: self._on_verify_finished(aid, res),
            on_error=lambda err, aid=account_id: self._on_verify_error(aid, err),
        )

    def _on_verify_finished(self, account_id: str, res: dict[str, Any]) -> None:
        acc = self._find_account(account_id)
        card = self._find_card(account_id)
        if card:
            card.verifyInboxBtn.setEnabled(True)
        if acc is None:
            return

        if res.get("username"):
            acc.username = str(res["username"]).strip()
        acc.last_checked = _now_label()

        if res.get("ok") and res.get("inbox_verified"):
            acc.inbox_verified = True
            if (acc.auth_method or "").lower() in ("expired", "business_suite", ""):
                acc.auth_method = "verified"
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.success(
                tr("accounts.inbox_verified"),
                tr("accounts.inbox_ok"),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        elif res.get("business_suite"):
            acc.inbox_verified = False
            acc.auth_method = "business_suite"
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.error(
                tr("accounts.inbox_blocked"),
                tr("err.inbox_unavailable"),
                duration=6500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        elif res.get("session_expired"):
            acc.inbox_verified = False
            acc.auth_method = "expired"
            if card:
                card.load_profile(acc, has_cookie=True)
            self.state_changed.emit()
            InfoBar.error(
                tr("accounts.inbox_expired"),
                tr("err.session_expired"),
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            InfoBar.error(
                tr("common.error"),
                friendly_error(res.get("error")),
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_verify_error(self, account_id: str, err: str) -> None:
        card = self._find_card(account_id)
        if card:
            card.verifyInboxBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _update_multi_hidden_hint(self) -> None:
        is_multi = bool(self._state.multi_account)
        extra_count = max(0, len(self._state.accounts) - 1)
        if not is_multi and extra_count > 0:
            self.multiHiddenHint.setText(tr("accounts.multi_hidden_hint", extra=extra_count))
            self.multiHiddenHint.show()
        else:
            self.multiHiddenHint.hide()

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("accounts.title"))
        self.hintLabel.setText(tr("accounts.guided_login_hint"))
        self.modeSegment.items["single"].setText(tr("accounts.single_mode"))
        self.modeSegment.items["multi"].setText(tr("accounts.multi_mode"))
        self.browserTitle.setText(tr("accounts.browser_profile"))
        self.refreshBrowsersBtn.setText(escape_qt_mnemonic(tr("common.refresh")))
        self.addAccountBtn.setText(escape_qt_mnemonic(tr("accounts.add_account")))
        self.taskGrabBtn.setText(escape_qt_mnemonic(tr("accounts.grab_session_now")))
        self.taskCancelBtn.setText(escape_qt_mnemonic(tr("common.cancel")))
        self._update_multi_hidden_hint()
        self._update_browser_badge()
        for card in self._cards:
            card.retranslate()


__all__ = ["AccountsPage"]
