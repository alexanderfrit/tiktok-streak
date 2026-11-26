"""Dedicated Automation page for Local PC Task Scheduler, GitHub Cloud, and inline preflight."""
from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidgetItem,
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
    HorizontalSeparator,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SimpleCardWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TimePicker,
    TitleLabel,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from gui.services.cloud_deploy import (
    _build_cloud_secrets,
    _deploy_github_task,
    _discover_bot_repos_task,
    _parse_repo_input,
    _pause_multiple_bot_repos_task,
    _toggle_bot_repo_workflow_task,
    _validate_pat_task,
    evaluate_deploy_preflight,
)
from gui.services.github_api import device_flow_start, has_required_scopes
from gui.services.i18n import get_language, tr
from gui.services.scheduler import WindowsScheduler
from gui.services.storage import AppState, ConfigStore, SecretStore
from gui.services.theme import INNER_RADIUS, PAGE_MARGINS, escape_qt_mnemonic
from gui.services.validators import (
    crons_to_local_times,
    format_schedule_summary,
    get_timezone_info,
    times_to_utc_crons,
)
from gui.version import APP_VERSION
from gui.widgets.chip_list import ChipFlowWidget
from gui.widgets.device_login_dialog import DeviceLoginDialog
from gui.widgets.fluent_redesign import (
    ConsolidatedDeployDialog,
    PreflightRow,
    PreflightSummaryTile,
)
from gui.widgets.fluent_spin_box import FluentSpinBox
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController


class _SelectionDot(QWidget):
    """Compact circular radio indicator showing which repository row is active."""

    def __init__(self, selected: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = bool(selected)
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = isDarkTheme()
        if self._selected:
            accent = QColor("#4cc2ff" if dark else "#005fb8")
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(2, 2, 12, 12)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(5, 5, 6, 6)
        else:
            ring = QColor(255, 255, 255, 76) if dark else QColor(0, 0, 0, 76)
            painter.setPen(QPen(ring, 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(2, 2, 12, 12)


class _StatusDot(QWidget):
    """6px semantic dot used inline with repository state text."""

    def __init__(self, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = bool(active)
        self.setFixedSize(8, 8)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = isDarkTheme()
        color = QColor("#6ccb5f" if dark else "#0f7b0f") if self._active else QColor("#f59e0b" if dark else "#9d5d00")
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(1, 1, 6, 6)


class _BotRepoRow(QFrame):
    """Flat clickable repository row with Pause/Resume toggle and open link."""

    clicked = Signal()

    def __init__(
        self,
        repo_item: dict[str, Any],
        is_selected: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("botRepoRow")
        self.repo_item = dict(repo_item)
        full_name = str(repo_item.get("full_name") or "")
        is_active = bool(repo_item.get("active"))
        is_priv = bool(repo_item.get("private"))
        html_url = str(repo_item.get("html_url") or f"https://github.com/{full_name}")

        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)

        self.radioDot = _SelectionDot(is_selected, self)
        self.statusDot = _StatusDot(is_active, self)
        lay.addWidget(self.radioDot, 0, Qt.AlignVCenter)
        lay.addWidget(self.statusDot, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        self.nameLabel = StrongBodyLabel(full_name, self) if is_selected else BodyLabel(full_name, self)
        self.nameLabel.setStyleSheet("background: transparent;")
        self.nameLabel.setMinimumWidth(0)
        self.nameLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.nameLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        state_lbl = tr("cloud.repo_state_active") if is_active else tr("cloud.repo_state_paused")
        vis_lbl = tr("cloud.repo_private_tag") if is_priv else tr("cloud.repo_public_tag")
        meta_parts = [state_lbl, vis_lbl]
        if is_selected:
            meta_parts.append(tr("cloud.repo_selected_tag"))
        self.metaLabel = CaptionLabel(" · ".join(meta_parts), self)
        self.metaLabel.setMinimumWidth(0)
        self.metaLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.metaLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        text_col.addWidget(self.nameLabel)
        text_col.addWidget(self.metaLabel)
        lay.addLayout(text_col, 1)

        fm = self.fontMetrics()
        max_lbl_w = max(
            fm.horizontalAdvance(tr("cloud.pause_schedule")),
            fm.horizontalAdvance(tr("cloud.resume_schedule")),
        )
        uniform_w = max(148, max_lbl_w + 58)
        toggle_txt = tr("cloud.pause_schedule") if is_active else tr("cloud.resume_schedule")
        self.toggleBtn = PushButton(
            FIF.PAUSE if is_active else FIF.PLAY,
            escape_qt_mnemonic(toggle_txt),
            self,
        )
        self.toggleBtn.setFixedHeight(30)
        self.toggleBtn.setFixedWidth(uniform_w)
        self.toggleBtn.setCursor(Qt.ArrowCursor)

        self.openBtn = TransparentToolButton(FIF.LINK, self)
        self.openBtn.setFixedSize(30, 30)
        self.openBtn.setToolTip(f"{tr('cloud.open_repo_tooltip')}\n{html_url}")
        self.openBtn.setCursor(Qt.ArrowCursor)
        self.openBtn.clicked.connect(lambda _=False, u=html_url: QDesktopServices.openUrl(QUrl(u)))

        lay.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)
        lay.addWidget(self.openBtn, 0, Qt.AlignVCenter)
        self.apply_theme(is_selected)

    def apply_theme(self, is_selected: bool) -> None:
        dark = isDarkTheme()
        if is_selected:
            bg = "rgba(76, 194, 255, 0.08)" if dark else "rgba(0, 95, 184, 0.05)"
            border = "rgba(76, 194, 255, 0.28)" if dark else "rgba(0, 95, 184, 0.22)"
            hover_bg = "rgba(76, 194, 255, 0.12)" if dark else "rgba(0, 95, 184, 0.09)"
            meta_fg = "#b8c7d4" if dark else "#455463"
        else:
            bg = "transparent"
            border = "rgba(255, 255, 255, 0.07)" if dark else "rgba(0, 0, 0, 0.07)"
            hover_bg = "rgba(255, 255, 255, 0.04)" if dark else "rgba(0, 0, 0, 0.035)"
            meta_fg = "#a3a3a3" if dark else "#616161"
        name_fg = "#ffffff" if dark else "#1a1a1a"
        self.setStyleSheet(
            f"QFrame#botRepoRow {{ background-color: {bg}; border: 1px solid {border}; border-radius: 6px; }}"
            f"QFrame#botRepoRow:hover {{ background-color: {hover_bg}; }}"
        )
        self.nameLabel.setStyleSheet(f"color: {name_fg}; background: transparent;")
        self.metaLabel.setStyleSheet(f"color: {meta_fg}; background: transparent;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


def _utc_times_from_crons(crons: list[str]) -> str:
    """Format UTC HH:MM list from simple daily cron strings for the compact card header."""
    out: list[str] = []
    for raw in crons:
        parts = str(raw or "").strip().split()
        if len(parts) == 5 and parts[0].isdigit():
            mm = int(parts[0])
            for h_tok in parts[1].split(","):
                if h_tok.strip().isdigit():
                    out.append(f"{int(h_tok.strip()):02d}:{mm:02d}")
    return ", ".join(sorted(dict.fromkeys(out))) if out else ", ".join(crons)


class AutomationPage(SmoothScrollArea):
    """Dedicated scheduling & deployment surface for Local PC, GitHub Cloud, and Hybrid modes."""

    state_changed = Signal()
    log_emitted = Signal(str)
    cloud_repos_updated = Signal(object)
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
        self.setObjectName("automation_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._loading = False
        self._smoke_test = smoke_test
        self._windows_tasks_count: int | None = None
        self._windows_tasks_scanned = False
        self._discovered_all_repos: dict[str, dict[str, Any]] = {}
        self._discovered_bot_repos: list[dict[str, Any]] = []
        self._bot_scan_done_for_token: str = ""
        self._bot_repo_row_widgets: list[QWidget] = []
        self._advanced_expanded = False
        self._custom_cron_dirty = False
        self._allow_existing_non_bot = False
        self._last_preflight: dict[str, Any] = {}
        self._last_deploy_text = ""

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(250)
        self._save_debounce.timeout.connect(self._emit_state_changed)

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
        # 1. Header + Single-Source Timezone Pill (eliminating duplicated Timezone ComboBox)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)
        self.titleLabel = TitleLabel(tr("automation.title"), self.view)
        self.subtitleLabel = BodyLabel(tr("automation.subtitle"), self.view)
        self.subtitleLabel.setWordWrap(True)
        self.subtitleLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_col.addWidget(self.titleLabel)
        title_col.addWidget(self.subtitleLabel)

        self.tzPillCard = QFrame(self.view)
        self.tzPillCard.setObjectName("AutomationTzPill")
        tz_lay = QHBoxLayout(self.tzPillCard)
        tz_lay.setContentsMargins(12, 6, 8, 6)
        tz_lay.setSpacing(8)
        self.tzValueLabel = StrongBodyLabel("", self.tzPillCard)
        self.changeTzBtn = TransparentPushButton(
            FIF.SETTING, escape_qt_mnemonic(tr("automation.tz_change")), self.tzPillCard
        )
        self.changeTzBtn.clicked.connect(lambda: self.navigate_requested.emit("settings:general"))
        tz_lay.addWidget(self.tzValueLabel, 0, Qt.AlignVCenter)
        tz_lay.addWidget(self.changeTzBtn, 0, Qt.AlignVCenter)

        header_row.addLayout(title_col, 1)
        header_row.addWidget(self.tzPillCard, 0, Qt.AlignTop)
        self.vBoxLayout.addLayout(header_row)

        # 2. Execution Mode Bar
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(12)
        self.modeSegment = SegmentedWidget(self.view)
        self.modeSegment.addItem("local", tr("schedule.mode_local"))
        self.modeSegment.addItem("cloud", tr("schedule.mode_cloud"))
        self.modeSegment.addItem("both", tr("schedule.mode_both"))
        self.modeSegment.setFixedHeight(36)
        self.modeSegment.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.modeSegment.currentItemChanged.connect(self._on_mode_changed)

        self.modeDescLabel = CaptionLabel("", self.view)
        self.modeDescLabel.setWordWrap(True)
        self.modeDescLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.modeDescLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        mode_row.addWidget(self.modeSegment, 0, Qt.AlignVCenter)
        mode_row.addWidget(self.modeDescLabel, 1, Qt.AlignVCenter)
        self.vBoxLayout.addLayout(mode_row)

        # 3. Local PC Schedule Card (flat 1-level HeaderCardWidget)
        self.localSection = HeaderCardWidget(self.view)
        self.localSection.setTitle(tr("automation.local_card_title"))
        self.localSection.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.windowsTasksBadge = StatusPill("", "info", self.localSection)
        self.localSection.headerLayout.addStretch(1)
        self.localSection.headerLayout.addWidget(self.windowsTasksBadge, 0, Qt.AlignVCenter)

        l_body = QVBoxLayout()
        l_body.setContentsMargins(0, 0, 0, 0)
        l_body.setSpacing(10)

        l_picker_row = FlowLayout(isTight=True)
        l_picker_row.setContentsMargins(0, 0, 0, 0)
        l_picker_row.setHorizontalSpacing(8)
        l_picker_row.setVerticalSpacing(6)
        self.timePicker = TimePicker(self.localSection)
        self.timePicker.setTime(QTime(8, 0))
        self.addTimeBtn = PushButton(
            FIF.ADD, escape_qt_mnemonic(tr("schedule.add_time")), self.localSection
        )
        self.addTimeBtn.clicked.connect(self._add_local_time)
        self._applyWinPrimaryBtn = PrimaryPushButton(
            FIF.DATE_TIME, escape_qt_mnemonic(tr("schedule.sync_windows")), self.localSection
        )
        self._applyWinSecondaryBtn = PushButton(
            FIF.DATE_TIME, escape_qt_mnemonic(tr("schedule.sync_windows")), self.localSection
        )
        self.applyWinScheduleBtn: PushButton = self._applyWinSecondaryBtn
        self.removeWinScheduleBtn = PushButton(
            FIF.DELETE, escape_qt_mnemonic(tr("schedule.remove_windows")), self.localSection
        )
        self._applyWinPrimaryBtn.clicked.connect(self._apply_windows_schedule)
        self._applyWinSecondaryBtn.clicked.connect(self._apply_windows_schedule)
        self.removeWinScheduleBtn.clicked.connect(self._remove_windows_schedule)

        l_picker_row.addWidget(self.timePicker)
        l_picker_row.addWidget(self.addTimeBtn)
        l_picker_row.addWidget(self._applyWinPrimaryBtn)
        l_picker_row.addWidget(self._applyWinSecondaryBtn)
        l_picker_row.addWidget(self.removeWinScheduleBtn)
        l_body.addLayout(l_picker_row)

        self.localTimesChips = ChipFlowWidget(
            empty_text=tr("schedule.times_empty"),
            icon=FIF.DATE_TIME,
            parent=self.localSection,
        )
        self.localTimesChips.set_remove_tooltip(tr("common.remove"))
        self.localTimesChips.item_removed.connect(self._remove_local_time_by_key)
        l_body.addWidget(self.localTimesChips)

        # Hidden backing list for test compatibility
        self.timesList = ListWidget(self.localSection)
        self.timesList.hide()

        l_opts_row = FlowLayout(isTight=True)
        l_opts_row.setContentsMargins(0, 2, 0, 0)
        l_opts_row.setHorizontalSpacing(10)
        l_opts_row.setVerticalSpacing(6)
        self.showBrowserLabel = BodyLabel(tr("run.show_browser"), self.localSection)
        self.showBrowserSwitch = SwitchButton(self.localSection)
        self.showBrowserSwitch.checkedChanged.connect(self._on_show_browser_changed)
        self.matchesRunBadge = StatusPill(tr("automation.matches_run_logs"), "success", self.localSection)
        self.dailyCapLabel = BodyLabel(tr("content.daily_cap_label"), self.localSection)
        self.dailyCapSpin = FluentSpinBox(self.localSection)
        self.dailyCapSpin.setRange(0, 500)
        self.dailyCapSpin.setFixedWidth(96)
        self.dailyCapSpin.valueChanged.connect(self._on_daily_cap_changed)
        l_opts_row.addWidget(self.showBrowserLabel)
        l_opts_row.addWidget(self.showBrowserSwitch)
        l_opts_row.addWidget(self.matchesRunBadge)
        l_opts_row.addWidget(self.dailyCapLabel)
        l_opts_row.addWidget(self.dailyCapSpin)
        l_body.addLayout(l_opts_row)

        self.localSection.viewLayout.addLayout(l_body)
        self.vBoxLayout.addWidget(self.localSection)

        # 4. GitHub Repository Card (flat 1-level HeaderCardWidget; sole Sign Out lives in Settings)
        self.cloudRepoCard = HeaderCardWidget(self.view)
        self.cloudRepoCard.setTitle(tr("automation.repo_card_title"))
        self.cloudRepoCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.ghStatusBadge = StatusPill("", "warning", self.cloudRepoCard)
        self.ghSigninBtn = PrimaryPushButton(
            FIF.GITHUB, escape_qt_mnemonic(tr("cloud.signin_device")), self.cloudRepoCard
        )
        self.ghSwitchBtn = PushButton(
            FIF.PEOPLE, escape_qt_mnemonic(tr("cloud.reconnect_github")), self.cloudRepoCard
        )
        self.ghSigninBtn.clicked.connect(self._start_github_device_flow)
        self.ghSwitchBtn.clicked.connect(self._start_github_device_flow)

        self.cloudRepoCard.headerLayout.addSpacing(8)
        self.cloudRepoCard.headerLayout.addWidget(self.ghStatusBadge, 0, Qt.AlignVCenter)
        self.cloudRepoCard.headerLayout.addStretch(1)
        self.cloudRepoCard.headerLayout.addWidget(self.ghSigninBtn, 0, Qt.AlignVCenter)
        self.cloudRepoCard.headerLayout.addWidget(self.ghSwitchBtn, 0, Qt.AlignVCenter)

        repo_body = QVBoxLayout()
        repo_body.setContentsMargins(0, 0, 0, 0)
        repo_body.setSpacing(10)

        repo_row = QHBoxLayout()
        repo_row.setContentsMargins(0, 0, 0, 0)
        repo_row.setSpacing(10)
        self.repoNameLabel = BodyLabel(tr("cloud.repo_name"), self.cloudRepoCard)
        self.repoNameEdit = LineEdit(self.cloudRepoCard)
        self.repoNameEdit.setMinimumWidth(140)
        self.repoNameEdit.setMaximumWidth(320)
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.repoNameEdit.textChanged.connect(self._on_repo_name_changed)
        self.privateRepoLabel = BodyLabel(tr("cloud.private_repo"), self.cloudRepoCard)
        self.privateRepoSwitch = SwitchButton(self.cloudRepoCard)
        self.privateRepoSwitch.checkedChanged.connect(self._on_private_repo_changed)

        repo_row.addWidget(self.repoNameLabel, 0, Qt.AlignVCenter)
        repo_row.addWidget(self.repoNameEdit, 1, Qt.AlignVCenter)
        repo_row.addStretch(1)
        repo_row.addWidget(self.privateRepoLabel, 0, Qt.AlignVCenter)
        repo_row.addWidget(self.privateRepoSwitch, 0, Qt.AlignVCenter)
        repo_body.addLayout(repo_row)

        repo_meta_row = QHBoxLayout()
        repo_meta_row.setContentsMargins(0, 0, 0, 0)
        repo_meta_row.setSpacing(8)
        self.repoValidCaption = CaptionLabel("", self.cloudRepoCard)
        self.repoValidCaption.setWordWrap(True)
        self.repoValidCaption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.repoValidBadge = StatusPill("", "warning", self.cloudRepoCard)
        self.repoValidBadge.hide()
        repo_meta_row.addWidget(self.repoValidBadge, 0, Qt.AlignVCenter)
        repo_meta_row.addWidget(self.repoValidCaption, 1, Qt.AlignVCenter)
        repo_body.addLayout(repo_meta_row)

        # Detected Bot Repositories panel
        self.botReposPanel = QWidget(self.cloudRepoCard)
        bot_lay = QVBoxLayout(self.botReposPanel)
        bot_lay.setContentsMargins(0, 2, 0, 0)
        bot_lay.setSpacing(8)
        bot_lay.addWidget(HorizontalSeparator(self.botReposPanel))

        bot_hdr = QHBoxLayout()
        bot_hdr.setContentsMargins(0, 0, 0, 0)
        bot_hdr.setSpacing(8)
        self.botReposHeading = StrongBodyLabel(tr("cloud.bot_repos_title"), self.botReposPanel)
        self.botReposSummaryBadge = StatusPill("", "info", self.botReposPanel)
        self.botReposSummaryBadge.hide()
        self.scanBotReposBtn = TransparentToolButton(FIF.SYNC, self.botReposPanel)
        self.scanBotReposBtn.setFixedSize(30, 30)
        self.scanBotReposBtn.setToolTip(tr("cloud.scan_bot_repos_tooltip"))
        self.scanBotReposBtn.clicked.connect(self.scan_bot_repos)
        bot_hdr.addWidget(self.botReposHeading, 0, Qt.AlignVCenter)
        bot_hdr.addWidget(self.botReposSummaryBadge, 0, Qt.AlignVCenter)
        bot_hdr.addStretch(1)
        bot_hdr.addWidget(self.scanBotReposBtn, 0, Qt.AlignVCenter)
        bot_lay.addLayout(bot_hdr)

        self.botReposEmptyLabel = CaptionLabel(tr("cloud.bot_repos_none"), self.botReposPanel)
        self.botReposEmptyLabel.setWordWrap(True)
        self.botReposEmptyLabel.hide()
        bot_lay.addWidget(self.botReposEmptyLabel)

        self.botReposRowsBox = QWidget(self.botReposPanel)
        self.botReposRowsLayout = QVBoxLayout(self.botReposRowsBox)
        self.botReposRowsLayout.setContentsMargins(0, 0, 0, 0)
        self.botReposRowsLayout.setSpacing(6)
        bot_lay.addWidget(self.botReposRowsBox)

        self.botReposPanel.hide()
        repo_body.addWidget(self.botReposPanel)
        self.cloudRepoCard.viewLayout.addLayout(repo_body)
        self.vBoxLayout.addWidget(self.cloudRepoCard)

        # 5. Daily Cloud Schedule Card (flat 1-level HeaderCardWidget)
        self.cloudScheduleCard = HeaderCardWidget(self.view)
        tz_short = self._active_tz_info()["short"]
        self.cloudScheduleCard.setTitle(tr("automation.schedule_card_title", tz=tz_short))
        self.cloudScheduleCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.utcEquivalentLabel = CaptionLabel("", self.cloudScheduleCard)
        self.cloudScheduleCard.headerLayout.addStretch(1)
        self.cloudScheduleCard.headerLayout.addWidget(self.utcEquivalentLabel, 0, Qt.AlignVCenter)

        sched_body = QVBoxLayout()
        sched_body.setContentsMargins(0, 0, 0, 0)
        sched_body.setSpacing(10)

        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(8)
        self.cloudTimePicker = TimePicker(self.cloudScheduleCard)
        self.cloudTimePicker.setTime(QTime(8, 15))
        self.addCloudTimeBtn = PushButton(
            FIF.ADD, escape_qt_mnemonic(tr("schedule.add_time")), self.cloudScheduleCard
        )
        self.addCloudTimeBtn.clicked.connect(self._add_cloud_time)
        picker_row.addWidget(self.cloudTimePicker, 0, Qt.AlignVCenter)
        picker_row.addWidget(self.addCloudTimeBtn, 0, Qt.AlignVCenter)
        picker_row.addStretch(1)
        sched_body.addLayout(picker_row)

        presets_row = QHBoxLayout()
        presets_row.setContentsMargins(0, 0, 0, 0)
        presets_row.setSpacing(8)
        self.quickPresetsLabel = CaptionLabel(tr("schedule.quick_presets"), self.cloudScheduleCard)
        self.preset1Btn = TransparentPushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_1")), self.cloudScheduleCard
        )
        self.preset2Btn = PushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_2")), self.cloudScheduleCard
        )
        self.preset3Btn = TransparentPushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_3")), self.cloudScheduleCard
        )
        self.preset1Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15"]))
        self.preset2Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15", "20:15"]))
        self.preset3Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15", "14:15", "20:15"]))
        presets_row.addWidget(self.quickPresetsLabel, 0, Qt.AlignVCenter)
        presets_row.addWidget(self.preset1Btn, 0, Qt.AlignVCenter)
        presets_row.addWidget(self.preset2Btn, 0, Qt.AlignVCenter)
        presets_row.addWidget(self.preset3Btn, 0, Qt.AlignVCenter)
        presets_row.addStretch(1)
        sched_body.addLayout(presets_row)

        self.cloudTimesChips = ChipFlowWidget(
            empty_text=tr("schedule.times_empty"),
            icon=FIF.DATE_TIME,
            parent=self.cloudScheduleCard,
        )
        self.cloudTimesChips.set_remove_tooltip(tr("common.remove"))
        self.cloudTimesChips.item_removed.connect(self._remove_cloud_time_by_key)
        self.cloudDelayNote = CaptionLabel(tr("schedule.cloud_delay_note"), self.cloudScheduleCard)
        self.cloudDelayNote.setWordWrap(True)
        self.cloudDelayNote.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sched_body.addWidget(self.cloudTimesChips)
        sched_body.addWidget(self.cloudDelayNote)

        self.cloudTimesList = ListWidget(self.cloudScheduleCard)
        self.cloudTimesList.hide()
        self.cronSummaryLabel = CaptionLabel("", self.cloudScheduleCard)
        self.cronSummaryLabel.hide()

        self.cloudScheduleCard.viewLayout.addLayout(sched_body)
        self.vBoxLayout.addWidget(self.cloudScheduleCard)

        # 6. Single Unified Advanced Disclosure (PAT + secrets-only + raw UTC cron)
        self.advancedDisclosureCard = SimpleCardWidget(self.view)
        adv_outer = QVBoxLayout(self.advancedDisclosureCard)
        adv_outer.setContentsMargins(14, 8, 14, 8)
        adv_outer.setSpacing(10)

        adv_hdr = QHBoxLayout()
        adv_hdr.setContentsMargins(0, 0, 0, 0)
        adv_hdr.setSpacing(8)
        self.advancedToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT,
            escape_qt_mnemonic(tr("automation.advanced_toggle")),
            self.advancedDisclosureCard,
        )
        self.advancedToggleBtn.setMinimumWidth(0)
        self.advancedToggleBtn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.advancedToggleBtn.clicked.connect(self._toggle_advanced_drawer)
        self.advancedOptionalTag = CaptionLabel(tr("common.optional"), self.advancedDisclosureCard)
        adv_hdr.addWidget(self.advancedToggleBtn, 1, Qt.AlignLeft | Qt.AlignVCenter)
        adv_hdr.addWidget(self.advancedOptionalTag, 0, Qt.AlignVCenter)
        adv_outer.addLayout(adv_hdr)

        self.advancedContainer = QWidget(self.advancedDisclosureCard)
        adv_lay = QVBoxLayout(self.advancedContainer)
        adv_lay.setContentsMargins(4, 2, 4, 4)
        adv_lay.setSpacing(10)

        self.syncOnlyCheck = CheckBox(
            escape_qt_mnemonic(tr("cloud.sync_only")), self.advancedContainer
        )
        self.syncOnlyCheck.setMinimumWidth(0)
        self.syncOnlyCheck.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.syncOnlyCheck.stateChanged.connect(lambda _s: self.refresh_preflight())
        adv_lay.addWidget(self.syncOnlyCheck)

        pat_row = QHBoxLayout()
        pat_row.setContentsMargins(0, 0, 0, 0)
        pat_row.setSpacing(8)
        self.patLabel = BodyLabel(tr("cloud.signin_pat"), self.advancedContainer)
        self.patLabel.setFixedWidth(78)
        self.patEdit = PasswordLineEdit(self.advancedContainer)
        self.patEdit.setMinimumWidth(0)
        self.patEdit.setPlaceholderText(tr("schedule.pat_placeholder"))
        self.savePatBtn = PushButton(
            FIF.SAVE, escape_qt_mnemonic(tr("schedule.save_pat")), self.advancedContainer
        )
        self.savePatBtn.clicked.connect(self._save_pat)
        pat_row.addWidget(self.patLabel, 0, Qt.AlignVCenter)
        pat_row.addWidget(self.patEdit, 1, Qt.AlignVCenter)
        pat_row.addWidget(self.savePatBtn, 0, Qt.AlignVCenter)
        adv_lay.addLayout(pat_row)

        cron_row = QHBoxLayout()
        cron_row.setContentsMargins(0, 0, 0, 0)
        cron_row.setSpacing(8)
        self.cronLabel = BodyLabel(tr("schedule.custom_cron"), self.advancedContainer)
        self.cronLabel.setFixedWidth(78)
        self.customCronEdit = LineEdit(self.advancedContainer)
        self.customCronEdit.setMinimumWidth(0)
        self.customCronEdit.setPlaceholderText("15 1,13 * * *")
        self.customCronEdit.textChanged.connect(self._on_custom_cron_changed)
        cron_row.addWidget(self.cronLabel, 0, Qt.AlignVCenter)
        cron_row.addWidget(self.customCronEdit, 1, Qt.AlignVCenter)
        adv_lay.addLayout(cron_row)

        self.advancedContainer.hide()
        adv_outer.addWidget(self.advancedContainer)
        self.vBoxLayout.addWidget(self.advancedDisclosureCard)

        # 7. Persistent Bottom Pre-Deploy Readiness & Deploy Bar
        self.preflightCard = SimpleCardWidget(self.view)
        pf_lay = QVBoxLayout(self.preflightCard)
        pf_lay.setContentsMargins(16, 14, 16, 14)
        pf_lay.setSpacing(10)

        self.preflightHeaderBox = QWidget(self.preflightCard)
        pf_hdr = QHBoxLayout(self.preflightHeaderBox)
        pf_hdr.setContentsMargins(0, 0, 0, 0)
        pf_hdr.setSpacing(10)
        self.preflightHeading = StrongBodyLabel(tr("automation.preflight_heading"), self.preflightHeaderBox)
        self.preflightHeading.setMinimumWidth(0)
        self.preflightHeading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.preflightIssuesBadge = StatusPill("", "warning", self.preflightHeaderBox)
        pf_hdr.addWidget(self.preflightHeading, 1, Qt.AlignVCenter)
        pf_hdr.addWidget(self.preflightIssuesBadge, 0, Qt.AlignVCenter)
        self.preflightHeaderBox.hide()
        pf_lay.addWidget(self.preflightHeaderBox)

        # Compact 3-tile all-pass bar
        self.preflightPassBox = QWidget(self.preflightCard)
        pass_row = QHBoxLayout(self.preflightPassBox)
        pass_row.setContentsMargins(0, 0, 0, 0)
        pass_row.setSpacing(10)
        self.passTileSession = PreflightSummaryTile(parent=self.preflightPassBox)
        self.passTileRepo = PreflightSummaryTile(parent=self.preflightPassBox)
        self.passTileDups = PreflightSummaryTile(parent=self.preflightPassBox)
        self._pass_tiles = {
            "session": self.passTileSession,
            "repo": self.passTileRepo,
            "dups": self.passTileDups,
        }
        pass_row.addWidget(self.passTileSession, 1)
        pass_row.addWidget(self.passTileRepo, 1)
        pass_row.addWidget(self.passTileDups, 1)
        pf_lay.addWidget(self.preflightPassBox)

        # Stacked inline issue rows (with 1-click fixes)
        self.preflightIssuesBox = QWidget(self.preflightCard)
        issues_lay = QVBoxLayout(self.preflightIssuesBox)
        issues_lay.setContentsMargins(0, 0, 0, 0)
        issues_lay.setSpacing(8)
        self.preflightRowSession = PreflightRow("session", parent=self.preflightIssuesBox)
        self.preflightRowRepo = PreflightRow("repo", parent=self.preflightIssuesBox)
        self.preflightRowDups = PreflightRow("dups", parent=self.preflightIssuesBox)
        self._preflight_rows = {
            "session": self.preflightRowSession,
            "repo": self.preflightRowRepo,
            "dups": self.preflightRowDups,
        }
        for row in self._preflight_rows.values():
            row.fix_clicked.connect(self._on_preflight_fix)
            issues_lay.addWidget(row)
        self.preflightIssuesBox.hide()
        pf_lay.addWidget(self.preflightIssuesBox)

        deploy_row = QHBoxLayout()
        deploy_row.setContentsMargins(0, 2, 0, 0)
        deploy_row.setSpacing(12)
        self.deployStatusLabel = CaptionLabel(tr("automation.deploy_ready_hint"), self.preflightCard)
        self.deployStatusLabel.setWordWrap(True)
        self.deployStatusLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.deployBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("schedule.deploy_btn")), self.preflightCard
        )
        self.deployBtn.clicked.connect(self._deploy_to_github)
        deploy_row.addWidget(self.deployStatusLabel, 1, Qt.AlignVCenter)
        deploy_row.addWidget(self.deployBtn, 0, Qt.AlignVCenter)
        pf_lay.addLayout(deploy_row)

        self.vBoxLayout.addWidget(self.preflightCard)
        self.vBoxLayout.addStretch(1)

        self._apply_styles()
        try:
            qconfig.themeChanged.connect(self._apply_styles)
        except Exception:
            pass

    def _apply_styles(self, _theme=None) -> None:
        dark = isDarkTheme()
        pill_bg = "rgba(255, 255, 255, 0.04)" if dark else "#f5f7fa"
        pill_border = "rgba(255, 255, 255, 0.09)" if dark else "#dde2ea"
        valid_fg = "#6ccb5f" if dark else "#0f7b0f"
        muted_fg = "#9a9a9a" if dark else "#616161"

        self.tzPillCard.setStyleSheet(
            f"QFrame#AutomationTzPill {{"
            f" background-color: {pill_bg};"
            f" border: 1px solid {pill_border};"
            f" border-radius: {INNER_RADIUS}px;"
            f"}}"
        )
        fg_primary = "#ffffff" if dark else "#1a1a1a"
        self.tzValueLabel.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        self.repoValidCaption.setStyleSheet(f"color: {valid_fg}; background: transparent;")
        self.cloudDelayNote.setStyleSheet(f"color: {muted_fg}; background: transparent;")

        cur_parsed = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        cur_full = str(cur_parsed.get("full_name") or "").strip().lower()
        for row in self._bot_repo_row_widgets:
            if isinstance(row, _BotRepoRow):
                fn = str(row.repo_item.get("full_name") or "").strip().lower()
                row.apply_theme(bool(cur_full and cur_full == fn))

    # -- Load & Sync -----------------------------------------------------
    def _active_tz_info(self) -> dict[str, Any]:
        return get_timezone_info(getattr(self._state, "timezone", None))

    def _active_tz_id(self) -> str:
        return str(self._active_tz_info()["id"])

    def _active_cloud_times(self) -> list[str]:
        raw = getattr(self._state, "cloud_times", None)
        if isinstance(raw, list) and raw:
            return list(raw)
        return ["08:15"]

    def _format_tz_pill(self) -> str:
        info = self._active_tz_info()
        short = str(info.get("short") or "WIB")
        mins = int(info.get("offset_minutes", 420))
        sign = "+" if mins >= 0 else "-"
        hh = abs(mins) // 60
        mm = abs(mins) % 60
        offset_str = f"UTC{sign}{hh}" if mm == 0 else f"UTC{sign}{hh}:{mm:02d}"
        return f"{short} ({offset_str})"

    def sync_timezone_display(self) -> None:
        tz_info = self._active_tz_info()
        tz_id = str(tz_info["id"])
        tz_short = str(tz_info["short"])
        prev_loading = self._loading
        self._loading = True
        try:
            self.tzValueLabel.setText(self._format_tz_pill())
            self.cloudScheduleCard.setTitle(tr("automation.schedule_card_title", tz=tz_short))
            self._reload_times_list()
            self._reload_cloud_times_list()
            if not self._custom_cron_dirty:
                try:
                    self.customCronEdit.setText("\n".join(times_to_utc_crons(self._active_cloud_times(), tz_id)))
                except Exception:
                    pass
            self._update_cron_summary()
        finally:
            self._loading = prev_loading

    def refresh_from_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        self._loading = True
        try:
            mode = st.mode if st.mode in ("local", "cloud", "both") else "cloud"
            self.modeSegment.setCurrentItem(mode)
            self._sync_mode_visibility(mode)

            self.showBrowserSwitch.setChecked(not bool(st.headless_local))
            self.dailyCapSpin.setValue(max(0, int(st.daily_cap)))

            self.privateRepoSwitch.setChecked(bool(st.github_private))
            if self.repoNameEdit.text() != st.github_repo_name:
                self.repoNameEdit.setText(st.github_repo_name)
            self._update_repo_badge()

            tz_id = self._active_tz_id()
            self.tzValueLabel.setText(self._format_tz_pill())
            tz_short = self._active_tz_info()["short"]
            self.cloudScheduleCard.setTitle(tr("automation.schedule_card_title", tz=tz_short))

            cloud_times = list(getattr(st, "cloud_times", None) or ["08:15"])
            raw_cron = str(st.cloud_cron or "").strip()
            try:
                derived_default = "\n".join(times_to_utc_crons(cloud_times, tz_id))
            except Exception:
                derived_default = ""

            is_complex_custom = False
            if raw_cron and raw_cron != derived_default:
                parsed_local = crons_to_local_times(raw_cron, tz_id)
                if parsed_local and cloud_times == ["08:00"]:
                    try:
                        roundtrip = "\n".join(times_to_utc_crons(parsed_local, tz_id))
                    except Exception:
                        roundtrip = ""
                    if roundtrip == raw_cron:
                        cloud_times = parsed_local
                        self._state.cloud_times = list(cloud_times)
                    else:
                        is_complex_custom = True
                elif not parsed_local:
                    is_complex_custom = True

            self._custom_cron_dirty = is_complex_custom
            if is_complex_custom:
                self.customCronEdit.setText(raw_cron)
                self._set_advanced_expanded(True)
            else:
                try:
                    self.customCronEdit.setText("\n".join(times_to_utc_crons(cloud_times, tz_id)))
                except Exception:
                    self.customCronEdit.setText(raw_cron)

            self._reload_times_list()
            self._reload_cloud_times_list()
            self._update_cron_summary()
            self._update_windows_tasks_badge()
            self._refresh_github_badge()
            self.refresh_preflight()
        finally:
            self._loading = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loading:
            self.sync_timezone_display()
            self.refresh_preflight()

    def _emit_state_changed(self) -> None:
        self.state_changed.emit()

    def _schedule_save(self) -> None:
        if not self._loading:
            self._save_debounce.start()

    def flush_edits(self) -> None:
        if self._save_debounce.isActive():
            self._save_debounce.stop()
            self.state_changed.emit()

    # -- Mode & Local PC Schedule ----------------------------------------
    def _on_mode_changed(self, route_key: str) -> None:
        if self._loading or route_key not in ("local", "cloud", "both"):
            return
        self._state.mode = route_key
        self._sync_mode_visibility(route_key)
        self.state_changed.emit()

    def _sync_mode_visibility(self, mode: str) -> None:
        show_local = mode in ("local", "both")
        show_cloud = mode in ("cloud", "both")
        self.localSection.setVisible(show_local)
        self.cloudRepoCard.setVisible(show_cloud)
        self.cloudScheduleCard.setVisible(show_cloud)
        self.advancedDisclosureCard.setVisible(show_cloud)
        self.preflightCard.setVisible(show_cloud)

        is_local_only = mode == "local"
        self._applyWinPrimaryBtn.setVisible(is_local_only)
        self._applyWinSecondaryBtn.setVisible(not is_local_only)
        self.applyWinScheduleBtn = (
            self._applyWinPrimaryBtn if is_local_only else self._applyWinSecondaryBtn
        )
        desc_key = {
            "local": "automation.mode_desc_local",
            "cloud": "automation.mode_desc_cloud",
            "both": "automation.mode_desc_both",
        }.get(mode, "automation.mode_desc_cloud")
        self.modeDescLabel.setText(tr(desc_key))

    def _reload_times_list(self) -> None:
        self.timesList.clear()
        tz_short = self._active_tz_info()["short"]
        chip_items: list[tuple[str, str]] = []
        for t in self._state.local_times:
            label = f"{t} {tz_short}"
            chip_items.append((t, label))
            item = QListWidgetItem(FIF.DATE_TIME.icon(), label)
            item.setSizeHint(QSize(0, 32))
            self.timesList.addItem(item)
        self.localTimesChips.set_items(chip_items)

    def _add_local_time(self) -> None:
        qt = self.timePicker.getTime()
        hhmm = f"{qt.hour():02d}:{qt.minute():02d}"
        if hhmm not in self._state.local_times:
            self._state.local_times = sorted([*self._state.local_times, hhmm])
            self._reload_times_list()
            self.state_changed.emit()

    def _remove_local_time_by_key(self, hhmm: str) -> None:
        target = str(hhmm or "").strip()
        if target not in self._state.local_times:
            return
        if len(self._state.local_times) == 1:
            InfoBar.warning(
                tr("common.warning"),
                tr("schedule.local_times_tz", tz=self._active_tz_info()["short"]),
                duration=2500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self._state.local_times = [t for t in self._state.local_times if t != target]
        self._reload_times_list()
        self.state_changed.emit()

    def _update_windows_tasks_badge(self) -> None:
        cnt = max(0, int(self._windows_tasks_count or 0))
        if sys.platform != "win32" or self._windows_tasks_count is None:
            level = "info"
        else:
            level = "success" if cnt else "warning"
        self.windowsTasksBadge.setState(level, tr("schedule.local_tasks_count", count=cnt))

    def maybe_refresh_windows_tasks(self) -> None:
        if self._smoke_test or self._windows_tasks_scanned:
            return
        self._windows_tasks_scanned = True
        self._refresh_windows_tasks()

    def _refresh_windows_tasks(self) -> None:
        if sys.platform != "win32":
            self._windows_tasks_count = 0
            self._update_windows_tasks_badge()
            return
        self._workers.start_task(
            WindowsScheduler().list_tasks,
            task_key="list_windows_tasks",
            on_finished=self._on_windows_tasks_listed,
            on_error=lambda _e: None,
        )

    def _on_windows_tasks_listed(self, tasks: list[dict[str, str]]) -> None:
        self._windows_tasks_count = len(tasks or [])
        self._update_windows_tasks_badge()

    def _set_apply_win_enabled(self, enabled: bool) -> None:
        self._applyWinPrimaryBtn.setEnabled(enabled)
        self._applyWinSecondaryBtn.setEnabled(enabled)

    def _apply_windows_schedule(self) -> None:
        times = list(self._state.local_times)
        self._set_apply_win_enabled(False)
        self._workers.start_task(
            WindowsScheduler().sync_daily_tasks,
            times,
            task_key="sync_windows_tasks",
            on_finished=self._on_windows_sync_ok,
            on_error=self._on_windows_sync_err,
        )

    def _on_windows_sync_ok(self, res: dict[str, Any]) -> None:
        self._set_apply_win_enabled(True)
        created = res.get("created") or []
        self._on_windows_tasks_listed(created)
        InfoBar.success(
            tr("common.success"),
            tr("schedule.local_tasks_count", count=len(created)),
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_windows_sync_err(self, err: str) -> None:
        self._set_apply_win_enabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _remove_windows_schedule(self) -> None:
        self.removeWinScheduleBtn.setEnabled(False)
        self._workers.start_task(
            WindowsScheduler().remove_all_tasks,
            task_key="remove_windows_tasks",
            on_finished=self._on_windows_removed,
            on_error=self._on_windows_remove_err,
        )

    def _on_windows_removed(self, _removed: list[str]) -> None:
        self.removeWinScheduleBtn.setEnabled(True)
        self._on_windows_tasks_listed([])
        InfoBar.success(
            tr("common.success"),
            tr("schedule.local_tasks_count", count=0),
            duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_windows_remove_err(self, err: str) -> None:
        self.removeWinScheduleBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def sync_headless(self) -> None:
        """Synchronize Show browser window switch with state.headless_local (exact RunPage polarity)."""
        self._loading = True
        try:
            self.showBrowserSwitch.setChecked(not bool(self._state.headless_local))
        finally:
            self._loading = False

    def _on_show_browser_changed(self, checked: bool) -> None:
        if not self._loading:
            self._state.headless_local = not bool(checked)
            self.state_changed.emit()

    def _on_daily_cap_changed(self, val: int) -> None:
        if not self._loading:
            self._state.daily_cap = int(val)
            self._schedule_save()

    # -- Unified Advanced Disclosure & GitHub Auth -----------------------
    def _set_advanced_expanded(self, expanded: bool) -> None:
        self._advanced_expanded = bool(expanded)
        self.advancedContainer.setVisible(self._advanced_expanded)
        self.advancedToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._advanced_expanded else FIF.CHEVRON_RIGHT
        )

    def _toggle_advanced_drawer(self) -> None:
        self._set_advanced_expanded(not self._advanced_expanded)

    def _refresh_github_badge(self) -> None:
        has_tok = bool(self._secrets.get_github_token())
        login = self._state.github_login.strip()
        if has_tok and login:
            self.ghStatusBadge.setState("success", tr("cloud.signed_in_as", login=login))
        elif has_tok:
            self.ghStatusBadge.setState("info", tr("common.ready"))
        else:
            self.ghStatusBadge.setState("warning", tr("schedule.not_signed_in"))

        self.ghSigninBtn.setVisible(not has_tok)
        self.ghSwitchBtn.setVisible(has_tok)
        self.botReposPanel.setVisible(has_tok)

    def _set_gh_auth_buttons_enabled(self, enabled: bool) -> None:
        self.ghSigninBtn.setEnabled(enabled)
        self.ghSwitchBtn.setEnabled(enabled)

    def _start_github_device_flow(self) -> None:
        self._set_gh_auth_buttons_enabled(False)
        self._workers.start_task(
            device_flow_start,
            task_key="github_device_start",
            on_finished=self._on_device_start_ok,
            on_error=self._on_device_start_err,
        )

    def _on_device_start_ok(self, info: dict[str, Any]) -> None:
        self._set_gh_auth_buttons_enabled(True)
        dlg = DeviceLoginDialog(
            parent=self.window(),
            worker_controller=self._workers,
            user_code=info.get("user_code", ""),
            verification_uri=info.get("verification_uri", "https://github.com/login/device"),
            device_code=info.get("device_code", ""),
            interval=int(info.get("interval") or 5),
            expires_in=int(info.get("expires_in") or 900),
        )
        if dlg.exec() and dlg.result_token:
            self._secrets.set_github_token(dlg.result_token)
            self._state.github_login = str(dlg.result_user.get("login") or "").strip()
            self._bot_scan_done_for_token = ""
            self._allow_existing_non_bot = False
            self._refresh_github_badge()
            self.refresh_preflight()
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                tr("cloud.signed_in_as", login=self._state.github_login),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            self.maybe_auto_scan_bot_repos()

    def _on_device_start_err(self, err: str) -> None:
        self._set_gh_auth_buttons_enabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _save_pat(self) -> None:
        tok = self.patEdit.text().strip()
        if not tok:
            return
        self.savePatBtn.setEnabled(False)
        self._workers.start_task(
            _validate_pat_task,
            tok,
            task_key="validate_pat",
            on_finished=self._on_pat_valid,
            on_error=self._on_pat_err,
        )

    def _on_pat_valid(self, res: dict[str, Any]) -> None:
        self.savePatBtn.setEnabled(True)
        user = res.get("user") or {}
        if user.get("scopes_header_present") and not has_required_scopes(user.get("scopes") or []):
            InfoBar.error(
                tr("common.error"),
                tr("err.http_403"),
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        self._secrets.set_github_token(res["token"])
        self._state.github_login = str(user.get("login") or "").strip()
        self._bot_scan_done_for_token = ""
        self._allow_existing_non_bot = False
        self.patEdit.clear()
        self._refresh_github_badge()
        self.refresh_preflight()
        self.state_changed.emit()
        InfoBar.success(
            tr("common.success"),
            tr("cloud.signed_in_as", login=self._state.github_login),
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )
        self.maybe_auto_scan_bot_repos()

    def _on_pat_err(self, err: str) -> None:
        self.savePatBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def clear_github_session_ui(self) -> None:
        """Reset discovered repo UI when Settings signs out of GitHub."""
        self._bot_scan_done_for_token = ""
        self._discovered_bot_repos = []
        self._discovered_all_repos = {}
        self._allow_existing_non_bot = False
        self._render_bot_repos_panel()
        self.cloud_repos_updated.emit([])
        self._refresh_github_badge()
        self.refresh_preflight()

    def _on_private_repo_changed(self, checked: bool) -> None:
        if not self._loading:
            self._state.github_private = bool(checked)
            self.state_changed.emit()

    def _on_repo_name_changed(self, text: str) -> None:
        self._allow_existing_non_bot = False
        if not self._loading:
            self._state.github_repo_name = text.strip()
            self._schedule_save()
        self._update_repo_badge()
        if self._discovered_bot_repos:
            self._render_bot_repos_panel()
        self.refresh_preflight()

    def _update_repo_badge(self) -> None:
        chk = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        if chk["valid"]:
            target_full = str(chk.get("full_name") or "").strip().lower()
            bot_match = next(
                (r for r in self._discovered_bot_repos if str(r.get("full_name") or "").lower() == target_full),
                None,
            )
            if bot_match is not None:
                state_lbl = tr("cloud.repo_state_active") if bot_match.get("active") else tr("cloud.repo_state_paused")
                self.repoValidBadge.hide()
                self.repoValidCaption.setText(f"✓ {tr('cloud.repo_status_existing_bot', state=state_lbl.lower())}")
                self.repoValidCaption.show()
            elif target_full and target_full in self._discovered_all_repos:
                self.repoValidCaption.hide()
                self.repoValidBadge.setState("warning", tr("cloud.repo_status_non_bot"))
                self.repoValidBadge.show()
            elif self._bot_scan_done_for_token and target_full and "/" in target_full:
                self.repoValidBadge.hide()
                self.repoValidCaption.setText(f"✓ {tr('cloud.repo_status_new')}")
                self.repoValidCaption.show()
            else:
                self.repoValidBadge.hide()
                self.repoValidCaption.setText(f"✓ {tr('content.repo_valid')}")
                self.repoValidCaption.show()
            self.syncOnlyCheck.setEnabled(True)
        else:
            self.repoValidCaption.hide()
            self.repoValidBadge.setState("warning", chk.get("error") or tr("common.warning"))
            self.repoValidBadge.show()
            self.syncOnlyCheck.setEnabled(False)

    # -- Bot Repo Discovery ----------------------------------------------
    def maybe_auto_scan_bot_repos(self) -> None:
        if self._smoke_test:
            return
        token = self._secrets.get_github_token().strip()
        if not token or self._bot_scan_done_for_token == token:
            return
        self.scan_bot_repos()

    def scan_bot_repos(self) -> None:
        if self._workers.is_running("discover_bot_repos"):
            return
        token = self._secrets.get_github_token().strip()
        if not token:
            return
        self.scanBotReposBtn.setEnabled(False)
        self.botReposSummaryBadge.setState("info", tr("cloud.bot_repos_scanning"))
        self.botReposSummaryBadge.show()
        self._workers.start_task(
            _discover_bot_repos_task,
            token,
            task_key="discover_bot_repos",
            on_finished=self._on_bot_repos_discovered,
            on_error=self._on_bot_repos_scan_error,
        )

    def _on_bot_repos_discovered(self, payload: Any) -> None:
        self.scanBotReposBtn.setEnabled(True)
        self._bot_scan_done_for_token = self._secrets.get_github_token().strip()
        if not isinstance(payload, dict):
            payload = {}
        all_repos = list(payload.get("all_repos") or [])
        self._discovered_all_repos = {
            str(r.get("full_name") or "").strip().lower(): r
            for r in all_repos
            if str(r.get("full_name") or "").strip()
        }
        self._discovered_bot_repos = [
            dict(r) for r in (payload.get("bot_repos") or []) if isinstance(r, dict) and r.get("full_name")
        ]
        self._render_bot_repos_panel()
        self._update_repo_badge()
        self.refresh_preflight()
        self.cloud_repos_updated.emit(list(self._discovered_bot_repos))

    def _on_bot_repos_scan_error(self, _err: str) -> None:
        self.scanBotReposBtn.setEnabled(True)
        self._render_bot_repos_panel()

    def set_repo_workflow_state(self, full_name: str, wf_state: str) -> None:
        norm = str(full_name or "").strip().lower()
        clean_state = str(wf_state or "").strip().lower()
        changed = False
        for r in self._discovered_bot_repos:
            if str(r.get("full_name") or "").strip().lower() == norm:
                r["workflow_state"] = clean_state
                r["active"] = clean_state == "active"
                changed = True
        if changed:
            self._render_bot_repos_panel()
            self._update_repo_badge()
            self.refresh_preflight()

    def _select_bot_repo(self, repo_item: dict[str, Any]) -> None:
        full_name = str(repo_item.get("full_name") or "").strip()
        is_priv = bool(repo_item.get("private", False))
        if full_name:
            self.repoNameEdit.setText(full_name)
        self.privateRepoSwitch.setChecked(is_priv)
        self._render_bot_repos_panel()
        self.refresh_preflight()

    def _toggle_bot_repo_workflow(self, full_name: str, pause: bool) -> None:
        token = self._secrets.get_github_token().strip()
        if not token or not full_name:
            return
        self.scanBotReposBtn.setEnabled(False)
        self._workers.start_task(
            _toggle_bot_repo_workflow_task,
            token,
            full_name,
            pause,
            task_key=f"toggle_bot_repo_{full_name}",
            on_finished=self._on_bot_repo_toggled,
            on_error=self._on_bot_repo_toggle_error,
        )

    def _on_bot_repo_toggled(self, payload: dict[str, Any]) -> None:
        self.scanBotReposBtn.setEnabled(True)
        full_name = str(payload.get("full_name") or "").strip()
        wf_state = str(payload.get("workflow_state") or ("disabled_manually" if not payload.get("active") else "active"))
        self.set_repo_workflow_state(full_name, wf_state)
        self.cloud_repos_updated.emit(list(self._discovered_bot_repos))
        msg_key = "cloud.schedule_resumed_ok" if payload.get("active") else "cloud.schedule_paused_ok"
        InfoBar.success(
            tr("common.success"),
            tr(msg_key, repo=full_name),
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_bot_repo_toggle_error(self, err: str) -> None:
        self.scanBotReposBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _render_bot_repos_panel(self) -> None:
        while self._bot_repo_row_widgets:
            w = self._bot_repo_row_widgets.pop()
            self.botReposRowsLayout.removeWidget(w)
            w.deleteLater()

        repos = list(self._discovered_bot_repos or [])
        self.botReposSummaryBadge.hide()

        if not repos:
            self.botReposEmptyLabel.setText(tr("cloud.bot_repos_none"))
            self.botReposEmptyLabel.setVisible(bool(self._bot_scan_done_for_token))
            return

        self.botReposEmptyLabel.hide()
        cur_parsed = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        cur_full = str(cur_parsed.get("full_name") or "").strip().lower()

        for item in repos:
            full_name = str(item.get("full_name") or "")
            is_active = bool(item.get("active"))
            is_cur = bool(cur_full and cur_full == full_name.lower())

            row = _BotRepoRow(item, is_selected=is_cur, parent=self.botReposRowsBox)
            row.clicked.connect(lambda it=dict(item): self._select_bot_repo(it))
            row.toggleBtn.clicked.connect(
                lambda _=False, fn=full_name, p=is_active: self._toggle_bot_repo_workflow(fn, p)
            )
            self.botReposRowsLayout.addWidget(row)
            self._bot_repo_row_widgets.append(row)

    def _pause_other_bot_repos(self) -> None:
        token = self._secrets.get_github_token().strip()
        if not token:
            return
        other_active = list(self._last_preflight.get("other_active") or [])
        if not other_active:
            return
        self.scanBotReposBtn.setEnabled(False)
        self._workers.start_task(
            _pause_multiple_bot_repos_task,
            token,
            other_active,
            task_key="pause_other_bot_repos",
            on_finished=self._on_other_bots_paused,
            on_error=self._on_other_bots_pause_error,
        )

    def _on_other_bots_paused(self, payload: Any) -> None:
        self.scanBotReposBtn.setEnabled(True)
        paused = list((payload or {}).get("paused") or [])
        for fn in paused:
            self.set_repo_workflow_state(fn, "disabled_manually")
        self.cloud_repos_updated.emit(list(self._discovered_bot_repos))
        if paused:
            InfoBar.success(
                tr("common.success"),
                tr("cloud.paused_others_ok", count=len(paused)),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_other_bots_pause_error(self, err: str) -> None:
        self.scanBotReposBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    # -- Daily Cloud Schedule --------------------------------------------
    def _reload_cloud_times_list(self) -> None:
        self.cloudTimesList.clear()
        tz_short = self._active_tz_info()["short"]
        chip_items: list[tuple[str, str]] = []
        for t in self._active_cloud_times():
            label = f"{t} {tz_short}"
            chip_items.append((t, label))
            item = QListWidgetItem(FIF.DATE_TIME.icon(), label)
            item.setSizeHint(QSize(0, 32))
            self.cloudTimesList.addItem(item)
        self.cloudTimesChips.set_items(chip_items)

    def _set_cloud_times(self, times: list[str]) -> None:
        cleaned = sorted(dict.fromkeys(str(t).strip() for t in times if str(t).strip()))
        if not cleaned:
            cleaned = ["08:15"]
        self._state.cloud_times = cleaned
        tz_id = self._active_tz_id()
        crons = times_to_utc_crons(cleaned, tz_id)
        cron_str = "\n".join(crons)
        self._state.cloud_cron = cron_str
        self._custom_cron_dirty = False
        prev_loading = self._loading
        self._loading = True
        try:
            self.customCronEdit.setText(cron_str)
        finally:
            self._loading = prev_loading
        self._reload_cloud_times_list()
        self._update_cron_summary()
        self.state_changed.emit()

    def _add_cloud_time(self) -> None:
        qt = self.cloudTimePicker.getTime()
        hhmm = f"{qt.hour():02d}:{qt.minute():02d}"
        current = self._active_cloud_times()
        if hhmm not in current:
            self._set_cloud_times([*current, hhmm])

    def _remove_cloud_time_by_key(self, hhmm: str) -> None:
        target = str(hhmm or "").strip()
        current = self._active_cloud_times()
        if target not in current:
            return
        if len(current) == 1:
            InfoBar.warning(
                tr("common.warning"),
                tr("schedule.cloud_times", tz=self._active_tz_info()["short"]),
                duration=2500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self._set_cloud_times([t for t in current if t != target])

    def _apply_cloud_preset(self, times: list[str]) -> None:
        self._set_cloud_times(times)

    def _on_custom_cron_changed(self, text: str) -> None:
        if not self._loading and self._advanced_expanded:
            self._custom_cron_dirty = True
            cleaned = "\n".join(
                " ".join(line.strip().split())
                for line in text.replace(";", "\n").splitlines()
                if line.strip()
            )
            if cleaned:
                self._state.cloud_cron = cleaned
                self._schedule_save()
        self._update_cron_summary()

    def _update_cron_summary(self) -> None:
        tz_id = self._active_tz_id()
        if self._advanced_expanded and self._custom_cron_dirty:
            raw_txt = self.customCronEdit.text().strip() or self._state.cloud_cron
            crons_list = [ln.strip() for ln in raw_txt.splitlines() if ln.strip()]
            crons_arg: Any = raw_txt
        else:
            try:
                crons_list = times_to_utc_crons(self._active_cloud_times(), tz_id)
                crons_arg = crons_list
            except Exception:
                crons_list = [self._state.cloud_cron] if self._state.cloud_cron else []
                crons_arg = self._state.cloud_cron
        summary = format_schedule_summary(crons_arg, tz_id=tz_id, lang=get_language())
        self.cronSummaryLabel.setText(tr("schedule.cloud_summary", summary=summary))
        self.utcEquivalentLabel.setText(
            tr("automation.utc_equivalent", times=_utc_times_from_crons(crons_list))
        )

    # -- Inline Pre-Deploy Readiness & Deploy ----------------------------
    def refresh_preflight(self) -> dict[str, Any]:
        pf = evaluate_deploy_preflight(
            self._state,
            self._secrets,
            self.repoNameEdit.text(),
            discovered_bot_repos=self._discovered_bot_repos,
            discovered_all_repos=self._discovered_all_repos,
            sync_only=bool(self.syncOnlyCheck.isEnabled() and self.syncOnlyCheck.isChecked()),
            allow_existing_non_bot=self._allow_existing_non_bot,
        )
        self._last_preflight = pf
        checks_by_id = {c["id"]: c for c in pf["checks"]}
        issue_count = int(pf["issue_count"])

        if issue_count == 0 and not pf["has_blocking"]:
            self.preflightHeaderBox.hide()
            self.preflightIssuesBox.hide()
            self.preflightPassBox.show()
            for cid, tile in self._pass_tiles.items():
                chk = checks_by_id[cid]
                tile.set_content(chk["title"], chk["detail"])
            if not self._workers.is_running("deploy_github"):
                self.deployStatusLabel.setText(self._last_deploy_text or tr("automation.deploy_ready_hint"))
        else:
            self.preflightPassBox.hide()
            self.preflightHeaderBox.show()
            self.preflightIssuesBox.show()
            badge_cnt = max(1, issue_count)
            self.preflightIssuesBadge.setState(
                "error" if pf["has_blocking"] else "warning",
                tr("automation.preflight_issues_badge", count=badge_cnt),
            )
            for cid, row in self._preflight_rows.items():
                chk = checks_by_id[cid]
                row.update_check(
                    status=chk["status"],
                    title=chk["title"],
                    detail=chk["detail"],
                    fix_key=chk.get("fix"),
                    fix_label=str(chk.get("fix_label") or ""),
                )
            if not self._workers.is_running("deploy_github"):
                self.deployStatusLabel.setText(
                    pf["video_error"] if pf.get("video_error") else tr("automation.preflight_resolve_hint")
                )

        if not self._workers.is_running("deploy_github"):
            self.deployBtn.setEnabled(not pf["has_blocking"])
        return pf

    def _on_preflight_fix(self, fix_key: str) -> None:
        if fix_key == "browser_login":
            self.navigate_requested.emit("accounts")
        elif fix_key == "connect_github":
            self._start_github_device_flow()
        elif fix_key == "allow_existing":
            self._allow_existing_non_bot = True
            self.refresh_preflight()
        elif fix_key == "pause_others":
            self._pause_other_bot_repos()

    def _deploy_to_github(self) -> None:
        if self._workers.is_running("deploy_github"):
            return
        self.flush_edits()

        pf = self.refresh_preflight()
        if pf["has_blocking"]:
            msg = pf["blocking_reasons"][0] if pf["blocking_reasons"] else tr("common.warning")
            InfoBar.warning(
                tr("common.warning"),
                msg,
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        token = self._secrets.get_github_token().strip()
        repo_chk = pf["repo_parsed"]

        try:
            desired_secrets = _build_cloud_secrets(self._state, self._secrets)
        except Exception as exc:
            InfoBar.warning(
                tr("common.warning"),
                str(exc),
                duration=4500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if self._advanced_expanded and self._custom_cron_dirty:
            lines = [
                " ".join(line.strip().split())
                for line in self.customCronEdit.text().replace(";", "\n").splitlines()
                if line.strip()
            ]
            if not lines or any(len(line.split(" ")) != 5 for line in lines):
                InfoBar.warning(
                    tr("common.warning"),
                    "Invalid 5-field UTC cron expression.",
                    duration=4000,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self.window(),
                )
                return
            cron_expr = "\n".join(lines)
        else:
            try:
                cron_expr = "\n".join(times_to_utc_crons(self._active_cloud_times(), self._active_tz_id()))
            except Exception as exc:
                InfoBar.warning(
                    tr("common.warning"),
                    str(exc),
                    duration=4000,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self.window(),
                )
                return

        pause_repos: list[str] = []
        warnings = list(pf.get("warnings") or [])
        other_active = list(pf.get("other_active") or [])
        if warnings:
            dlg = ConsolidatedDeployDialog(
                self.window(),
                warnings,
                has_duplicates=bool(other_active),
            )
            if not dlg.exec():
                return
            if other_active and dlg.choice == "pause":
                pause_repos = other_active

        raw_repo_arg = (
            repo_chk["full_name"]
            if "/" in self.repoNameEdit.text().strip()
            else repo_chk["name"]
        )
        sync_only = bool(self.syncOnlyCheck.isEnabled() and self.syncOnlyCheck.isChecked())

        self.deployBtn.setEnabled(False)
        self.deployStatusLabel.setText(tr("common.loading"))
        deploy_kwargs: dict[str, Any] = {
            "sync_only": sync_only,
            "task_key": "deploy_github",
            "pass_log_cb": True,
            "on_log": self._on_deploy_log,
            "on_finished": self._on_deploy_finished,
            "on_error": self._on_deploy_error,
        }
        if pause_repos:
            deploy_kwargs["pause_repos"] = tuple(pause_repos)
        self._workers.start_task(
            _deploy_github_task,
            token,
            raw_repo_arg,
            bool(self._state.github_private),
            self._state.github_source_repo,
            cron_expr,
            desired_secrets,
            **deploy_kwargs,
        )

    def _on_deploy_log(self, msg: str) -> None:
        self.deployStatusLabel.setText(msg)
        self.log_emitted.emit(msg)

    def _on_deploy_finished(self, res: dict[str, Any]) -> None:
        full_name = res.get("full_name", "")
        if res.get("login"):
            self._state.github_login = res["login"]
        if not res.get("sync_only"):
            self._state.last_deployed_version = APP_VERSION
        self._last_deploy_text = f"{full_name} ({len(res.get('written', []))} secrets synced)"
        self._refresh_github_badge()
        self.refresh_preflight()
        self.deployStatusLabel.setText(self._last_deploy_text)
        self.state_changed.emit()
        InfoBar.success(
            tr("common.success"),
            f"{full_name} — {len(res.get('written', []))} secrets",
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )
        self._bot_scan_done_for_token = ""
        self.maybe_auto_scan_bot_repos()

    def _on_deploy_error(self, err: str) -> None:
        self.refresh_preflight()
        self.deployStatusLabel.setText(err)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=6500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("automation.title"))
        self.subtitleLabel.setText(tr("automation.subtitle"))
        self.changeTzBtn.setText(escape_qt_mnemonic(tr("automation.tz_change")))
        self.modeSegment.items["local"].setText(tr("schedule.mode_local"))
        self.modeSegment.items["cloud"].setText(tr("schedule.mode_cloud"))
        self.modeSegment.items["both"].setText(tr("schedule.mode_both"))
        self._sync_mode_visibility(self._state.mode if self._state.mode in ("local", "cloud", "both") else "cloud")
        self.localSection.setTitle(tr("automation.local_card_title"))
        self.localTimesChips.set_empty_text(tr("schedule.times_empty"))
        self.localTimesChips.set_remove_tooltip(tr("common.remove"))
        self.addTimeBtn.setText(escape_qt_mnemonic(tr("schedule.add_time")))
        win_sync_txt = escape_qt_mnemonic(tr("schedule.sync_windows"))
        self._applyWinPrimaryBtn.setText(win_sync_txt)
        self._applyWinSecondaryBtn.setText(win_sync_txt)
        self.removeWinScheduleBtn.setText(escape_qt_mnemonic(tr("schedule.remove_windows")))
        self.showBrowserLabel.setText(tr("run.show_browser"))
        self.matchesRunBadge.setState("success", tr("automation.matches_run_logs"))
        self.dailyCapLabel.setText(tr("content.daily_cap_label"))
        self.cloudRepoCard.setTitle(tr("automation.repo_card_title"))
        self.ghSigninBtn.setText(escape_qt_mnemonic(tr("cloud.signin_device")))
        self.ghSwitchBtn.setText(escape_qt_mnemonic(tr("cloud.reconnect_github")))
        self.repoNameLabel.setText(tr("cloud.repo_name"))
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.privateRepoLabel.setText(tr("cloud.private_repo"))
        self.botReposHeading.setText(tr("cloud.bot_repos_title"))
        self.scanBotReposBtn.setToolTip(tr("cloud.scan_bot_repos_tooltip"))
        self.cloudTimesChips.set_empty_text(tr("schedule.times_empty"))
        self.cloudTimesChips.set_remove_tooltip(tr("common.remove"))
        self.addCloudTimeBtn.setText(escape_qt_mnemonic(tr("schedule.add_time")))
        self.quickPresetsLabel.setText(tr("schedule.quick_presets"))
        self.preset1Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_1")))
        self.preset2Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_2")))
        self.preset3Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_3")))
        self.cloudDelayNote.setText(tr("schedule.cloud_delay_note"))
        self.advancedToggleBtn.setText(escape_qt_mnemonic(tr("automation.advanced_toggle")))
        self.advancedOptionalTag.setText(tr("common.optional"))
        self.syncOnlyCheck.setText(escape_qt_mnemonic(tr("cloud.sync_only")))
        self.patLabel.setText(tr("cloud.signin_pat"))
        self.patEdit.setPlaceholderText(tr("schedule.pat_placeholder"))
        self.savePatBtn.setText(escape_qt_mnemonic(tr("schedule.save_pat")))
        self.cronLabel.setText(tr("schedule.custom_cron"))
        self.preflightHeading.setText(tr("automation.preflight_heading"))
        self.deployBtn.setText(escape_qt_mnemonic(tr("schedule.deploy_btn")))
        self.sync_timezone_display()
        self._update_windows_tasks_badge()
        self._update_repo_badge()
        self._refresh_github_badge()
        self._render_bot_repos_panel()
        self.refresh_preflight()


__all__ = ["AutomationPage"]
