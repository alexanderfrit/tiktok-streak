"""Run & Logs page: local smoke test, full local run, GitHub monitor, and log console."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    SwitchButton,
    TableWidget,
    TitleLabel,
)

from gui.services.github_api import WORKFLOW_FILE, GitHub, GitHubError
from gui.services.i18n import tr
from gui.services.local_runner import LocalRunner
from gui.services.storage import AppState, ConfigStore, SecretStore
from gui.services.theme import escape_qt_mnemonic, tabular_mono_font
try:
    from gui.services.validators import format_utc_timestamp
except ImportError:  # pragma: no cover
    from datetime import datetime, timezone, timedelta

    def format_utc_timestamp(iso_str: str, tz_id: str = "Asia/Jakarta") -> str:  # type: ignore[misc]
        raw = str(iso_str or "").strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_wib = dt.astimezone(timezone(timedelta(hours=7)))
            return f"{dt_wib.strftime('%Y-%m-%d %H:%M')} WIB"
        except Exception:
            return raw.replace("T", " ").replace("Z", " UTC")
from gui.widgets.log_viewer import LogViewer
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


def _fetch_runs_task(token: str, full_name: str) -> list[dict[str, Any]]:
    gh = GitHub(token)
    return gh.runs(full_name, WORKFLOW_FILE, limit=8)


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
    return {"run": run, "runs": runs}


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


class _FlowBox(QWidget):
    """Container widget exposing FlowLayout heightForWidth and sizeHint accurately."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        h_spacing: int = 8,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self.flow = FlowLayout(self, isTight=False)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(h_spacing)
        self.flow.setVerticalSpacing(v_spacing)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def addWidget(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.flow.heightForWidth(max(width, 1))

    def _effective_width(self) -> int:
        if self.width() > 100:
            return self.width()
        total = sum(
            item.sizeHint().width()
            for i in range(self.flow.count())
            if (item := self.flow.itemAt(i)) is not None
        )
        spacing = max(self.flow.count() - 1, 0) * self.flow.horizontalSpacing()
        return max(total + spacing, self.flow.minimumSize().width(), 1)

    def sizeHint(self) -> QSize:
        width = self._effective_width()
        return QSize(self.flow.minimumSize().width(), self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        if self.width() > 100:
            return QSize(self.flow.minimumSize().width(), self.heightForWidth(self.width()))
        return self.flow.minimumSize()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateGeometry()


class RunPage(SmoothScrollArea):
    """Execute local smoke tests / full runs, monitor GitHub runs, and inspect logs."""

    state_changed = Signal()

    def __init__(
        self,
        state: AppState,
        config_store: ConfigStore,
        secret_store: SecretStore,
        worker_controller: WorkerController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("run_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._loading = False
        self._local_cancel: threading.Event | None = None
        self._runs: list[dict[str, Any]] = []
        self._last_diag_dir: Path | None = None

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(28, 22, 28, 28)
        self.vBoxLayout.setSpacing(16)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.enableTransparentBackground()

        self._build_ui()
        self.refresh_from_state()

    def _build_ui(self) -> None:
        self.titleLabel = TitleLabel(tr("nav.run_logs"), self.view)
        self.vBoxLayout.addWidget(self.titleLabel)

        # 1. Local Test & Run card
        self.localCard = HeaderCardWidget(self.view)
        self.localCard.setTitle(tr("run.local_title"))
        local_body = QVBoxLayout()
        local_body.setContentsMargins(0, 0, 0, 0)
        local_body.setSpacing(10)

        friend_grid = QGridLayout()
        friend_grid.setContentsMargins(0, 0, 0, 0)
        friend_grid.setHorizontalSpacing(10)
        friend_grid.setVerticalSpacing(6)
        friend_grid.setColumnStretch(0, 1)
        self.friendPromptLabel = BodyLabel(tr("schedule.test_friend_prompt"), self.localCard)
        self.friendPromptLabel.setWordWrap(True)
        self.friendCombo = ComboBox(self.localCard)
        self.friendCombo.setMinimumWidth(0)
        self.showBrowserSwitch = SwitchButton(self.localCard)
        self.showBrowserSwitch.checkedChanged.connect(self._on_show_browser_changed)
        self.showBrowserLabel = BodyLabel(tr("run.show_browser"), self.localCard)
        browser_opt = QHBoxLayout()
        browser_opt.setSpacing(8)
        browser_opt.addWidget(self.showBrowserLabel)
        browser_opt.addWidget(self.showBrowserSwitch)
        friend_grid.addWidget(self.friendPromptLabel, 0, 0, 1, 2)
        friend_grid.addWidget(self.friendCombo, 1, 0)
        friend_grid.addLayout(browser_opt, 1, 1)

        btn_row = _FlowBox(self.localCard, h_spacing=10, v_spacing=6)
        self.quickTestBtn = PrimaryPushButton(
            FIF.PLAY, escape_qt_mnemonic(tr("run.quick_test")), btn_row
        )
        self.fullRunBtn = PushButton(
            FIF.PLAY_SOLID, escape_qt_mnemonic(tr("run.full_run")), btn_row
        )
        self.stopBtn = PushButton(
            FIF.PAUSE, escape_qt_mnemonic(tr("schedule.stop_run")), btn_row
        )
        self.stopBtn.setEnabled(False)

        self.quickTestBtn.clicked.connect(self._start_quick_test)
        self.fullRunBtn.clicked.connect(self._start_full_run)
        self.stopBtn.clicked.connect(self._stop_local_run)

        btn_row.addWidget(self.quickTestBtn)
        btn_row.addWidget(self.fullRunBtn)
        btn_row.addWidget(self.stopBtn)

        self.localProgress = IndeterminateProgressBar(self.localCard)
        self.localProgress.stop()
        self.localProgress.hide()
        self.localStatusLabel = CaptionLabel("", self.localCard)
        self.localStatusLabel.setWordWrap(True)

        local_body.addLayout(friend_grid)
        local_body.addWidget(btn_row)
        local_body.addWidget(self.localProgress)
        local_body.addWidget(self.localStatusLabel)
        self.localCard.viewLayout.addLayout(local_body)
        self.vBoxLayout.addWidget(self.localCard)

        # 2. Cloud Monitor card
        self.cloudCard = HeaderCardWidget(self.view)
        self.cloudCard.setTitle(tr("run.cloud_title"))
        cloud_body = QVBoxLayout()
        cloud_body.setContentsMargins(0, 0, 0, 0)
        cloud_body.setSpacing(10)

        cloud_top = QHBoxLayout()
        cloud_top.setSpacing(10)
        self.repoTargetLabel = BodyLabel("", self.cloudCard)
        self.repoTargetLabel.setWordWrap(True)
        self.runCloudBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("run.run_cloud_btn")), self.cloudCard
        )
        self.refreshRunsBtn = PushButton(
            FIF.SYNC, escape_qt_mnemonic(tr("common.refresh")), self.cloudCard
        )
        self.runCloudBtn.clicked.connect(self._run_on_github_now)
        self.refreshRunsBtn.clicked.connect(self.refresh_cloud_runs)
        cloud_top.addWidget(self.repoTargetLabel, 1)
        cloud_top.addWidget(self.runCloudBtn)
        cloud_top.addWidget(self.refreshRunsBtn)
        cloud_body.addLayout(cloud_top)

        self.cloudDelayHintLabel = CaptionLabel(tr("run.cloud_delay_hint"), self.cloudCard)
        self.cloudDelayHintLabel.setWordWrap(True)
        cloud_body.addWidget(self.cloudDelayHintLabel)

        self.runEmptyLabel = CaptionLabel(tr("run.empty_runs"), self.cloudCard)
        self.runEmptyLabel.setWordWrap(True)
        self.runEmptyLabel.setAlignment(Qt.AlignCenter)
        self.runEmptyLabel.setContentsMargins(12, 18, 12, 18)
        cloud_body.addWidget(self.runEmptyLabel)

        self.runsTable = TableWidget(self.cloudCard)
        self.runsTable.setColumnCount(4)
        self.runsTable.setHorizontalHeaderLabels([
            tr("run.col_status"),
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
        header.setMinimumSectionSize(90)
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.runsTable.setMinimumHeight(170)
        self.runsTable.setMaximumHeight(220)
        self.runsTable.itemSelectionChanged.connect(self._sync_selected_run_buttons)
        self.runsTable.itemDoubleClicked.connect(lambda _item: self._open_selected_run_url())
        cloud_body.addWidget(self.runsTable)

        self.runHintLabel = CaptionLabel(tr("run.select_run_hint"), self.cloudCard)
        self.runHintLabel.setWordWrap(True)
        cloud_body.addWidget(self.runHintLabel)

        run_actions = _FlowBox(self.cloudCard, h_spacing=8, v_spacing=6)
        self.openRunBtn = PushButton(
            FIF.GLOBE, escape_qt_mnemonic(tr("run.open_run")), run_actions
        )
        self.viewLogsBtn = PushButton(
            FIF.DOCUMENT, escape_qt_mnemonic(tr("cloud.view_logs")), run_actions
        )
        self.downloadDiagBtn = PushButton(
            FIF.CLOUD_DOWNLOAD, escape_qt_mnemonic(tr("cloud.download_diagnostics")), run_actions
        )
        self.openRunBtn.clicked.connect(self._open_selected_run_url)
        self.viewLogsBtn.clicked.connect(self._view_selected_run_logs)
        self.downloadDiagBtn.clicked.connect(self._download_selected_diagnostics)
        run_actions.addWidget(self.openRunBtn)
        run_actions.addWidget(self.viewLogsBtn)
        run_actions.addWidget(self.downloadDiagBtn)
        cloud_body.addWidget(run_actions)

        # Diagnostics screenshot preview container (hidden until downloaded)
        self.diagPreviewBox = QWidget(self.cloudCard)
        dp_lay = QVBoxLayout(self.diagPreviewBox)
        dp_lay.setContentsMargins(0, 6, 0, 0)
        dp_lay.setSpacing(8)
        dp_bar = QHBoxLayout()
        self.diagCombo = ComboBox(self.diagPreviewBox)
        self.diagCombo.currentIndexChanged.connect(self._on_diag_image_selected)
        self.openDiagFolderBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("run.open_folder")), self.diagPreviewBox
        )
        self.openDiagFolderBtn.clicked.connect(self._open_diag_folder)
        dp_bar.addWidget(self.diagCombo, 1)
        dp_bar.addWidget(self.openDiagFolderBtn)

        self.diagImageLabel = ImageLabel(self.diagPreviewBox)
        self.diagImageLabel.setBorderRadius(8, 8, 8, 8)
        dp_lay.addLayout(dp_bar)
        dp_lay.addWidget(self.diagImageLabel, 0, Qt.AlignLeft)
        self.diagPreviewBox.hide()
        cloud_body.addWidget(self.diagPreviewBox)

        self._sync_runs_empty_state()

        self.cloudCard.viewLayout.addLayout(cloud_body)
        self.vBoxLayout.addWidget(self.cloudCard)

        # 3. Bottom LogViewer
        self.logViewer = LogViewer(
            parent=self.view,
            secrets_provider=self._collect_extra_secrets,
            summary_provider=self._build_diag_summary,
        )
        self.vBoxLayout.addWidget(self.logViewer)

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
            self.repoTargetLabel.setText(f"GitHub: {full_name}")
            self.runCloudBtn.setEnabled(True)
            self.refreshRunsBtn.setEnabled(True)
        else:
            self.repoTargetLabel.setText(tr("schedule.not_signed_in"))
            self.runCloudBtn.setEnabled(False)
            self.refreshRunsBtn.setEnabled(False)

        if self._runs:
            tz_id = getattr(self._state, "timezone", "Asia/Jakarta")
            for row_idx, r in enumerate(self._runs):
                if row_idx < self.runsTable.rowCount():
                    item = self.runsTable.item(row_idx, 3)
                    if item is not None:
                        item.setText(format_utc_timestamp(str(r.get("created_at") or ""), tz_id=tz_id))

        self._sync_selected_run_buttons()

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
            self.stopBtn.setEnabled(False)

    def _on_local_progress(self, payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("message"):
            self.localStatusLabel.setText(str(payload["message"]))

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
        self.runEmptyLabel.setVisible(not has_rows)
        self.runHintLabel.setVisible(has_rows)

    def _populate_runs_table(self, runs: list[dict[str, Any]]) -> None:
        self._runs = list(runs or [])
        self.runsTable.setRowCount(len(self._runs))
        mono_font = tabular_mono_font(10)
        tz_id = getattr(self._state, "timezone", "Asia/Jakarta")
        for row_idx, r in enumerate(self._runs):
            vals = [
                str(r.get("status") or ""),
                str(r.get("conclusion") or "-"),
                str(r.get("event") or ""),
                format_utc_timestamp(str(r.get("created_at") or ""), tz_id=tz_id),
            ]
            for col_idx, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col_idx == 3:
                    item.setFont(mono_font)
                self.runsTable.setItem(row_idx, col_idx, item)
        if self._runs:
            self.runsTable.selectRow(0)
        self._sync_runs_empty_state()
        self._sync_selected_run_buttons()

    def _selected_run(self) -> dict[str, Any] | None:
        row = self.runsTable.currentRow()
        if 0 <= row < len(self._runs):
            return self._runs[row]
        return None

    def _sync_selected_run_buttons(self) -> None:
        has_sel = self._selected_run() is not None and bool(self._secrets.get_github_token())
        self.openRunBtn.setEnabled(has_sel)
        self.viewLogsBtn.setEnabled(has_sel)
        self.downloadDiagBtn.setEnabled(has_sel)

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

    def _on_runs_fetched(self, runs: list[dict[str, Any]]) -> None:
        self.refreshRunsBtn.setEnabled(True)
        self._populate_runs_table(runs)

    def _run_on_github_now(self) -> None:
        token = self._secrets.get_github_token().strip()
        full_name = self._full_repo_name()
        if not token or not full_name:
            return
        self.runCloudBtn.setEnabled(False)
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
        self._populate_runs_table(payload.get("runs") or [])
        run = payload.get("run") or {}
        InfoBar.success(
            tr("common.success"),
            f"Run #{run.get('id', '?')}: {run.get('status', 'queued')}",
            duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_cloud_error(self, err: str) -> None:
        self.refreshRunsBtn.setEnabled(bool(self._secrets.get_github_token() and self._full_repo_name()))
        self.runCloudBtn.setEnabled(bool(self._secrets.get_github_token() and self._full_repo_name()))
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
            self.diagImageLabel.scaledToHeight(220)
        else:
            self.diagImageLabel.setImage(None)

    def _open_diag_folder(self) -> None:
        if self._last_diag_dir and self._last_diag_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_diag_dir)))

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("nav.run_logs"))
        self.localCard.setTitle(tr("run.local_title"))
        self.friendPromptLabel.setText(tr("schedule.test_friend_prompt"))
        self.showBrowserLabel.setText(tr("run.show_browser"))
        self.quickTestBtn.setText(escape_qt_mnemonic(tr("run.quick_test")))
        self.fullRunBtn.setText(escape_qt_mnemonic(tr("run.full_run")))
        self.stopBtn.setText(escape_qt_mnemonic(tr("schedule.stop_run")))
        self.cloudCard.setTitle(tr("run.cloud_title"))
        self.cloudDelayHintLabel.setText(tr("run.cloud_delay_hint"))
        self.runCloudBtn.setText(escape_qt_mnemonic(tr("run.run_cloud_btn")))
        self.refreshRunsBtn.setText(escape_qt_mnemonic(tr("common.refresh")))
        self.runEmptyLabel.setText(tr("run.empty_runs"))
        self.runHintLabel.setText(tr("run.select_run_hint"))
        self.runsTable.setHorizontalHeaderLabels([
            tr("run.col_status"),
            tr("run.col_conclusion"),
            tr("run.col_event"),
            tr("run.col_created"),
        ])
        self.openRunBtn.setText(escape_qt_mnemonic(tr("run.open_run")))
        self.viewLogsBtn.setText(escape_qt_mnemonic(tr("cloud.view_logs")))
        self.downloadDiagBtn.setText(escape_qt_mnemonic(tr("cloud.download_diagnostics")))
        self.openDiagFolderBtn.setText(escape_qt_mnemonic(tr("run.open_folder")))
        self.logViewer.retranslate()
        self.refresh_from_state()


__all__ = ["RunPage"]
