"""Home dashboard page with readiness summary cards and 4-step checklist."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoLevel,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from gui.services.i18n import tr
from gui.services.storage import AppState, SecretStore
from gui.services.theme import PAGE_MARGINS, escape_qt_mnemonic
from gui.services.validators import validate_repo_name, validate_video_url
from gui.widgets.security_dialog import SecurityPrivacyDialog
from gui.widgets.status_pill import StatusPill


class _ChecklistRow(CardWidget):
    """Single step card in the 4-step readiness checklist."""

    clicked = Signal()

    def __init__(self, step_num: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_num = step_num
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.titleLabel = StrongBodyLabel("", self)
        self.badge = StatusPill("", "neutral", self)
        title_row.addWidget(self.titleLabel)
        title_row.addWidget(self.badge)
        title_row.addStretch(1)

        self.descLabel = BodyLabel("", self)
        self.descLabel.setWordWrap(True)
        self.detailLabel = CaptionLabel("", self)
        self.detailLabel.setWordWrap(True)

        text_col.addLayout(title_row)
        text_col.addWidget(self.descLabel)
        text_col.addWidget(self.detailLabel)

        btn_txt = escape_qt_mnemonic(tr("home.go_step"))
        self._primaryGoBtn = PrimaryPushButton(FIF.RIGHT_ARROW, btn_txt, self)
        self._secondaryGoBtn = PushButton(FIF.RIGHT_ARROW, btn_txt, self)
        self._primaryGoBtn.clicked.connect(self.clicked.emit)
        self._secondaryGoBtn.clicked.connect(self.clicked.emit)
        self.goBtn: PushButton = self._secondaryGoBtn

        layout.addLayout(text_col, 1)
        layout.addWidget(self._primaryGoBtn, 0, Qt.AlignVCenter)
        layout.addWidget(self._secondaryGoBtn, 0, Qt.AlignVCenter)
        self.set_primary(False)

    def set_primary(self, is_primary: bool) -> None:
        primary = bool(is_primary)
        self._primaryGoBtn.setVisible(primary)
        self._secondaryGoBtn.setVisible(not primary)
        self.goBtn = self._primaryGoBtn if primary else self._secondaryGoBtn

    def set_button_text(self, text: str) -> None:
        escaped = escape_qt_mnemonic(text)
        self._primaryGoBtn.setText(escaped)
        self._secondaryGoBtn.setText(escaped)


class HomePage(SmoothScrollArea):
    """Overview page showing setup status, summary metrics, and guided checklist."""

    navigate_requested = Signal(str)

    def __init__(
        self,
        state: AppState,
        secret_store: SecretStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("home_interface")
        self._state = state
        self._secrets = secret_store

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGINS)
        self.vBoxLayout.setSpacing(16)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        # Welcome header
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.welcomeTitle = TitleLabel(tr("dashboard.welcome"), self.view)
        self.welcomeSubtitle = BodyLabel(tr("app.subtitle"), self.view)
        self.overallDesc = CaptionLabel("", self.view)
        self.overallDesc.setWordWrap(True)
        title_col.addWidget(self.welcomeTitle)
        title_col.addWidget(self.welcomeSubtitle)
        title_col.addWidget(self.overallDesc)

        self.overallBadge = StatusPill("", "neutral", self.view)
        header_row.addLayout(title_col, 1)
        header_row.addWidget(self.overallBadge, 0, Qt.AlignTop)
        self.vBoxLayout.addLayout(header_row)

        # 4 Summary cards
        stats_grid = QGridLayout()
        stats_grid.setSpacing(12)
        self.statCards: list[tuple[CardWidget, CaptionLabel, StrongBodyLabel]] = []
        for idx in range(4):
            card = SimpleCardWidget(self.view)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(16, 12, 16, 12)
            c_lay.setSpacing(4)
            cap = CaptionLabel("", card)
            val = StrongBodyLabel("", card)
            val.setWordWrap(True)
            c_lay.addWidget(cap)
            c_lay.addWidget(val)
            stats_grid.addWidget(card, 0, idx)
            self.statCards.append((card, cap, val))
        self.vBoxLayout.addLayout(stats_grid)

        # Checklist header
        self.checklistHeading = SubtitleLabel(tr("home.checklist_heading"), self.view)
        self.vBoxLayout.addWidget(self.checklistHeading)

        self.step1 = _ChecklistRow(1, self.view)
        self.step2 = _ChecklistRow(2, self.view)
        self.step3 = _ChecklistRow(3, self.view)
        self.step4 = _ChecklistRow(4, self.view)

        self.step1.clicked.connect(lambda: self.navigate_requested.emit("accounts"))
        self.step2.clicked.connect(self._go_step2)
        self.step3.clicked.connect(lambda: self.navigate_requested.emit("content"))
        self.step4.clicked.connect(lambda: self.navigate_requested.emit("run"))

        for row in (self.step1, self.step2, self.step3, self.step4):
            self.vBoxLayout.addWidget(row)

        # Security & Privacy transparency banner card
        self.privacyCard = SimpleCardWidget(self.view)
        priv_lay = QHBoxLayout(self.privacyCard)
        priv_lay.setContentsMargins(18, 12, 18, 12)
        priv_lay.setSpacing(14)
        priv_text_col = QVBoxLayout()
        priv_text_col.setSpacing(2)
        self.privacyTitle = StrongBodyLabel(tr("security.banner_title"), self.privacyCard)
        self.privacyDesc = CaptionLabel(tr("security.banner_desc"), self.privacyCard)
        self.privacyDesc.setWordWrap(True)
        priv_text_col.addWidget(self.privacyTitle)
        priv_text_col.addWidget(self.privacyDesc)
        self.privacyBtn = PushButton(
            FIF.INFO, escape_qt_mnemonic(tr("security.how_protected_btn")), self.privacyCard
        )
        self.privacyBtn.clicked.connect(self._open_security_dialog)
        priv_lay.addLayout(priv_text_col, 1)
        priv_lay.addWidget(self.privacyBtn, 0, Qt.AlignVCenter)
        self.vBoxLayout.addWidget(self.privacyCard)

        # Quick shortcuts
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        smoke_txt = escape_qt_mnemonic(tr("dashboard.smoke_test"))
        self._quickTestPrimary = PrimaryPushButton(FIF.PLAY, smoke_txt, self.view)
        self._quickTestSecondary = PushButton(FIF.PLAY, smoke_txt, self.view)
        self._quickTestPrimary.clicked.connect(lambda: self.navigate_requested.emit("run"))
        self._quickTestSecondary.clicked.connect(lambda: self.navigate_requested.emit("run"))
        self.quickTestBtn: PushButton = self._quickTestSecondary
        self.quickSettingsBtn = PushButton(FIF.SETTING, escape_qt_mnemonic(tr("nav.settings")), self.view)
        self.quickSettingsBtn.clicked.connect(lambda: self.navigate_requested.emit("settings"))
        quick_row.addWidget(self._quickTestPrimary)
        quick_row.addWidget(self._quickTestSecondary)
        quick_row.addWidget(self.quickSettingsBtn)
        quick_row.addStretch(1)
        self.vBoxLayout.addLayout(quick_row)
        self.vBoxLayout.addStretch(1)
        self._set_quick_test_primary(False)

        self.refresh_state()

    def _set_quick_test_primary(self, is_primary: bool) -> None:
        primary = bool(is_primary)
        self._quickTestPrimary.setVisible(primary)
        self._quickTestSecondary.setVisible(not primary)
        self.quickTestBtn = self._quickTestPrimary if primary else self._quickTestSecondary

    def _active_accounts(self):
        if not self._state.accounts:
            return []
        return list(self._state.accounts) if self._state.multi_account else [self._state.accounts[0]]

    def _go_step2(self) -> None:
        total_friends = sum(len(a.friends) for a in self._active_accounts())
        self.navigate_requested.emit("accounts" if total_friends == 0 else "content")

    def _open_security_dialog(self) -> None:
        dlg = SecurityPrivacyDialog(self.window(), config_dir=self._secrets.config_dir)
        dlg.exec()

    def refresh_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        active = self._active_accounts()

        # Summary cards
        accounts_with_cookie = [a for a in active if self._secrets.get_account_cookie(a.id)]
        blocked_accounts = [
            a for a in active if (a.auth_method or "").lower() in ("business_suite", "guided_blocked")
        ]
        total_friends = sum(len(a.friends) for a in active)

        mode_key = {
            "cloud": "dashboard.mode_cloud",
            "local": "dashboard.mode_local",
            "both": "dashboard.mode_both",
        }.get(st.mode, "dashboard.mode_cloud")

        self.statCards[0][1].setText(tr("nav.accounts"))
        self.statCards[0][2].setText(tr("dashboard.accounts_count", count=len(accounts_with_cookie)))

        self.statCards[1][1].setText(tr("content.friends_label"))
        self.statCards[1][2].setText(tr("dashboard.friends_count", count=total_friends))

        self.statCards[2][1].setText(tr("schedule.mode_label"))
        self.statCards[2][2].setText(tr(mode_key))

        self.statCards[3][1].setText(tr("run.local_title"))
        self.statCards[3][2].setText(
            tr("dashboard.last_test_ok") if st.last_test_ok else tr("dashboard.last_test_pending")
        )

        # Step 1: TikTok Account
        s1_ok = bool(accounts_with_cookie) and not blocked_accounts
        self.step1.titleLabel.setText(tr("home.step1_title"))
        self.step1.descLabel.setText(tr("home.step1_desc"))
        if blocked_accounts:
            self.step1.badge.setState(InfoLevel.ERROR, tr("accounts.inbox_blocked"))
            self.step1.detailLabel.setText(tr("err.inbox_unavailable"))
        elif accounts_with_cookie:
            verified_names = [
                f"@{a.username}" if a.username else a.name for a in accounts_with_cookie
            ]
            any_verified = any(a.inbox_verified for a in accounts_with_cookie)
            any_confirmed_user = any(bool((a.username or "").strip()) for a in accounts_with_cookie)
            if any_verified or any_confirmed_user:
                self.step1.badge.setState(
                    InfoLevel.SUCCESS,
                    tr("accounts.inbox_verified") if any_verified else tr("common.ready"),
                )
            else:
                self.step1.badge.setState(InfoLevel.WARNING, tr("accounts.inbox_unchecked"))
            self.step1.detailLabel.setText(", ".join(verified_names))
        else:
            self.step1.badge.setState(InfoLevel.WARNING, tr("home.needs_attention"))
            self.step1.detailLabel.setText(tr("accounts.no_session"))
        self.step1.badge.adjustSize()

        # Step 2: Friends & Message
        vid_ok = True
        vid_err = ""
        if st.video_enabled:
            vcheck = validate_video_url(st.video_url, resolve_short=False)
            vid_ok = bool(vcheck["valid"])
            vid_err = vcheck.get("error") or ""

        s2_ok = total_friends > 0 and bool(st.message.strip()) and vid_ok
        self.step2.titleLabel.setText(tr("home.step2_title"))
        self.step2.descLabel.setText(tr("home.step2_desc"))
        if s2_ok:
            self.step2.badge.setState(InfoLevel.SUCCESS, tr("common.ready"))
            self.step2.detailLabel.setText(
                f"{tr('dashboard.friends_count', count=total_friends)} • {st.message.strip()[:40]}"
            )
        else:
            self.step2.badge.setState(InfoLevel.WARNING, tr("home.needs_attention"))
            reasons = []
            if total_friends == 0:
                reasons.append(tr("accounts.friends_count", count=0))
            if not st.message.strip():
                reasons.append(tr("content.message_label"))
            if vid_err:
                reasons.append(vid_err)
            self.step2.detailLabel.setText(" • ".join(reasons))
        self.step2.badge.adjustSize()

        # Step 3: Delivery (Local / Cloud)
        gh_token = self._secrets.get_github_token()
        repo_raw = (st.github_repo_name or "").strip()
        if "/" in repo_raw:
            parts = [p.strip() for p in repo_raw.split("/")]
            if len(parts) == 2 and parts[0] and parts[1]:
                o_chk = validate_repo_name(parts[0])
                r_chk = validate_repo_name(parts[1])
                repo_check = r_chk if not r_chk["valid"] else (o_chk if not o_chk["valid"] else {"valid": True, "ok": True, "name": parts[1], "error": None})
            else:
                repo_check = {"valid": False, "ok": False, "name": repo_raw, "error": "Invalid repository format (expected owner/repo or repo)."}
        else:
            repo_check = validate_repo_name(repo_raw)
        cloud_ok = bool(gh_token and repo_check["valid"])
        local_ok = bool(st.local_times)
        if st.mode == "cloud":
            s3_ok = cloud_ok
        elif st.mode == "local":
            s3_ok = local_ok
        else:
            s3_ok = cloud_ok and local_ok

        self.step3.titleLabel.setText(tr("home.step3_title"))
        self.step3.descLabel.setText(tr("home.step3_desc"))
        if s3_ok:
            self.step3.badge.setState(InfoLevel.SUCCESS, tr("common.ready"))
            parts = []
            if st.mode in ("cloud", "both"):
                owner = st.github_login or "?"
                repo_disp = repo_raw if "/" in repo_raw else f"{owner}/{repo_raw}"
                parts.append(f"GitHub: {repo_disp}")
            if st.mode in ("local", "both"):
                parts.append(f"PC: {', '.join(st.local_times)}")
            self.step3.detailLabel.setText(" • ".join(parts))
        else:
            self.step3.badge.setState(InfoLevel.WARNING, tr("home.needs_attention"))
            self.step3.detailLabel.setText(
                tr("schedule.not_signed_in") if (st.mode in ("cloud", "both") and not gh_token) else (repo_check.get("error") or "")
            )
        self.step3.badge.adjustSize()

        # Step 4: Safety Test & Alerts
        tg_token = self._secrets.get_telegram_token()
        tg_ready = bool(st.telegram_enabled and tg_token and st.telegram_chat_id)
        s4_ok = bool(st.last_test_ok)
        self.step4.titleLabel.setText(tr("home.step4_title"))
        self.step4.descLabel.setText(tr("home.step4_desc"))
        if s4_ok:
            self.step4.badge.setState(InfoLevel.SUCCESS, tr("dashboard.last_test_ok"))
        else:
            self.step4.badge.setState("neutral", tr("dashboard.last_test_pending"))
        self.step4.badge.adjustSize()
        tg_txt = tr("common.enabled") if tg_ready else tr("common.optional")
        self.step4.detailLabel.setText(f"{tr('cloud.telegram_title')}: {tg_txt}")

        # Highlight ONLY the first incomplete step's CTA as primary
        steps_status = [
            (self.step1, s1_ok),
            (self.step2, s2_ok),
            (self.step3, s3_ok),
            (self.step4, s4_ok),
        ]
        next_row = next((row for row, ok in steps_status if not ok), None)
        for row, _ok in steps_status:
            row.set_primary(row is next_row)
        self._set_quick_test_primary(next_row is None)

        # Overall header badge
        all_ready = s1_ok and s2_ok and s3_ok
        if all_ready:
            self.overallBadge.setState(InfoLevel.SUCCESS, tr("common.ready"))
            self.overallDesc.setText(tr("dashboard.status_ready"))
        else:
            self.overallBadge.setState(InfoLevel.WARNING, tr("home.needs_attention"))
            self.overallDesc.setText(tr("dashboard.status_incomplete"))
        self.overallBadge.adjustSize()

    def retranslate(self) -> None:
        self.welcomeTitle.setText(tr("dashboard.welcome"))
        self.welcomeSubtitle.setText(tr("app.subtitle"))
        self.checklistHeading.setText(tr("home.checklist_heading"))
        self.privacyTitle.setText(tr("security.banner_title"))
        self.privacyDesc.setText(tr("security.banner_desc"))
        self.privacyBtn.setText(escape_qt_mnemonic(tr("security.how_protected_btn")))
        for row in (self.step1, self.step2, self.step3, self.step4):
            row.set_button_text(tr("home.go_step"))
        smoke_txt = escape_qt_mnemonic(tr("dashboard.smoke_test"))
        self._quickTestPrimary.setText(smoke_txt)
        self._quickTestSecondary.setText(smoke_txt)
        self.quickSettingsBtn.setText(escape_qt_mnemonic(tr("nav.settings")))
        self.refresh_state()


__all__ = ["HomePage"]
