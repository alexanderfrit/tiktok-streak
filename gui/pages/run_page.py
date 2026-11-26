"""Run & Logs page: equal-height Local/Cloud command cards and Master-Detail runs + live logs."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FlowLayout,
    FluentIcon as FIF,
    HeaderCardWidget,
    ImageLabel,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TitleLabel,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from gui.services.github_api import WORKFLOW_FILE, GitHub, GitHubError
from gui.services.i18n import tr
from gui.services.local_runner import LocalRunner
from gui.services.storage import AppState, ConfigStore, SecretStore
from gui.services.theme import INNER_RADIUS, PAGE_MARGINS, escape_qt_mnemonic, tabular_mono_font
from gui.services.validators import format_utc_timestamp, get_timezone_info
from gui.widgets.log_viewer import LogViewer
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController


def _run_local_task(
    state_snapshot: AppState,
    secret_store: SecretStore,
    *,
    only_account: str | None = None,
    only_friend: str | None = None,
    headless: bool | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    runner = LocalRunner()
    if only_friend:
        return runner.run_smoke_test(
            state_snapshot,
            secret_store,
            friend=only_friend,
            account_id=only_account,
            headless=bool(headless),
            stop_event=cancel_event,
            log_cb=log_cb,
            progress_cb=progress_cb,
        )
    return runner.run(
        state_snapshot,
        secret_store,
        only_account=only_account,
        headless=headless,
        stop_event=cancel_event,
        log_cb=log_cb,
        progress_cb=progress_cb,
    )


_ACTIVE_RUN_STATUSES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})


def _fetch_runs_task(token: str, full_name: str) -> dict[str, Any]:
    gh = GitHub(token)
    runs = gh.runs(full_name, WORKFLOW_FILE, limit=8)
    wf_state: str | None = None
    get_wf = getattr(gh, "get_workflow", None)
    if callable(get_wf):
        try:
            wf = get_wf(full_name, WORKFLOW_FILE)
            if isinstance(wf, dict) and wf.get("state"):
                wf_state = str(wf["state"]).strip().lower()
        except Exception:
            wf_state = None
    return {"full_name": full_name, "runs": runs, "workflow_state": wf_state}


def _toggle_cloud_schedule_task(token: str, full_name: str, pause: bool) -> dict[str, Any]:
    gh = GitHub(token)
    cancelled: list[int] = []
    if pause:
        res = gh.pause_workflow(full_name, WORKFLOW_FILE, cancel_running=True)
        cancelled = list(res.get("cancelled_run_ids") or [])
        new_state = "disabled_manually"
    else:
        gh.enable_workflow(full_name, WORKFLOW_FILE)
        new_state = "active"
    runs: list[dict[str, Any]] = []
    try:
        runs = gh.runs(full_name, WORKFLOW_FILE, limit=8)
    except Exception:
        runs = []
    return {
        "full_name": full_name,
        "paused": pause,
        "workflow_state": new_state,
        "cancelled_run_ids": cancelled,
        "runs": runs,
    }


def _cancel_run_task(token: str, full_name: str, run_id: int) -> dict[str, Any]:
    gh = GitHub(token)
    gh.cancel_run(full_name, int(run_id))
    runs: list[dict[str, Any]] = []
    try:
        runs = gh.runs(full_name, WORKFLOW_FILE, limit=8)
    except Exception:
        runs = []
    return {"full_name": full_name, "run_id": int(run_id), "runs": runs}


def _dispatch_run_task(token: str, full_name: str, log_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
    gh = GitHub(token)
    if log_cb:
        log_cb(f"Enabling and dispatching {WORKFLOW_FILE} on {full_name}...")
    try:
        gh.enable_workflow(full_name, WORKFLOW_FILE)
    except GitHubError:
        pass
    run = gh.dispatch_and_track(full_name, WORKFLOW_FILE, timeout=30.0)
    runs = gh.runs(full_name, WORKFLOW_FILE, limit=8)
    return {"run": run, "runs": runs, "workflow_state": "active", "full_name": full_name}


def _fetch_job_logs_task(token: str, full_name: str, run_id: int) -> str:
    gh = GitHub(token)
    jobs = gh.get_run_jobs(full_name, run_id)
    if not jobs:
        return f"No jobs found for run #{run_id}."
    chunks: list[str] = []
    for job in jobs:
        jname = job.get("name") or f"Job {job.get('id')}"
        jid = job.get("id")
        chunks.append(f"=== {jname} (id={jid}, conclusion={job.get('conclusion')}) ===")
        if jid:
            try:
                chunks.append(gh.get_job_logs(full_name, int(jid)))
            except GitHubError as exc:
                chunks.append(f"(Could not fetch logs: {exc})")
    return "\n".join(chunks)


def _download_artifacts_task(token: str, full_name: str, run_id: int, dest_dir: Path) -> list[str]:
    gh = GitHub(token)
    paths = gh.download_diagnostics_artifact(full_name, run_id, dest_dir)
    return [str(p) for p in paths]


def _human_run_result(run: dict[str, Any]) -> tuple[str, str]:
    """Translate raw GitHub Actions status/conclusion into calm human-readable label & level."""
    status = str(run.get("status") or "").strip().lower()
    conclusion = str(run.get("conclusion") or "").strip().lower()
    if status in ("in_progress",):
        return tr("run.result_running"), "info"
    if status in ("queued", "waiting", "pending", "requested"):
        return tr("run.result_queued"), "info"
    if conclusion == "success":
        return tr("run.result_passed"), "success"
    if conclusion in ("failure", "timed_out", "action_required"):
        return tr("run.result_failed"), "error"
    if conclusion in ("cancelled", "skipped"):
        return tr("run.result_cancelled"), "warning"
    return (conclusion or status or "-").capitalize(), "neutral"


def _human_run_trigger(event: str) -> str:
    ev = str(event or "").strip().lower()
    if ev == "schedule":
        return tr("run.trigger_scheduled")
    if ev == "workflow_dispatch":
        return tr("run.trigger_manual")
    return ev or "-"


class RunPage(SmoothScrollArea):
    """Execute local tests/runs, monitor GitHub Cloud runs, and inspect live logs side-by-side."""

    state_changed = Signal()
    cloud_schedule_toggled = Signal(str, str)

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
        self.setObjectName("run_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._smoke_test = smoke_test
        self._loading = False
        self._local_cancel: threading.Event | None = None
        self._runs: list[dict[str, Any]] = []
        self._cloud_workflow_state: str | None = None
        self._last_diag_dir: Path | None = None

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
        # Header
        hdr = QVBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(3)
        self.titleLabel = TitleLabel(tr("run.title"), self.view)
        self.subtitleLabel = BodyLabel(tr("run.subtitle"), self.view)
        self.subtitleLabel.setWordWrap(True)
        self.subtitleLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hdr.addWidget(self.titleLabel)
        hdr.addWidget(self.subtitleLabel)
        self.vBoxLayout.addLayout(hdr)

        # 1. Top Equal-Height Command Split (Local Run [stretch 3] | GitHub Cloud [stretch 2])
        top_split = QHBoxLayout()
        top_split.setContentsMargins(0, 0, 0, 0)
        top_split.setSpacing(14)

        # 1a. Local Run card
        self.localCard = HeaderCardWidget(self.view)
        self.localCard.setTitle(tr("run.local_title"))
        self.localCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.localCard.setMinimumWidth(0)
        self.localCard.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        local_body = QVBoxLayout()
        local_body.setContentsMargins(0, 0, 0, 0)
        local_body.setSpacing(10)

        cmd_row = FlowLayout(isTight=True)
        cmd_row.setContentsMargins(0, 0, 0, 0)
        cmd_row.setHorizontalSpacing(6)
        cmd_row.setVerticalSpacing(6)
        self.friendCombo = ComboBox(self.localCard)
        self.friendCombo.setMinimumWidth(120)
        self.friendCombo.setMaximumWidth(175)
        self.quickTestBtn = PrimaryPushButton(
            FIF.PLAY, escape_qt_mnemonic(tr("run.quick_test")), self.localCard
        )
        self.fullRunBtn = PushButton(
            FIF.PLAY_SOLID, escape_qt_mnemonic(tr("run.full_run")), self.localCard
        )
        self.stopBtn = PushButton(
            FIF.PAUSE, escape_qt_mnemonic(tr("run.stop_short")), self.localCard
        )
        self.stopBtn.setEnabled(False)
        self.quickTestBtn.clicked.connect(self._start_quick_test)
        self.fullRunBtn.clicked.connect(self._start_full_run)
        self.stopBtn.clicked.connect(self._stop_local_run)

        cmd_row.addWidget(self.friendCombo)
        cmd_row.addWidget(self.quickTestBtn)
        cmd_row.addWidget(self.fullRunBtn)
        cmd_row.addWidget(self.stopBtn)
        local_body.addLayout(cmd_row)

        # Cleanly decoupled Show browser window bar (2-row layout so hint reads full-width)
        self.browserOptBar = QFrame(self.localCard)
        self.browserOptBar.setObjectName("RunBrowserOptBar")
        opt_lay = QVBoxLayout(self.browserOptBar)
        opt_lay.setContentsMargins(10, 8, 10, 8)
        opt_lay.setSpacing(4)
        opt_top = QHBoxLayout()
        opt_top.setContentsMargins(0, 0, 0, 0)
        opt_top.setSpacing(8)
        self.showBrowserLabel = StrongBodyLabel(tr("run.show_browser"), self.browserOptBar)
        self.showBrowserSwitch = SwitchButton(self.browserOptBar)
        self.showBrowserSwitch.checkedChanged.connect(self._on_show_browser_changed)
        opt_top.addWidget(self.showBrowserLabel, 1, Qt.AlignVCenter)
        opt_top.addWidget(self.showBrowserSwitch, 0, Qt.AlignVCenter)
        self.showBrowserHintLabel = CaptionLabel(tr("run.show_browser_hint"), self.browserOptBar)
        self.showBrowserHintLabel.setWordWrap(True)
        self.showBrowserHintLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        opt_lay.addLayout(opt_top)
        opt_lay.addWidget(self.showBrowserHintLabel)
        local_body.addWidget(self.browserOptBar)

        self.localProgress = IndeterminateProgressBar(self.localCard)
        self.localProgress.stop()
        self.localProgress.hide()
        self.localStatusLabel = CaptionLabel("", self.localCard)
        self.localStatusLabel.setWordWrap(True)
        self.localStatusLabel.hide()
        local_body.addWidget(self.localProgress)
        local_body.addWidget(self.localStatusLabel)

        self.localCard.viewLayout.addLayout(local_body)

        # 1b. GitHub Cloud card (matches exact height of Local Run card)
        self.cloudCard = HeaderCardWidget(self.view)
        self.cloudCard.setTitle(tr("run.cloud_title"))
        self.cloudCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.cloudCard.setMinimumWidth(0)
        self.cloudCard.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cloudWorkflowBadge = StatusPill("", "neutral", self.cloudCard)
        self.cloudWorkflowBadge.hide()
        self.cloudCard.headerLayout.addStretch(1)
        self.cloudCard.headerLayout.addWidget(self.cloudWorkflowBadge, 0, Qt.AlignVCenter)

        cloud_body = QVBoxLayout()
        cloud_body.setContentsMargins(0, 0, 0, 0)
        cloud_body.setSpacing(10)

        self.cloudInfoBox = QFrame(self.cloudCard)
        self.cloudInfoBox.setObjectName("RunCloudInfoBox")
        ci_lay = QVBoxLayout(self.cloudInfoBox)
        ci_lay.setContentsMargins(12, 10, 12, 10)
        ci_lay.setSpacing(4)

        ci_top = QHBoxLayout()
        ci_top.setContentsMargins(0, 0, 0, 0)
        ci_top.setSpacing(8)
        self.cloudRepoIcon = QLabel(self.cloudInfoBox)
        self.cloudRepoIcon.setFixedSize(16, 16)
        self.cloudRepoIcon.setAlignment(Qt.AlignCenter)
        self.repoTargetLabel = StrongBodyLabel("", self.cloudInfoBox)
        self.repoTargetLabel.setWordWrap(True)
        self.repoTargetLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ci_top.addWidget(self.cloudRepoIcon, 0, Qt.AlignVCenter)
        ci_top.addWidget(self.repoTargetLabel, 1, Qt.AlignVCenter)

        self.cloudScheduleLabel = BodyLabel("", self.cloudInfoBox)
        self.cloudScheduleLabel.setWordWrap(True)
        self.cloudScheduleLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        ci_lay.addLayout(ci_top)
        ci_lay.addWidget(self.cloudScheduleLabel)
        cloud_body.addWidget(self.cloudInfoBox)
        cloud_body.addStretch(1)

        cloud_btns = FlowLayout(isTight=True)
        cloud_btns.setContentsMargins(0, 0, 0, 0)
        cloud_btns.setHorizontalSpacing(6)
        cloud_btns.setVerticalSpacing(6)
        self.runCloudBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("run.run_cloud_btn")), self.cloudCard
        )
        self.toggleCloudScheduleBtn = PushButton(
            FIF.PAUSE, escape_qt_mnemonic(tr("cloud.pause_short")), self.cloudCard
        )
        self.refreshRunsBtn = TransparentToolButton(FIF.SYNC, self.cloudCard)
        self.refreshRunsBtn.setFixedSize(32, 32)
        self.refreshRunsBtn.setToolTip(tr("common.refresh"))
        self.runCloudBtn.clicked.connect(self._run_on_github_now)
        self.toggleCloudScheduleBtn.clicked.connect(self._toggle_cloud_schedule)
        self.refreshRunsBtn.clicked.connect(self.refresh_cloud_runs)
        cloud_btns.addWidget(self.runCloudBtn)
        cloud_btns.addWidget(self.toggleCloudScheduleBtn)
        cloud_btns.addWidget(self.refreshRunsBtn)
        cloud_body.addLayout(cloud_btns)

        self.cloudCard.viewLayout.addLayout(cloud_body)

        top_split.addWidget(self.localCard, 3)
        top_split.addWidget(self.cloudCard, 2)
        self.vBoxLayout.addLayout(top_split)

        # 2. Bottom Equal-Height Master-Detail Split (Recent Cloud Runs | Live Logs)
        bottom_split = QHBoxLayout()
        bottom_split.setContentsMargins(0, 0, 0, 0)
        bottom_split.setSpacing(14)

        # 2a. Master: Recent Cloud Runs card
        self.recentRunsCard = HeaderCardWidget(self.view)
        self.recentRunsCard.setTitle(tr("run.recent_runs_title"))
        self.recentRunsCard.headerView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.recentRunsCard.setMinimumWidth(0)
        self.recentRunsCard.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        runs_body = QVBoxLayout()
        runs_body.setContentsMargins(0, 0, 0, 0)
        runs_body.setSpacing(10)

        # Actionable Empty State
        self.runEmptyBox = QWidget(self.recentRunsCard)
        empty_lay = QVBoxLayout(self.runEmptyBox)
        empty_lay.setContentsMargins(12, 24, 12, 24)
        empty_lay.setSpacing(8)
        empty_lay.setAlignment(Qt.AlignCenter)
        self.runEmptyTitle = StrongBodyLabel(tr("run.no_runs"), self.runEmptyBox)
        self.runEmptyTitle.setAlignment(Qt.AlignCenter)
        self.runEmptyLabel = CaptionLabel(tr("run.empty_runs"), self.runEmptyBox)
        self.runEmptyLabel.setWordWrap(True)
        self.runEmptyLabel.setAlignment(Qt.AlignCenter)
        self.emptyRunCloudBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("run.run_cloud_btn")), self.runEmptyBox
        )
        self.emptyRunCloudBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.emptyRunCloudBtn.clicked.connect(self._run_on_github_now)
        empty_lay.addWidget(self.runEmptyTitle, 0, Qt.AlignHCenter)
        empty_lay.addWidget(self.runEmptyLabel, 0, Qt.AlignHCenter)
        empty_lay.addWidget(self.emptyRunCloudBtn, 0, Qt.AlignHCenter)
        runs_body.addWidget(self.runEmptyBox, 1)

        # Compact 3-column human-readable runs table
        self.runsTable = TableWidget(self.recentRunsCard)
        self.runsTable.setColumnCount(3)
        self.runsTable.setHorizontalHeaderLabels([
            tr("run.col_conclusion"),
            tr("run.col_event"),
            tr("run.col_created"),
        ])
        self.runsTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.runsTable.setSelectionMode(QAbstractItemView.SingleSelection)
        self.runsTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.runsTable.setWordWrap(False)
        self.runsTable.setTextElideMode(Qt.ElideRight)
        self.runsTable.verticalHeader().hide()
        header = self.runsTable.horizontalHeader()
        header.setMinimumSectionSize(56)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.runsTable.setMinimumHeight(180)
        self.runsTable.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.runsTable.itemSelectionChanged.connect(self._sync_selected_run_buttons)
        self.runsTable.itemDoubleClicked.connect(lambda _item: self._open_selected_run_url())
        runs_body.addWidget(self.runsTable, 1)

        # Clean action bar below runs table (wraps cleanly on narrow half-split widths)
        run_actions = FlowLayout(isTight=True)
        run_actions.setContentsMargins(0, 0, 0, 0)
        run_actions.setHorizontalSpacing(6)
        run_actions.setVerticalSpacing(6)
        self.viewLogsBtn = PushButton(
            FIF.DOCUMENT, escape_qt_mnemonic(tr("cloud.view_logs")), self.recentRunsCard
        )
        self.downloadDiagBtn = PushButton(
            FIF.CLOUD_DOWNLOAD, escape_qt_mnemonic(tr("cloud.download_diagnostics")), self.recentRunsCard
        )
        self.openRunBtn = TransparentPushButton(
            FIF.LINK, escape_qt_mnemonic(tr("run.open_run")), self.recentRunsCard
        )
        self.cancelCloudRunBtn = PushButton(
            FIF.CANCEL, escape_qt_mnemonic(tr("cloud.stop_run")), self.recentRunsCard
        )
        self.cancelCloudRunBtn.hide()
        self.viewLogsBtn.clicked.connect(self._view_selected_run_logs)
        self.downloadDiagBtn.clicked.connect(self._download_selected_diagnostics)
        self.openRunBtn.clicked.connect(self._open_selected_run_url)
        self.cancelCloudRunBtn.clicked.connect(self._cancel_selected_cloud_run)

        run_actions.addWidget(self.viewLogsBtn)
        run_actions.addWidget(self.downloadDiagBtn)
        run_actions.addWidget(self.openRunBtn)
        run_actions.addWidget(self.cancelCloudRunBtn)
        runs_body.addLayout(run_actions)

        # Stacked non-overflowing Failure Screenshot Preview container
        self.diagPreviewBox = QFrame(self.recentRunsCard)
        self.diagPreviewBox.setObjectName("RunDiagPreviewBox")
        dp_lay = QVBoxLayout(self.diagPreviewBox)
        dp_lay.setContentsMargins(10, 8, 10, 10)
        dp_lay.setSpacing(8)
        self.diagPreviewTitle = StrongBodyLabel(tr("run.diag_preview_title"), self.diagPreviewBox)
        dp_bar = QHBoxLayout()
        dp_bar.setContentsMargins(0, 0, 0, 0)
        dp_bar.setSpacing(8)
        self.diagCombo = ComboBox(self.diagPreviewBox)
        self.diagCombo.setMinimumWidth(0)
        self.diagCombo.currentIndexChanged.connect(self._on_diag_image_selected)
        self.openDiagFolderBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("run.open_folder")), self.diagPreviewBox
        )
        self.openDiagFolderBtn.clicked.connect(self._open_diag_folder)
        dp_bar.addWidget(self.diagCombo, 1)
        dp_bar.addWidget(self.openDiagFolderBtn, 0)

        self.diagImageLabel = ImageLabel(self.diagPreviewBox)
        self.diagImageLabel.setBorderRadius(8, 8, 8, 8)
        dp_lay.addWidget(self.diagPreviewTitle)
        dp_lay.addLayout(dp_bar)
        dp_lay.addWidget(self.diagImageLabel, 0, Qt.AlignHCenter)
        self.diagPreviewBox.hide()
        runs_body.addWidget(self.diagPreviewBox)

        self._sync_runs_empty_state()
        self.recentRunsCard.viewLayout.addLayout(runs_body)

        # 2b. Detail: Live Logs console (matches exact height of Recent Cloud Runs card)
        self.logViewer = LogViewer(
            parent=self.view,
            secrets_provider=self._collect_extra_secrets,
            summary_provider=self._build_diag_summary,
        )

        bottom_split.addWidget(self.recentRunsCard, 11)
        bottom_split.addWidget(self.logViewer, 10)
        self.vBoxLayout.addLayout(bottom_split, 1)

        self._apply_styles()
        try:
            qconfig.themeChanged.connect(self._apply_styles)
        except Exception:
            pass

    def _apply_styles(self, _theme=None) -> None:
        dark = isDarkTheme()
        bar_bg = "rgba(255, 255, 255, 0.04)" if dark else "#f4f6fa"
        bar_border = "rgba(255, 255, 255, 0.08)" if dark else "#e2e6ee"
        muted_fg = "#9a9a9a" if dark else "#616161"
        sched_fg = "#c8c8c8" if dark else "#4a5058"

        self.browserOptBar.setStyleSheet(
            f"QFrame#RunBrowserOptBar {{ background-color: {bar_bg}; border: 1px solid {bar_border}; "
            f"border-radius: {INNER_RADIUS}px; }}"
        )
        self.cloudInfoBox.setStyleSheet(
            f"QFrame#RunCloudInfoBox {{ background-color: {bar_bg}; border: 1px solid {bar_border}; "
            f"border-radius: {INNER_RADIUS}px; }}"
        )
        self.diagPreviewBox.setStyleSheet(
            f"QFrame#RunDiagPreviewBox {{ background-color: {bar_bg}; border: 1px solid {bar_border}; "
            f"border-radius: {INNER_RADIUS}px; }}"
        )
        fg_primary = "#ffffff" if dark else "#1a1a1a"
        self.showBrowserLabel.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        self.showBrowserHintLabel.setStyleSheet(f"color: {muted_fg}; background: transparent;")
        self.repoTargetLabel.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        self.cloudScheduleLabel.setStyleSheet(f"color: {sched_fg}; background: transparent;")
        self.diagPreviewTitle.setStyleSheet(f"color: {fg_primary}; background: transparent;")
        try:
            self.cloudRepoIcon.setPixmap(FIF.GITHUB.icon().pixmap(16, 16))
        except Exception:
            pass

    def _collect_extra_secrets(self) -> list[str]:
        return LocalRunner._collect_secrets(self._state, self._secrets)

    def _build_diag_summary(self) -> str:
        st = self._state
        repo_disp = self._full_repo_name() or st.github_repo_name
        return (
            f"Mode: {st.mode} | MultiAccount: {st.multi_account} | "
            f"Accounts: {len(st.accounts)} | Repo: {repo_disp}"
        )

    def append_log(self, line: str) -> None:
        self.logViewer.append_log(line)

    def start_smoke_test(self) -> None:
        self._start_quick_test()

    def start_full_run(self) -> None:
        self._start_full_run()

    def refresh_from_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        self._loading = True
        try:
            self.showBrowserSwitch.setChecked(not st.headless_local)
        finally:
            self._loading = False

        # Populate friend dropdown from active accounts
        prev_data = self.friendCombo.currentData()
        self.friendCombo.blockSignals(True)
        self.friendCombo.clear()
        active = list(st.accounts) if st.multi_account else ([st.accounts[0]] if st.accounts else [])
        for acc in active:
            for friend in acc.friends:
                label = f"@{friend} ({acc.name})" if st.multi_account else f"@{friend}"
                self.friendCombo.addItem(label, userData={"account_id": acc.id, "friend": friend})
        if prev_data and isinstance(prev_data, dict):
            for i in range(self.friendCombo.count()):
                if self.friendCombo.itemData(i) == prev_data:
                    self.friendCombo.setCurrentIndex(i)
                    break
        self.friendCombo.blockSignals(False)

        full_name = self._full_repo_name()
        has_tok = bool(self._secrets.get_github_token())
        if full_name and has_tok:
            self.runCloudBtn.setEnabled(True)
            self.emptyRunCloudBtn.setEnabled(True)
            self.refreshRunsBtn.setEnabled(True)
            self.toggleCloudScheduleBtn.setEnabled(True)
        else:
            self.runCloudBtn.setEnabled(False)
            self.emptyRunCloudBtn.setEnabled(False)
            self.refreshRunsBtn.setEnabled(False)
            self.toggleCloudScheduleBtn.setEnabled(False)
            self._cloud_workflow_state = None
        self._sync_cloud_info_labels()
        self._sync_cloud_workflow_controls()

        if self._runs:
            self._populate_runs_table(self._runs)

        self._sync_selected_run_buttons()

    def _sync_cloud_info_labels(self) -> None:
        st = self._state
        full_name = self._full_repo_name()
        has_tok = bool(self._secrets.get_github_token())
        tz_short = get_timezone_info(getattr(st, "timezone", None))["short"]
        times_str = ", ".join(list(getattr(st, "cloud_times", None) or [])[:3])
        sched_txt = (
            tr("run.cloud_schedule_line", times=times_str, tz=tz_short)
            if times_str
            else tr("run.cloud_schedule_none")
        )
        if full_name and has_tok:
            self.repoTargetLabel.setText(full_name)
            if self._runs:
                res_lbl, _lvl = _human_run_result(self._runs[0])
                sched_txt = f"{sched_txt} · {tr('run.cloud_last_run', result=res_lbl)}"
        else:
            self.repoTargetLabel.setText(tr("schedule.not_signed_in"))
        self.cloudScheduleLabel.setText(sched_txt)

    def _full_repo_name(self) -> str:
        owner = self._state.github_login.strip()
        repo = self._state.github_repo_name.strip()
        if not repo:
            return ""
        if "/" in repo:
            return repo
        return f"{owner}/{repo}" if owner else ""

    def _on_show_browser_changed(self, checked: bool) -> None:
        if self._loading:
            return
        new_headless = not bool(checked)
        if self._state.headless_local == new_headless:
            return
        self._state.headless_local = new_headless
        self.state_changed.emit()

    # -- Local Execution -------------------------------------------------
    def _set_local_running(self, running: bool, status_text: str = "") -> None:
        self.quickTestBtn.setEnabled(not running)
        self.fullRunBtn.setEnabled(not running)
        self.stopBtn.setEnabled(running)
        if running:
            self.localProgress.show()
            self.localProgress.start()
        else:
            self.localProgress.stop()
            self.localProgress.hide()
        if status_text:
            self.localStatusLabel.setText(status_text)
            self.localStatusLabel.show()
        elif not running:
            self.localStatusLabel.hide()

    def _start_quick_test(self) -> None:
        if self._workers.is_running("local_run"):
            return
        sel = self.friendCombo.currentData()
        if not isinstance(sel, dict) or not sel.get("friend"):
            InfoBar.warning(
                tr("common.warning"),
                tr("accounts.friends_count", count=0),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        snapshot = AppState.from_dict(self._state.to_dict())
        headless = not self.showBrowserSwitch.isChecked()
        mode_note = "headless" if headless else "visible browser"
        self._local_cancel = threading.Event()
        self._set_local_running(True, f"{tr('run.quick_test')}: @{sel['friend']}...")
        self.logViewer.append_log(f"--- Quick Test (@{sel['friend']}, {mode_note}) ---")

        self._workers.start_task(
            _run_local_task,
            snapshot,
            self._secrets,
            only_account=sel["account_id"],
            only_friend=sel["friend"],
            headless=headless,
            task_key="local_run",
            cancel_event=self._local_cancel,
            pass_cancel_event=True,
            pass_log_cb=True,
            pass_progress_cb=True,
            on_log=self.logViewer.append_log,
            on_progress=self._on_local_progress,
            on_finished=lambda res: self._on_local_finished(res, is_smoke=True),
            on_error=self._on_local_error,
        )

    def _start_full_run(self) -> None:
        if self._workers.is_running("local_run"):
            return
        snapshot = AppState.from_dict(self._state.to_dict())
        headless = not self.showBrowserSwitch.isChecked()
        self._local_cancel = threading.Event()
        self._set_local_running(True, f"{tr('run.full_run')}...")
        self.logViewer.append_log("--- Full Local Streak Run ---")

        self._workers.start_task(
            _run_local_task,
            snapshot,
            self._secrets,
            headless=headless,
            task_key="local_run",
            cancel_event=self._local_cancel,
            pass_cancel_event=True,
            pass_log_cb=True,
            pass_progress_cb=True,
            on_log=self.logViewer.append_log,
            on_progress=self._on_local_progress,
            on_finished=lambda res: self._on_local_finished(res, is_smoke=False),
            on_error=self._on_local_error,
        )

    def _stop_local_run(self) -> None:
        if self._local_cancel is not None:
            self._local_cancel.set()
            self.localStatusLabel.setText(tr("common.cancel") + "...")
            self.localStatusLabel.show()
            self.stopBtn.setEnabled(False)

    def _on_local_progress(self, payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("message"):
            self.localStatusLabel.setText(str(payload["message"]))
            self.localStatusLabel.show()

    def _on_local_finished(self, res: dict[str, Any], is_smoke: bool) -> None:
        ok = bool(res.get("ok"))
        elapsed = res.get("elapsed", 0)
        summary = f"{tr('common.success') if ok else tr('common.error')} ({elapsed}s)"
        if res.get("error"):
            summary = str(res["error"])
        self._set_local_running(False, summary)

        if is_smoke or ok:
            self._state.last_test_ok = ok
            self.state_changed.emit()

        if ok:
            InfoBar.success(
                tr("common.success"),
                summary,
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            InfoBar.warning(
                tr("common.warning"),
                summary,
                duration=5500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_local_error(self, err: str) -> None:
        self._set_local_running(False, err)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    # -- GitHub Cloud Monitor --------------------------------------------
    def _sync_runs_empty_state(self) -> None:
        has_rows = self.runsTable.rowCount() > 0
        self.runsTable.setVisible(has_rows)
        self.runEmptyBox.setVisible(not has_rows)

    def _populate_runs_table(self, runs: list[dict[str, Any]]) -> None:
        prev_row = self.runsTable.currentRow()
        self._runs = list(runs or [])
        self.runsTable.setRowCount(len(self._runs))
        mono_font = tabular_mono_font(10)
        tz_id = getattr(self._state, "timezone", "Asia/Jakarta")
        dark = isDarkTheme()

        color_map = {
            "success": QColor("#6ccb5f" if dark else "#0f7b0f"),
            "error": QColor("#ff99a4" if dark else "#b42318"),
            "warning": QColor("#fce100" if dark else "#9d5d00"),
            "info": QColor("#4cc2ff" if dark else "#005fb8"),
        }

        for row_idx, r in enumerate(self._runs):
            result_lbl, level = _human_run_result(r)
            trigger_lbl = _human_run_trigger(str(r.get("event") or ""))
            created_lbl = format_utc_timestamp(str(r.get("created_at") or ""), tz_id=tz_id)

            res_item = QTableWidgetItem(f"● {result_lbl}")
            if level in color_map:
                res_item.setForeground(color_map[level])
            trig_item = QTableWidgetItem(trigger_lbl)
            time_item = QTableWidgetItem(created_lbl)
            time_item.setFont(mono_font)

            self.runsTable.setItem(row_idx, 0, res_item)
            self.runsTable.setItem(row_idx, 1, trig_item)
            self.runsTable.setItem(row_idx, 2, time_item)

        if self._runs:
            target_row = prev_row if 0 <= prev_row < len(self._runs) else 0
            self.runsTable.selectRow(target_row)
        self._sync_runs_empty_state()
        self._sync_cloud_info_labels()
        self._sync_selected_run_buttons()

    def _selected_run(self) -> dict[str, Any] | None:
        row = self.runsTable.currentRow()
        if 0 <= row < len(self._runs):
            return self._runs[row]
        return None

    def _sync_selected_run_buttons(self) -> None:
        run = self._selected_run()
        has_tok = bool(self._secrets.get_github_token())
        has_sel = run is not None and has_tok
        self.openRunBtn.setEnabled(has_sel)
        self.viewLogsBtn.setEnabled(has_sel)
        self.downloadDiagBtn.setEnabled(has_sel)
        run_status = str((run or {}).get("status") or "").strip().lower()
        is_active = has_sel and run_status in _ACTIVE_RUN_STATUSES
        self.cancelCloudRunBtn.setEnabled(is_active)
        self.cancelCloudRunBtn.setVisible(is_active)

    def _is_cloud_workflow_paused(self) -> bool:
        return bool(self._cloud_workflow_state and self._cloud_workflow_state != "active")

    def _sync_cloud_workflow_controls(self) -> None:
        if not self._cloud_workflow_state:
            self.cloudWorkflowBadge.hide()
            self.toggleCloudScheduleBtn.setText(escape_qt_mnemonic(tr("cloud.pause_short")))
            self.toggleCloudScheduleBtn.setIcon(FIF.PAUSE)
            return
        paused = self._is_cloud_workflow_paused()
        if paused:
            self.cloudWorkflowBadge.setState("warning", tr("cloud.repo_state_paused"))
            self.toggleCloudScheduleBtn.setText(escape_qt_mnemonic(tr("cloud.resume_short")))
            self.toggleCloudScheduleBtn.setIcon(FIF.PLAY)
        else:
            self.cloudWorkflowBadge.setState("success", tr("cloud.repo_state_active"))
            self.toggleCloudScheduleBtn.setText(escape_qt_mnemonic(tr("cloud.pause_short")))
            self.toggleCloudScheduleBtn.setIcon(FIF.PAUSE)
        self.cloudWorkflowBadge.show()
        self.cloudWorkflowBadge.adjustSize()

    def set_cloud_workflow_state(self, full_name: str, wf_state: str | None) -> None:
        cur_repo = self._full_repo_name().strip().lower()
        if cur_repo and full_name and cur_repo != full_name.strip().lower():
            return
        self._cloud_workflow_state = wf_state.strip().lower() if wf_state else None
        self._sync_cloud_workflow_controls()

    def refresh_cloud_runs(self) -> None:
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not token or not full_name:
            return
        self.refreshRunsBtn.setEnabled(False)
        self._workers.start_task(
            _fetch_runs_task,
            token,
            full_name,
            task_key="fetch_cloud_runs",
            on_finished=self._on_runs_fetched,
            on_error=self._on_cloud_error,
        )

    def _on_runs_fetched(self, payload: Any) -> None:
        self.refreshRunsBtn.setEnabled(True)
        if isinstance(payload, dict):
            runs = list(payload.get("runs") or [])
            if payload.get("workflow_state") is not None:
                self._cloud_workflow_state = str(payload["workflow_state"]).strip().lower()
                self._sync_cloud_workflow_controls()
        else:
            runs = list(payload or [])
        self._populate_runs_table(runs)

    def _run_on_github_now(self) -> None:
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not token or not full_name:
            return
        self.runCloudBtn.setEnabled(False)
        self.emptyRunCloudBtn.setEnabled(False)
        self._workers.start_task(
            _dispatch_run_task,
            token,
            full_name,
            task_key="dispatch_cloud_run",
            pass_log_cb=True,
            on_log=self.logViewer.append_log,
            on_finished=self._on_cloud_dispatched,
            on_error=self._on_cloud_error,
        )

    def _on_cloud_dispatched(self, payload: dict[str, Any]) -> None:
        self.runCloudBtn.setEnabled(True)
        self.emptyRunCloudBtn.setEnabled(True)
        if payload.get("workflow_state"):
            self._cloud_workflow_state = str(payload["workflow_state"]).strip().lower()
            self._sync_cloud_workflow_controls()
            if payload.get("full_name"):
                self.cloud_schedule_toggled.emit(str(payload["full_name"]), self._cloud_workflow_state)
        self._populate_runs_table(payload.get("runs") or [])
        run = payload.get("run") or {}
        InfoBar.success(
            tr("common.success"),
            f"Run #{run.get('id', '?')}: {run.get('status', 'queued')}",
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _toggle_cloud_schedule(self) -> None:
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not token or not full_name:
            return
        pause = not self._is_cloud_workflow_paused()
        self.toggleCloudScheduleBtn.setEnabled(False)
        self._workers.start_task(
            _toggle_cloud_schedule_task,
            token,
            full_name,
            pause,
            task_key="toggle_cloud_schedule",
            on_finished=self._on_cloud_schedule_toggled,
            on_error=self._on_cloud_error,
        )

    def _on_cloud_schedule_toggled(self, payload: dict[str, Any]) -> None:
        self.toggleCloudScheduleBtn.setEnabled(bool(self._secrets.get_github_token() and self._full_repo_name()))
        full_name = str(payload.get("full_name") or self._full_repo_name())
        wf_state = str(payload.get("workflow_state") or ("disabled_manually" if payload.get("paused") else "active"))
        self._cloud_workflow_state = wf_state
        self._sync_cloud_workflow_controls()
        if payload.get("runs") is not None:
            self._populate_runs_table(payload.get("runs") or [])
        self.cloud_schedule_toggled.emit(full_name, wf_state)
        msg_key = "cloud.schedule_paused_ok" if payload.get("paused") else "cloud.schedule_resumed_ok"
        InfoBar.success(
            tr("common.success"),
            tr(msg_key, repo=full_name),
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _cancel_selected_cloud_run(self) -> None:
        run = self._selected_run()
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not run or not token or not full_name:
            return
        rid = int(run["id"])
        self.cancelCloudRunBtn.setEnabled(False)
        self._workers.start_task(
            _cancel_run_task,
            token,
            full_name,
            rid,
            task_key="cancel_cloud_run",
            on_finished=self._on_cloud_run_cancelled,
            on_error=self._on_cloud_error,
        )

    def _on_cloud_run_cancelled(self, payload: dict[str, Any]) -> None:
        self._populate_runs_table(payload.get("runs") or [])
        rid = payload.get("run_id", "?")
        InfoBar.success(
            tr("common.success"),
            tr("cloud.run_cancelled_ok", run_id=rid),
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_cloud_error(self, err: str) -> None:
        ready = bool(self._secrets.get_github_token() and self._full_repo_name())
        self.refreshRunsBtn.setEnabled(ready)
        self.runCloudBtn.setEnabled(ready)
        self.emptyRunCloudBtn.setEnabled(ready)
        self.toggleCloudScheduleBtn.setEnabled(ready)
        self._sync_selected_run_buttons()
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _open_selected_run_url(self) -> None:
        run = self._selected_run()
        if run and run.get("html_url"):
            QDesktopServices.openUrl(QUrl(str(run["html_url"])))

    def _view_selected_run_logs(self) -> None:
        run = self._selected_run()
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not run or not token or not full_name:
            return
        rid = int(run["id"])
        self.viewLogsBtn.setEnabled(False)
        self._workers.start_task(
            _fetch_job_logs_task,
            token,
            full_name,
            rid,
            task_key="fetch_job_logs",
            on_finished=self._on_job_logs_fetched,
            on_error=self._on_job_logs_error,
        )

    def _on_job_logs_fetched(self, text: str) -> None:
        self._sync_selected_run_buttons()
        self.logViewer.set_logs(text)

    def _on_job_logs_error(self, err: str) -> None:
        self._sync_selected_run_buttons()
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _download_selected_diagnostics(self) -> None:
        run = self._selected_run()
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not run or not token or not full_name:
            return
        rid = int(run["id"])
        dest_dir = self._config_store.config_dir / "diagnostics" / str(rid)
        self._last_diag_dir = dest_dir
        self.downloadDiagBtn.setEnabled(False)
        self._workers.start_task(
            _download_artifacts_task,
            token,
            full_name,
            rid,
            dest_dir,
            task_key="download_diag",
            on_finished=self._on_diag_downloaded,
            on_error=self._on_diag_error,
        )

    def _on_diag_downloaded(self, files: list[str]) -> None:
        self._sync_selected_run_buttons()
        pngs = [f for f in files if f.lower().endswith(".png")]
        self.diagCombo.blockSignals(True)
        self.diagCombo.clear()
        for p in pngs:
            self.diagCombo.addItem(Path(p).name, userData=p)
        self.diagCombo.blockSignals(False)

        if pngs:
            self.diagPreviewBox.show()
            self._on_diag_image_selected(0)
        elif files:
            self.diagPreviewBox.show()
            self.diagImageLabel.setImage(None)
        else:
            self.diagPreviewBox.hide()

        InfoBar.success(
            tr("common.success"),
            f"{len(files)} file(s)",
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_diag_error(self, err: str) -> None:
        self._sync_selected_run_buttons()
        InfoBar.warning(
            tr("common.warning"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_diag_image_selected(self, _idx: int) -> None:
        path = self.diagCombo.currentData()
        if path and Path(str(path)).is_file():
            self.diagImageLabel.setImage(str(path))
            self.diagImageLabel.scaledToWidth(320)
        else:
            self.diagImageLabel.setImage(None)

    def _open_diag_folder(self) -> None:
        if self._last_diag_dir and self._last_diag_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_diag_dir)))

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("run.title"))
        self.subtitleLabel.setText(tr("run.subtitle"))
        self.localCard.setTitle(tr("run.local_title"))
        self.showBrowserLabel.setText(tr("run.show_browser"))
        self.showBrowserHintLabel.setText(tr("run.show_browser_hint"))
        self.quickTestBtn.setText(escape_qt_mnemonic(tr("run.quick_test")))
        self.fullRunBtn.setText(escape_qt_mnemonic(tr("run.full_run")))
        self.stopBtn.setText(escape_qt_mnemonic(tr("run.stop_short")))
        self.cloudCard.setTitle(tr("run.cloud_title"))
        self.runCloudBtn.setText(escape_qt_mnemonic(tr("run.run_cloud_btn")))
        self.emptyRunCloudBtn.setText(escape_qt_mnemonic(tr("run.run_cloud_btn")))
        self.refreshRunsBtn.setToolTip(tr("common.refresh"))
        self.recentRunsCard.setTitle(tr("run.recent_runs_title"))
        self.runEmptyTitle.setText(tr("run.no_runs"))
        self.runEmptyLabel.setText(tr("run.empty_runs"))
        self.runsTable.setHorizontalHeaderLabels([
            tr("run.col_conclusion"),
            tr("run.col_event"),
            tr("run.col_created"),
        ])
        self.openRunBtn.setText(escape_qt_mnemonic(tr("run.open_run")))
        self.viewLogsBtn.setText(escape_qt_mnemonic(tr("cloud.view_logs")))
        self.downloadDiagBtn.setText(escape_qt_mnemonic(tr("cloud.download_diagnostics")))
        self.cancelCloudRunBtn.setText(escape_qt_mnemonic(tr("cloud.stop_run")))
        self.diagPreviewTitle.setText(tr("run.diag_preview_title"))
        self.openDiagFolderBtn.setText(escape_qt_mnemonic(tr("run.open_folder")))
        self._sync_cloud_workflow_controls()
        self.logViewer.retranslate()
        self.refresh_from_state()


__all__ = ["RunPage"]
