"""Home overview page with header command bar, 3 KPI tiles, aligned checklist, and privacy bar."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    HeaderCardWidget,
    InfoLevel,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    TitleLabel,
    TransparentPushButton,
    isDarkTheme,
    qconfig,
)

from gui.services.cloud_deploy import _parse_repo_input
from gui.services.i18n import tr
from gui.services.storage import AppState, SecretStore
from gui.services.theme import INNER_RADIUS, PAGE_MARGINS, escape_qt_mnemonic
from gui.services.updater import is_newer_version
from gui.services.validators import validate_video_url
from gui.version import APP_VERSION
from gui.widgets.fluent_redesign import StepPill
from gui.widgets.security_dialog import SecurityPrivacyDialog
from gui.widgets.split_row import ResponsiveSplitRow
from gui.widgets.status_pill import StatusPill


class _ChecklistItemRow(QFrame):
    """Single compact row inside the Setup Checklist HeaderCardWidget."""

    clicked = Signal()

    def __init__(self, step_num: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HomeChecklistRow")
        self.step_num = step_num
        self.setFixedHeight(48)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(12)

        self.stepPill = StepPill("", "pending", fixed_width=136, parent=self)
        self.detailLabel = BodyLabel("", self)
        self.detailLabel.setWordWrap(False)
        self.detailLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.goBtn = TransparentPushButton(FIF.RIGHT_ARROW, "", self)
        self.goBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.goBtn.clicked.connect(self.clicked.emit)

        lay.addWidget(self.stepPill, 0, Qt.AlignVCenter)
        lay.addWidget(self.detailLabel, 1, Qt.AlignVCenter)
        lay.addWidget(self.goBtn, 0, Qt.AlignVCenter)

        self._is_current = False
        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def set_step(
        self,
        *,
        pill_state: str,
        pill_text: str,
        summary_text: str,
        action_text: str,
    ) -> None:
        self._is_current = pill_state == "current"
        self.stepPill.set_state(pill_state, pill_text)
        self.detailLabel.setText(str(summary_text or ""))
        self.goBtn.setText(escape_qt_mnemonic(action_text))
        self._apply_theme()

    def _apply_theme(self, _theme=None) -> None:
        dark = isDarkTheme()
        if self._is_current:
            bg = "rgba(76, 194, 255, 0.07)" if dark else "#f4f9ff"
            border = "rgba(76, 194, 255, 0.28)" if dark else "#c4ddf8"
        else:
            bg = "rgba(255, 255, 255, 0.03)" if dark else "#f8f9fb"
            border = "rgba(255, 255, 255, 0.07)" if dark else "#e5e8ee"
        fg_text = "#ffffff" if dark else "#1a1a1a"
        self.setStyleSheet(
            f"QFrame#HomeChecklistRow {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        self.detailLabel.setStyleSheet(f"color: {fg_text}; background: transparent;")


class HomePage(SmoothScrollArea):
    """Overview page showing setup status, KPI tiles, aligned checklist, and local privacy."""

    navigate_requested = Signal(str)
    quick_run_requested = Signal(str)

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
        self.vBoxLayout.setSpacing(14)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        # 1. Header + CommandBar (always above the fold; wraps gracefully on narrow widths)
        title_box = QWidget(self.view)
        title_col = QVBoxLayout(title_box)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)

        title_line = QHBoxLayout()
        title_line.setContentsMargins(0, 0, 0, 0)
        title_line.setSpacing(10)
        self.welcomeTitle = TitleLabel(tr("dashboard.welcome"), title_box)
        self.overallBadge = StatusPill("", "neutral", title_box)
        title_line.addWidget(self.welcomeTitle, 0, Qt.AlignVCenter)
        title_line.addWidget(self.overallBadge, 0, Qt.AlignVCenter)
        title_line.addStretch(1)

        self.welcomeSubtitle = BodyLabel(tr("dashboard.subtitle"), title_box)
        self.welcomeSubtitle.setWordWrap(True)
        self.welcomeSubtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_col.addLayout(title_line)
        title_col.addWidget(self.welcomeSubtitle)

        cmd_box = QWidget(self.view)
        cmd_row = QHBoxLayout(cmd_box)
        cmd_row.setContentsMargins(0, 0, 0, 0)
        cmd_row.setSpacing(8)
        self.runAllBtn = PushButton(
            FIF.PLAY, escape_qt_mnemonic(tr("dashboard.run_local_now")), cmd_box
        )
        self.runAllBtn.clicked.connect(lambda: self.quick_run_requested.emit("full"))

        smoke_txt = escape_qt_mnemonic(tr("dashboard.smoke_test"))
        self._quickTestPrimary = PrimaryPushButton(FIF.PLAY, smoke_txt, cmd_box)
        self._quickTestSecondary = PushButton(FIF.PLAY, smoke_txt, cmd_box)
        self._quickTestPrimary.clicked.connect(lambda: self.quick_run_requested.emit("smoke"))
        self._quickTestSecondary.clicked.connect(lambda: self.quick_run_requested.emit("smoke"))
        self.quickTestBtn: PushButton = self._quickTestPrimary

        cmd_row.addWidget(self.runAllBtn, 0, Qt.AlignVCenter)
        cmd_row.addWidget(self._quickTestPrimary, 0, Qt.AlignVCenter)
        cmd_row.addWidget(self._quickTestSecondary, 0, Qt.AlignVCenter)

        self.headerSplit = ResponsiveSplitRow(
            title_box, cmd_box, h_spacing=12, v_spacing=8, left_min_width=220, parent=self.view
        )
        self.vBoxLayout.addWidget(self.headerSplit)

        # 2. Conditional Cloud Drift Banner (routes directly to Automation)
        self.cloudSyncBannerCard = QFrame(self.view)
        self.cloudSyncBannerCard.setObjectName("HomeCloudDriftBanner")
        cs_lay = QHBoxLayout(self.cloudSyncBannerCard)
        cs_lay.setContentsMargins(16, 11, 16, 11)
        cs_lay.setSpacing(12)
        cs_col = QVBoxLayout()
        cs_col.setContentsMargins(0, 0, 0, 0)
        cs_col.setSpacing(2)
        self.cloudSyncBannerTitle = StrongBodyLabel(
            tr("update.cloud_sync_title"), self.cloudSyncBannerCard
        )
        self.cloudSyncBannerDesc = CaptionLabel("", self.cloudSyncBannerCard)
        self.cloudSyncBannerDesc.setWordWrap(True)
        cs_col.addWidget(self.cloudSyncBannerTitle)
        cs_col.addWidget(self.cloudSyncBannerDesc)
        self.cloudSyncGoBtn = PushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("update.cloud_sync_btn")), self.cloudSyncBannerCard
        )
        self.cloudSyncGoBtn.clicked.connect(lambda: self.navigate_requested.emit("automation"))
        cs_lay.addLayout(cs_col, 1)
        cs_lay.addWidget(self.cloudSyncGoBtn, 0, Qt.AlignVCenter)
        self.cloudSyncBannerCard.hide()
        self.vBoxLayout.addWidget(self.cloudSyncBannerCard)

        # 3. Three Equal-Width / Equal-Height KPI Tiles
        kpi_row = QHBoxLayout()
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(12)
        self.statCards: list[tuple[SimpleCardWidget, CaptionLabel, StrongBodyLabel]] = []
        for _idx in range(3):
            card = SimpleCardWidget(self.view)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card.setMinimumHeight(74)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(16, 12, 16, 12)
            c_lay.setSpacing(4)
            cap = CaptionLabel("", card)
            val = StrongBodyLabel("", card)
            val.setWordWrap(True)
            c_lay.addWidget(cap)
            c_lay.addWidget(val)
            c_lay.addStretch(1)
            kpi_row.addWidget(card, 1)
            self.statCards.append((card, cap, val))
        self.vBoxLayout.addLayout(kpi_row)

        # 4. Single Aligned Setup Checklist HeaderCardWidget (1:1 routes)
        self.checklistCard = HeaderCardWidget(self.view)
        self.checklistCard.setTitle(tr("home.checklist_heading"))
        self.checklistCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.checklistProgressBadge = StatusPill("", "info", self.checklistCard)
        self.checklistHintLabel = CaptionLabel(tr("home.checklist_hint"), self.checklistCard)
        self.checklistHintLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.checklistHintLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.checklistCard.headerLayout.addSpacing(8)
        self.checklistCard.headerLayout.addWidget(self.checklistProgressBadge, 0, Qt.AlignVCenter)
        self.checklistCard.headerLayout.addWidget(self.checklistHintLabel, 1, Qt.AlignVCenter)

        self.step1 = _ChecklistItemRow(1, self.checklistCard)
        self.step2 = _ChecklistItemRow(2, self.checklistCard)
        self.step3 = _ChecklistItemRow(3, self.checklistCard)
        self.step4 = _ChecklistItemRow(4, self.checklistCard)

        self.step1.clicked.connect(lambda: self.navigate_requested.emit("accounts"))
        self.step2.clicked.connect(lambda: self.navigate_requested.emit("content"))
        self.step3.clicked.connect(lambda: self.navigate_requested.emit("automation"))
        self.step4.clicked.connect(lambda: self.navigate_requested.emit("run"))

        checklist_body = QVBoxLayout()
        checklist_body.setContentsMargins(0, 0, 0, 0)
        checklist_body.setSpacing(8)
        for row in (self.step1, self.step2, self.step3, self.step4):
            checklist_body.addWidget(row)
        self.checklistCard.viewLayout.addLayout(checklist_body)
        self.vBoxLayout.addWidget(self.checklistCard)

        # 5. Compact Local-First Privacy Bar (with Telegram Settings deep-link)
        self.privacyCard = SimpleCardWidget(self.view)
        priv_outer = QVBoxLayout(self.privacyCard)
        priv_outer.setContentsMargins(16, 12, 16, 12)
        priv_outer.setSpacing(0)

        priv_left = QWidget(self.privacyCard)
        priv_left_lay = QHBoxLayout(priv_left)
        priv_left_lay.setContentsMargins(0, 0, 0, 0)
        priv_left_lay.setSpacing(12)
        self.privacyIcon = QLabel(priv_left)
        self.privacyIcon.setFixedSize(20, 20)
        self.privacyIcon.setAlignment(Qt.AlignCenter)

        priv_text_col = QVBoxLayout()
        priv_text_col.setContentsMargins(0, 0, 0, 0)
        priv_text_col.setSpacing(2)
        self.privacyTitle = StrongBodyLabel(tr("security.banner_title"), priv_left)
        self.privacyTitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.privacyDesc = CaptionLabel(tr("security.banner_desc"), priv_left)
        self.privacyDesc.setWordWrap(True)
        self.privacyDesc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        priv_text_col.addWidget(self.privacyTitle)
        priv_text_col.addWidget(self.privacyDesc)
        priv_left_lay.addWidget(self.privacyIcon, 0, Qt.AlignVCenter)
        priv_left_lay.addLayout(priv_text_col, 1)

        priv_right = QWidget(self.privacyCard)
        priv_right_lay = QHBoxLayout(priv_right)
        priv_right_lay.setContentsMargins(0, 0, 0, 0)
        priv_right_lay.setSpacing(8)
        self.telegramSettingsBtn = TransparentPushButton(
            FIF.SEND, escape_qt_mnemonic(tr("home.telegram_settings_btn")), priv_right
        )
        self.telegramSettingsBtn.clicked.connect(lambda: self.navigate_requested.emit("settings:general"))
        self.privacyBtn = PushButton(
            FIF.INFO, escape_qt_mnemonic(tr("home.privacy_details_btn")), priv_right
        )
        self.privacyBtn.clicked.connect(self._open_security_dialog)
        priv_right_lay.addWidget(self.telegramSettingsBtn, 0, Qt.AlignVCenter)
        priv_right_lay.addWidget(self.privacyBtn, 0, Qt.AlignVCenter)

        self.privacySplit = ResponsiveSplitRow(
            priv_left, priv_right, h_spacing=12, v_spacing=8, left_min_width=260, parent=self.privacyCard
        )
        priv_outer.addWidget(self.privacySplit)
        self.vBoxLayout.addWidget(self.privacyCard)

        self.vBoxLayout.addStretch(1)

        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

        self._set_quick_test_primary(True)
        self.refresh_state()

    def _apply_theme(self, _theme=None) -> None:
        dark = isDarkTheme()
        if dark:
            warn_bg = "rgba(252, 225, 0, 0.08)"
            warn_border = "rgba(252, 225, 0, 0.32)"
            warn_fg = "#fce100"
        else:
            warn_bg = "#fef9ec"
            warn_border = "#d9a94c"
            warn_fg = "#9d5d00"

        self.cloudSyncBannerCard.setStyleSheet(
            f"QFrame#HomeCloudDriftBanner {{"
            f" background-color: {warn_bg};"
            f" border: 1px dashed {warn_border};"
            f" border-radius: 8px;"
            f"}}"
        )
        title_fg = "#ffffff" if dark else "#1a1a1a"
        self.cloudSyncBannerTitle.setStyleSheet(f"color: {title_fg}; background: transparent;")
        self.cloudSyncBannerDesc.setStyleSheet(f"color: {warn_fg}; background: transparent;")
        try:
            self.privacyIcon.setPixmap(FIF.CERTIFICATE.icon().pixmap(18, 18))
        except Exception:
            pass

    def _set_quick_test_primary(self, is_primary: bool) -> None:
        primary = bool(is_primary)
        self._quickTestPrimary.setVisible(primary)
        self._quickTestSecondary.setVisible(not primary)
        self.quickTestBtn = self._quickTestPrimary if primary else self._quickTestSecondary

    def _active_accounts(self):
        if not self._state.accounts:
            return []
        return list(self._state.accounts) if self._state.multi_account else [self._state.accounts[0]]

    def _open_security_dialog(self) -> None:
        dlg = SecurityPrivacyDialog(self.window(), config_dir=self._secrets.config_dir)
        dlg.exec()

    def _sync_cloud_banner(self) -> None:
        deployed_ver = str(getattr(self._state, "last_deployed_version", "") or "").strip().lstrip("vV")
        has_gh = bool(self._secrets.get_github_token())
        show_drift = (
            self._state.mode in ("cloud", "both")
            and has_gh
            and bool(deployed_ver)
            and is_newer_version(APP_VERSION, deployed_ver)
        )
        if show_drift:
            self.cloudSyncBannerTitle.setText(tr("update.cloud_sync_title"))
            self.cloudSyncBannerDesc.setText(
                tr("update.cloud_sync_desc", app_ver=APP_VERSION, deployed_ver=f"v{deployed_ver}")
            )
            self.cloudSyncBannerCard.show()
        else:
            self.cloudSyncBannerCard.hide()

    def refresh_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        active = self._active_accounts()

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

        # KPI 1: Account & Friends
        self.statCards[0][1].setText(tr("home.kpi_account_title"))
        if accounts_with_cookie:
            first_acc = accounts_with_cookie[0]
            first_lbl = f"@{first_acc.username}" if first_acc.username else (first_acc.name or "Main")
            if len(accounts_with_cookie) > 1:
                acc_disp = f"{first_lbl} +{len(accounts_with_cookie) - 1}"
            else:
                acc_disp = first_lbl
            self.statCards[0][2].setText(
                f"{acc_disp} · {tr('accounts.friends_count', count=total_friends)}"
            )
        else:
            self.statCards[0][2].setText(
                f"{tr('accounts.no_session')} · {tr('accounts.friends_count', count=total_friends)}"
            )

        # KPI 2: Automation
        self.statCards[1][1].setText(tr("home.kpi_automation_title"))
        times_src = st.cloud_times if st.mode in ("cloud", "both") else st.local_times
        times_str = ", ".join((times_src or [])[:3])
        if times_str:
            self.statCards[1][2].setText(f"{tr(mode_key)} · {times_str}")
        else:
            self.statCards[1][2].setText(tr(mode_key))

        # KPI 3: Last Run
        self.statCards[2][1].setText(tr("home.kpi_lastrun_title"))
        self.statCards[2][2].setText(
            tr("dashboard.last_test_ok") if st.last_test_ok else tr("dashboard.last_test_pending")
        )

        # Step 1: Account & Target Friends -> "accounts"
        s1_ok = bool(accounts_with_cookie) and not blocked_accounts and total_friends > 0
        if blocked_accounts:
            s1_summary = tr("accounts.inbox_blocked")
        elif accounts_with_cookie:
            first_acc = accounts_with_cookie[0]
            name_lbl = f"@{first_acc.username}" if first_acc.username else (first_acc.name or "Main")
            ver_lbl = tr("accounts.inbox_verified").lower() if first_acc.inbox_verified else tr("common.ready").lower()
            s1_summary = f"{name_lbl} {ver_lbl} · {tr('accounts.friends_count', count=total_friends)}"
        else:
            s1_summary = tr("accounts.no_session")

        # Step 2: Payload (Message & Media) -> "content"
        vid_ok = True
        vid_err = ""
        if st.video_enabled:
            vcheck = validate_video_url(st.video_url, resolve_short=False)
            vid_ok = bool(vcheck["valid"])
            vid_err = vcheck.get("error") or ""
        s2_ok = bool(st.message.strip()) and vid_ok
        if s2_ok:
            raw_msg = st.message.strip()
            short_msg = (raw_msg[:26].rstrip() + "…") if len(raw_msg) > 27 else raw_msg
            parts = [short_msg]
            if st.video_enabled:
                parts.append(f"{max(1, int(st.share_times or 1))}x video")
            if st.photo_enabled:
                parts.append(tr("content.photo_toggle"))
            s2_summary = " · ".join(parts)
        else:
            s2_summary = vid_err or tr("home.step2_desc")

        # Step 3: Automation -> "automation"
        gh_token = self._secrets.get_github_token()
        repo_chk = _parse_repo_input(st.github_repo_name, default_owner=st.github_login)
        cloud_ok = bool(gh_token and repo_chk["valid"])
        local_ok = bool(st.local_times)
        if st.mode == "cloud":
            s3_ok = cloud_ok
        elif st.mode == "local":
            s3_ok = local_ok
        else:
            s3_ok = cloud_ok and local_ok

        if s3_ok:
            s3_parts = []
            if st.mode in ("cloud", "both"):
                repo_disp = repo_chk.get("full_name") or st.github_repo_name
                s3_parts.append(f"{tr('dashboard.mode_cloud')} · {repo_disp}")
            if st.mode in ("local", "both"):
                s3_parts.append(f"{tr('dashboard.mode_local')} · {', '.join(st.local_times[:2])}")
            s3_summary = " · ".join(s3_parts)
        else:
            s3_summary = (
                tr("schedule.not_signed_in")
                if (st.mode in ("cloud", "both") and not gh_token)
                else str(repo_chk.get("error") or tr("home.step3_desc"))
            )

        # Step 4: Test Run -> "run"
        tg_token = self._secrets.get_telegram_token()
        tg_ready = bool(st.telegram_enabled and tg_token and st.telegram_chat_id)
        s4_ok = bool(st.last_test_ok)
        tg_lbl = tr("common.enabled") if tg_ready else tr("common.optional")
        s4_prefix = tr("dashboard.last_test_ok") if s4_ok else tr("dashboard.last_test_pending")
        s4_summary = f"{s4_prefix} · {tr('cloud.telegram_title')}: {tg_lbl}"

        # Highlight ONLY first incomplete step as "current"
        steps_meta = [
            (self.step1, s1_ok, tr("home.step1_title"), s1_summary, tr("nav.accounts")),
            (self.step2, s2_ok, tr("home.step2_title"), s2_summary, tr("nav.content")),
            (self.step3, s3_ok, tr("home.step3_title"), s3_summary, tr("nav.automation")),
            (self.step4, s4_ok, tr("home.step4_title"), s4_summary, tr("nav.run_logs")),
        ]
        found_current = False
        done_count = 0
        for row, ok, title, summary, nav_lbl in steps_meta:
            if ok:
                done_count += 1
                p_state = "done"
            elif not found_current:
                found_current = True
                p_state = "current"
            else:
                p_state = "pending"
            row.set_step(
                pill_state=p_state,
                pill_text=title,
                summary_text=summary,
                action_text=nav_lbl,
            )

        self.checklistProgressBadge.setState(
            InfoLevel.SUCCESS if done_count == 4 else "info",
            tr("home.checklist_progress", done=done_count, total=4),
        )

        core_ready = s1_ok and s2_ok and s3_ok
        self._set_quick_test_primary(core_ready)
        if core_ready:
            self.overallBadge.setState(InfoLevel.SUCCESS, tr("dashboard.status_ready"))
        else:
            self.overallBadge.setState(InfoLevel.WARNING, tr("dashboard.status_incomplete"))
        self.overallBadge.adjustSize()
        self._sync_cloud_banner()

    def retranslate(self) -> None:
        self.welcomeTitle.setText(tr("dashboard.welcome"))
        self.welcomeSubtitle.setText(tr("dashboard.subtitle"))
        self.runAllBtn.setText(escape_qt_mnemonic(tr("dashboard.run_local_now")))
        smoke_txt = escape_qt_mnemonic(tr("dashboard.smoke_test"))
        self._quickTestPrimary.setText(smoke_txt)
        self._quickTestSecondary.setText(smoke_txt)
        self.checklistCard.setTitle(tr("home.checklist_heading"))
        self.checklistHintLabel.setText(tr("home.checklist_hint"))
        self.cloudSyncGoBtn.setText(escape_qt_mnemonic(tr("update.cloud_sync_btn")))
        self.privacyTitle.setText(tr("security.banner_title"))
        self.privacyDesc.setText(tr("security.banner_desc"))
        self.telegramSettingsBtn.setText(escape_qt_mnemonic(tr("home.telegram_settings_btn")))
        self.privacyBtn.setText(escape_qt_mnemonic(tr("home.privacy_details_btn")))
        self.refresh_state()


__all__ = ["HomePage"]
