"""Content, media, execution mode, Windows Task Scheduler, and GitHub Cloud deploy page."""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FlowLayout,
    FluentIcon as FIF,
    HeaderCardWidget,
    ImageLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    PasswordLineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TimePicker,
    TitleLabel,
    TransparentPushButton,
)

import gui.services.github_api as github_api
from gui.services.github_api import (
    WORKFLOW_FILE,
    GitHub,
    GitHubError,
    SECRET_NAMES,
    device_flow_start,
    has_required_scopes,
    resolve_source_dir,
)
from gui.services.i18n import get_language, tr
from gui.services.local_runner import _resolve_asset_path
from gui.services.scheduler import WindowsScheduler
from gui.services.storage import (
    AppState,
    ConfigStore,
    DEFAULT_PHOTO_PATH,
    DEFAULT_VIDEO_URL,
    SecretStore,
)
from gui.services.theme import escape_qt_mnemonic
from gui.services.validators import (
    cron_to_wib_summary,
    prepare_photo_secret,
    validate_repo_name,
    validate_video_url,
)
from gui.widgets.device_login_dialog import DeviceLoginDialog
from gui.widgets.fluent_spin_box import FluentSpinBox
from gui.widgets.status_pill import StatusPill
from gui.workers import WorkerController

_CRON_PRESETS: tuple[tuple[str, str], ...] = (
    ("0 1 * * *", "schedule.preset_1"),
    ("0 1,13 * * *", "schedule.preset_2"),
    ("0 1,7,13 * * *", "schedule.preset_3"),
    ("custom", "schedule.preset_custom"),
)


def _parse_repo_input(raw: str, default_owner: str = "") -> dict[str, Any]:
    """Validate and split ``repo`` or ``owner/repo`` into structured parts."""
    cleaned = str(raw or "").strip().strip("/")
    if not cleaned:
        res = validate_repo_name("")
        res["owner"] = default_owner.strip()
        res["full_name"] = ""
        return res

    resolver = getattr(github_api, "resolve_repo_parts", None)
    if "/" in cleaned:
        if callable(resolver):
            try:
                owner_part, repo_part = resolver(cleaned, default_owner)
            except Exception as exc:
                return {
                    "valid": False,
                    "ok": False,
                    "owner": "",
                    "name": cleaned,
                    "full_name": "",
                    "error": str(exc),
                }
        else:
            parts = [p.strip() for p in cleaned.split("/")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return {
                    "valid": False,
                    "ok": False,
                    "owner": "",
                    "name": cleaned,
                    "full_name": "",
                    "error": "Use 'repo-name' or 'owner/repo-name'.",
                }
            owner_part, repo_part = parts[0], parts[1]

        owner_chk = validate_repo_name(owner_part)
        if not owner_chk["valid"]:
            return {
                "valid": False,
                "ok": False,
                "owner": owner_part,
                "name": repo_part,
                "full_name": "",
                "error": owner_chk.get("error"),
            }
        repo_chk = validate_repo_name(repo_part)
        if not repo_chk["valid"]:
            return {
                "valid": False,
                "ok": False,
                "owner": owner_part,
                "name": repo_part,
                "full_name": "",
                "error": repo_chk.get("error"),
            }
        return {
            "valid": True,
            "ok": True,
            "owner": owner_part,
            "name": repo_part,
            "full_name": f"{owner_part}/{repo_part}",
            "error": None,
        }

    repo_chk = validate_repo_name(cleaned)
    owner_part = default_owner.strip()
    repo_chk["owner"] = owner_part
    repo_chk["full_name"] = f"{owner_part}/{cleaned}" if owner_part and repo_chk["valid"] else cleaned
    return repo_chk


def _fetch_user_repos_task(token: str) -> list[dict[str, Any]]:
    gh = GitHub(token.strip())
    list_fn = getattr(gh, "list_user_repos", None)
    if callable(list_fn):
        return list(list_fn(limit=100) or [])
    data = gh._req(
        "GET",
        "/user/repos?sort=updated&affiliation=owner,collaborator,organization_member&per_page=100",
    )
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "full_name": str(item.get("full_name") or ""),
                "name": str(item.get("name") or ""),
                "owner": str(((item.get("owner") or {}).get("login")) or ""),
                "private": bool(item.get("private", False)),
                "default_branch": str(item.get("default_branch") or "main"),
                "html_url": str(item.get("html_url") or ""),
            }
        )
    return out


def _validate_pat_task(token: str) -> dict[str, Any]:
    gh = GitHub(token.strip())
    me = gh.whoami()
    return {"token": token.strip(), "user": me}


def _build_cloud_secrets(state: AppState, secret_store: SecretStore) -> dict[str, str]:
    is_multi = bool(state.multi_account)
    active = list(state.accounts) if is_multi else ([state.accounts[0]] if state.accounts else [])
    if not active:
        raise ValueError("Configure at least one TikTok account first.")

    eff_video_url = (state.video_url or DEFAULT_VIDEO_URL).strip() if state.video_enabled else ""
    eff_share_times = max(1, int(state.share_times)) if state.video_enabled else 0

    desired: dict[str, str] = {
        "MESSAGE": state.message or "🔥 Daily Streak",
        "STREAK_VIDEO_URL": eff_video_url if state.video_enabled else "none",
        "SHARE_TIMES": str(eff_share_times) if state.video_enabled else "0",
    }
    if not state.video_enabled:
        desired["DISABLE_VIDEO"] = "true"

    if not state.photo_enabled:
        desired["DISABLE_PHOTO"] = "true"
        desired["PHOTO_PATH"] = "none"
    elif state.photo_path.strip():
        resolved_photo = _resolve_asset_path(state.photo_path.strip())
        desired["PHOTO_DATA_URL"] = prepare_photo_secret(resolved_photo)

    if int(state.daily_cap) > 0:
        desired["DAILY_CAP"] = str(int(state.daily_cap))

    if state.telegram_enabled:
        tg_tok = secret_store.get_telegram_token().strip()
        tg_chat = state.telegram_chat_id.strip()
        if tg_tok and tg_chat:
            desired["TELEGRAM_BOT_TOKEN"] = tg_tok
            desired["TELEGRAM_CHAT_ID"] = tg_chat

    if is_multi:
        payload: list[dict[str, Any]] = []
        for acc in active:
            cookie = secret_store.get_account_cookie(acc.id).strip()
            if not cookie:
                raise ValueError(f"Account '{acc.name}' is missing a TikTok session cookie.")
            if not acc.friends:
                raise ValueError(f"Account '{acc.name}' has no target friends configured.")

            item: dict[str, Any] = {
                "name": acc.name or "Account",
                "session_id": cookie,
                "friends": list(acc.friends),
                "message": acc.message_override if acc.message_override is not None else (state.message or "🔥 Daily Streak"),
            }
            if acc.video_url_override is not None:
                ov_v = acc.video_url_override.strip()
                item["video_url"] = "none" if (not ov_v or ov_v.lower() in ("none", "off", "disabled")) else ov_v
            else:
                item["video_url"] = eff_video_url if state.video_enabled else "none"

            if acc.share_times_override is not None:
                item["share_times"] = max(0, int(acc.share_times_override))
            else:
                item["share_times"] = eff_share_times

            if acc.photo_path_override is not None:
                ov_p = acc.photo_path_override.strip()
                if not ov_p or ov_p.lower() in ("none", "off", "disabled"):
                    item["photo_path"] = "none"
                elif ov_p == DEFAULT_PHOTO_PATH:
                    item["photo_path"] = DEFAULT_PHOTO_PATH
            elif not state.photo_enabled:
                item["photo_path"] = "none"

            payload.append(item)
        desired["TIKTOK_ACCOUNTS_JSON"] = json.dumps(payload, ensure_ascii=False)
    else:
        primary = active[0]
        primary_cookie = secret_store.get_account_cookie(primary.id).strip()
        if not primary_cookie:
            raise ValueError(f"Account '{primary.name}' is missing a TikTok session cookie.")
        if not primary.friends:
            raise ValueError(f"Account '{primary.name}' has no target friends configured.")
        desired["TIKTOK_SESSION_ID"] = primary_cookie
        desired["FRIENDS_LIST"] = ",".join(primary.friends)

    return desired


def _deploy_github_task(
    token: str,
    repo_name: str,
    is_private: bool,
    source_repo: str,
    cron_expr: str,
    desired_secrets: dict[str, str],
    log_cb: Callable[[str], None] | None = None,
    sync_only: bool = False,
) -> dict[str, Any]:
    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    gh = GitHub(token)
    _log("Verifying GitHub token and scopes...")
    me = gh.whoami()
    if me.get("scopes_header_present") and not has_required_scopes(me.get("scopes") or []):
        raise GitHubError("Token is missing required 'repo' and 'workflow' scopes.", status_code=403)

    login = str(me.get("login") or "").strip()
    if not login:
        raise GitHubError("Could not determine GitHub username.", status_code=401)

    parsed = _parse_repo_input(repo_name, default_owner=login)
    if not parsed["valid"]:
        raise GitHubError(str(parsed.get("error") or "Invalid repository name."), status_code=422)
    owner_part = parsed["owner"] or login
    repo_part = parsed["name"]
    full_name = f"{owner_part}/{repo_part}"

    if is_private:
        create_kwargs: dict[str, Any] = {
            "owner": owner_part,
            "repo_name": repo_part,
            "source_dir": resolve_source_dir(),
            "cron_expr": cron_expr,
            "progress_cb": _log,
        }
        if sync_only:
            try:
                sig = inspect.signature(gh.create_private_repo_with_bot)
                if "push_files" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    create_kwargs["push_files"] = False
            except (TypeError, ValueError):
                pass
        repo_info = gh.create_private_repo_with_bot(**create_kwargs)
        html_url = repo_info.get("html_url", f"https://github.com/{full_name}")
    else:
        _log(f"Checking public repository {full_name}...")
        exists = False
        try:
            repo_info = gh.get_repo(full_name)
            exists = True
        except GitHubError as exc:
            if exc.status_code != 404:
                raise
            repo_info = {}

        if not exists:
            _log(f"Forking {source_repo} -> {full_name}...")
            fork_kwargs: dict[str, Any] = {"name": repo_part, "wait_ready": True}
            if owner_part.lower() != login.lower():
                fork_kwargs["org"] = owner_part
            repo_info = gh.fork(source_repo, **fork_kwargs)
        elif not sync_only:
            _log(f"Syncing upstream changes into {full_name}...")
            try:
                gh.merge_upstream(full_name)
            except GitHubError as exc:
                _log(f"Upstream sync note: {exc}")
        else:
            _log(f"Keeping existing repository code for {full_name} (sync-only mode)...")

        _log("Enabling GitHub Actions and workflow...")
        gh.enable_actions(full_name)
        gh.enable_workflow(full_name, WORKFLOW_FILE)

        try:
            current_cron = gh.get_workflow_cron(full_name, WORKFLOW_FILE)
        except GitHubError:
            current_cron = ""
        if current_cron != cron_expr:
            _log(f"Updating workflow schedule to '{cron_expr}'...")
            gh.update_workflow_cron(full_name, cron_expr, WORKFLOW_FILE)
        html_url = repo_info.get("html_url", f"https://github.com/{full_name}")

    _log(f"Syncing {len(desired_secrets)} GitHub secrets (removing stale managed secrets)...")
    sync_res = gh.sync_secrets(full_name, desired_secrets, managed_names=SECRET_NAMES)
    _log(
        f"Deploy complete: {full_name} "
        f"(written={len(sync_res['written'])}, deleted={len(sync_res['deleted'])})"
    )
    return {
        "ok": True,
        "login": login,
        "full_name": full_name,
        "html_url": html_url,
        "written": sync_res["written"],
        "deleted": sync_res["deleted"],
    }


class ContentPage(SmoothScrollArea):
    """Configure message, video/photo cards, local scheduler, and GitHub deployment."""

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
        self.setObjectName("content_interface")
        self._state = state
        self._config_store = config_store
        self._secrets = secret_store
        self._workers = worker_controller
        self._loading = False
        self._smoke_test = smoke_test
        self._windows_tasks_count: int | None = None
        self._fetched_repos: dict[str, dict[str, Any]] = {}
        self._pat_expanded = False

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(250)
        self._save_debounce.timeout.connect(self._emit_state_changed)

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
        if not smoke_test and sys.platform == "win32":
            self._refresh_windows_tasks()

    def _build_ui(self) -> None:
        self.titleLabel = TitleLabel(tr("content.title"), self.view)
        self.vBoxLayout.addWidget(self.titleLabel)

        # 1. Streak Message card
        self.msgCard = HeaderCardWidget(self.view)
        self.msgCard.setTitle(tr("content.message_label"))
        self.msgEdit = PlainTextEdit(self.msgCard)
        self.msgEdit.setFixedHeight(78)
        self.msgEdit.textChanged.connect(self._on_message_changed)
        self.msgCard.viewLayout.addWidget(self.msgEdit)
        self.vBoxLayout.addWidget(self.msgCard)

        # 2. Video Share card
        self.videoCard = HeaderCardWidget(self.view)
        self.videoCard.setTitle(tr("content.video_toggle"))
        self.videoCard.headerLabel.setWordWrap(True)
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
        self.videoUrlLabel = BodyLabel(tr("content.video_url_label"), self.videoCard)
        self.videoUrlLabel.setWordWrap(True)
        self.videoUrlEdit = LineEdit(self.videoCard)
        self.videoUrlEdit.setMinimumWidth(0)
        self.videoUrlEdit.setPlaceholderText(DEFAULT_VIDEO_URL)
        self.videoUrlEdit.textChanged.connect(self._on_video_url_changed)
        self.resolveShortBtn = PushButton(
            FIF.LINK, escape_qt_mnemonic(tr("content.video_resolve")), self.videoCard
        )
        self.resolveShortBtn.clicked.connect(self._resolve_short_link)
        self.videoHintCaption = CaptionLabel(tr("content.video_hint"), self.videoCard)
        self.videoHintCaption.setWordWrap(True)
        vid_url_grid.addWidget(self.videoUrlLabel, 0, 0, 1, 2)
        vid_url_grid.addWidget(self.videoUrlEdit, 1, 0)
        vid_url_grid.addWidget(self.resolveShortBtn, 1, 1)
        vid_url_grid.addWidget(self.videoHintCaption, 2, 0, 1, 2)

        vid_count_row = QHBoxLayout()
        vid_count_row.setSpacing(8)
        self.shareTimesLabel = BodyLabel(tr("content.share_times_label"), self.videoCard)
        self.shareTimesSpin = FluentSpinBox(self.videoCard)
        self.shareTimesSpin.setRange(1, 10)
        self.shareTimesSpin.valueChanged.connect(self._on_share_times_changed)
        self.videoHintLabel = CaptionLabel("", self.videoCard)
        self.videoHintLabel.setWordWrap(True)
        vid_count_row.addWidget(self.shareTimesLabel)
        vid_count_row.addWidget(self.shareTimesSpin)
        vid_count_row.addSpacing(12)
        vid_count_row.addWidget(self.videoHintLabel, 1)

        vid_body.addLayout(vid_url_grid)
        vid_body.addLayout(vid_count_row)
        self.videoCard.viewLayout.addLayout(vid_body)
        self.vBoxLayout.addWidget(self.videoCard)

        # 3. Photo card
        self.photoCard = HeaderCardWidget(self.view)
        self.photoCard.setTitle(tr("content.photo_toggle"))
        self.photoSwitch = SwitchButton(self.photoCard)
        self.photoSwitch.checkedChanged.connect(self._on_photo_toggled)
        self.photoCard.headerLayout.addStretch(1)
        self.photoCard.headerLayout.addWidget(self.photoSwitch, 0, Qt.AlignVCenter)

        photo_row = QHBoxLayout()
        photo_row.setSpacing(14)
        self.photoPreview = ImageLabel(self.photoCard)
        self.photoPreview.setBorderRadius(8, 8, 8, 8)
        self.photoPreview.setFixedSize(110, 110)

        photo_right = QVBoxLayout()
        photo_right.setSpacing(8)
        self.photoStatusLabel = BodyLabel("", self.photoCard)
        self.photoStatusLabel.setWordWrap(True)
        photo_btns = FlowLayout(isTight=True)
        photo_btns.setContentsMargins(0, 0, 0, 0)
        photo_btns.setHorizontalSpacing(8)
        photo_btns.setVerticalSpacing(6)
        self.choosePhotoBtn = PushButton(
            FIF.PHOTO, escape_qt_mnemonic(tr("content.photo_select")), self.photoCard
        )
        self.defaultPhotoBtn = PushButton(
            FIF.ROTATE, escape_qt_mnemonic(tr("content.photo_use_default")), self.photoCard
        )
        self.resetPhotoBtn = PushButton(
            FIF.DELETE, escape_qt_mnemonic(tr("content.photo_reset")), self.photoCard
        )
        self.choosePhotoBtn.clicked.connect(self._choose_photo)
        self.defaultPhotoBtn.clicked.connect(self._use_default_photo)
        self.resetPhotoBtn.clicked.connect(self._clear_reset_photo)
        photo_btns.addWidget(self.choosePhotoBtn)
        photo_btns.addWidget(self.defaultPhotoBtn)
        photo_btns.addWidget(self.resetPhotoBtn)

        photo_right.addWidget(self.photoStatusLabel)
        photo_right.addLayout(photo_btns)
        photo_right.addStretch(1)

        photo_row.addWidget(self.photoPreview)
        photo_row.addLayout(photo_right, 1)
        self.photoCard.viewLayout.addLayout(photo_row)
        self.vBoxLayout.addWidget(self.photoCard)

        # 4. Automation Mode & Schedule card
        self.modeCard = HeaderCardWidget(self.view)
        self.modeCard.setTitle(tr("schedule.title"))
        mode_body = QVBoxLayout()
        mode_body.setContentsMargins(0, 0, 0, 0)
        mode_body.setSpacing(12)

        mode_seg_row = QHBoxLayout()
        mode_seg_row.setSpacing(8)
        self.modeSegment = SegmentedWidget(self.modeCard)
        self.modeSegment.addItem("local", tr("schedule.mode_local"))
        self.modeSegment.addItem("cloud", tr("schedule.mode_cloud"))
        self.modeSegment.addItem("both", tr("schedule.mode_both"))
        self.modeSegment.setFixedHeight(36)
        self.modeSegment.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.modeSegment.currentItemChanged.connect(self._on_mode_changed)
        mode_seg_row.addWidget(self.modeSegment)
        mode_seg_row.addStretch(1)
        mode_body.addLayout(mode_seg_row)

        # 4a. Local PC section
        self.localSection = CardWidget(self.modeCard)
        l_lay = QVBoxLayout(self.localSection)
        l_lay.setContentsMargins(14, 12, 14, 12)
        l_lay.setSpacing(10)

        l_head = QHBoxLayout()
        l_head.setSpacing(8)
        self.localHeading = StrongBodyLabel(tr("dashboard.mode_local"), self.localSection)
        self.localHeading.setWordWrap(True)
        self.windowsTasksBadge = StatusPill("", "info", self.localSection)
        self._update_windows_tasks_badge()
        l_head.addWidget(self.localHeading, 1)
        l_head.addWidget(self.windowsTasksBadge, 0, Qt.AlignVCenter)
        l_lay.addLayout(l_head)

        time_picker_grid = QGridLayout()
        time_picker_grid.setContentsMargins(0, 0, 0, 0)
        time_picker_grid.setHorizontalSpacing(8)
        time_picker_grid.setVerticalSpacing(6)
        self.localTimesLabel = BodyLabel(tr("schedule.local_times"), self.localSection)
        self.localTimesLabel.setWordWrap(True)
        self.timePicker = TimePicker(self.localSection)
        self.timePicker.setTime(QTime(8, 0))
        self.addTimeBtn = PushButton(
            FIF.ADD, escape_qt_mnemonic(tr("schedule.add_time")), self.localSection
        )
        self.removeTimeBtn = PushButton(
            FIF.REMOVE, escape_qt_mnemonic(tr("common.remove")), self.localSection
        )
        self.addTimeBtn.clicked.connect(self._add_local_time)
        self.removeTimeBtn.clicked.connect(self._remove_selected_time)
        time_picker_grid.addWidget(self.localTimesLabel, 0, 0, 1, 4)
        time_picker_grid.addWidget(self.timePicker, 1, 0)
        time_picker_grid.addWidget(self.addTimeBtn, 1, 1)
        time_picker_grid.addWidget(self.removeTimeBtn, 1, 2)
        time_picker_grid.setColumnStretch(3, 1)
        l_lay.addLayout(time_picker_grid)

        self.timesList = ListWidget(self.localSection)
        self.timesList.setFixedHeight(84)
        l_lay.addWidget(self.timesList)

        win_btn_row = FlowLayout(isTight=True)
        win_btn_row.setContentsMargins(0, 0, 0, 0)
        win_btn_row.setHorizontalSpacing(8)
        win_btn_row.setVerticalSpacing(6)
        self._applyWinPrimaryBtn = PrimaryPushButton(
            FIF.DATE_TIME, escape_qt_mnemonic(tr("schedule.sync_windows")), self.localSection
        )
        self._applyWinSecondaryBtn = PushButton(
            FIF.DATE_TIME, escape_qt_mnemonic(tr("schedule.sync_windows")), self.localSection
        )
        self.applyWinScheduleBtn = self._applyWinSecondaryBtn
        self.removeWinScheduleBtn = PushButton(
            FIF.DELETE, escape_qt_mnemonic(tr("schedule.remove_windows")), self.localSection
        )
        self._applyWinPrimaryBtn.clicked.connect(self._apply_windows_schedule)
        self._applyWinSecondaryBtn.clicked.connect(self._apply_windows_schedule)
        self.removeWinScheduleBtn.clicked.connect(self._remove_windows_schedule)
        win_btn_row.addWidget(self._applyWinPrimaryBtn)
        win_btn_row.addWidget(self._applyWinSecondaryBtn)
        win_btn_row.addWidget(self.removeWinScheduleBtn)
        l_lay.addLayout(win_btn_row)

        local_opts_grid = QGridLayout()
        local_opts_grid.setContentsMargins(0, 0, 0, 0)
        local_opts_grid.setHorizontalSpacing(12)
        local_opts_grid.setVerticalSpacing(8)
        local_opts_grid.setColumnStretch(0, 1)
        self.headlessLabel = BodyLabel(tr("schedule.local_headless"), self.localSection)
        self.headlessLabel.setWordWrap(True)
        self.headlessSwitch = SwitchButton(self.localSection)
        self.headlessSwitch.checkedChanged.connect(self._on_headless_changed)
        self.dailyCapLabel = BodyLabel(tr("content.daily_cap_label"), self.localSection)
        self.dailyCapLabel.setWordWrap(True)
        self.dailyCapSpin = FluentSpinBox(self.localSection)
        self.dailyCapSpin.setRange(0, 500)
        self.dailyCapSpin.valueChanged.connect(self._on_daily_cap_changed)
        local_opts_grid.addWidget(self.headlessLabel, 0, 0)
        local_opts_grid.addWidget(self.headlessSwitch, 0, 1, Qt.AlignLeft)
        local_opts_grid.addWidget(self.dailyCapLabel, 1, 0)
        local_opts_grid.addWidget(self.dailyCapSpin, 1, 1, Qt.AlignLeft)
        l_lay.addLayout(local_opts_grid)
        mode_body.addWidget(self.localSection)

        # 4b. GitHub Cloud section
        self.cloudSection = CardWidget(self.modeCard)
        c_lay = QVBoxLayout(self.cloudSection)
        c_lay.setContentsMargins(14, 12, 14, 12)
        c_lay.setSpacing(10)

        c_head_grid = QGridLayout()
        c_head_grid.setContentsMargins(0, 0, 0, 0)
        c_head_grid.setHorizontalSpacing(8)
        c_head_grid.setVerticalSpacing(6)
        c_head_top = QHBoxLayout()
        c_head_top.setSpacing(8)
        self.cloudHeading = StrongBodyLabel(tr("cloud.title"), self.cloudSection)
        self.ghStatusBadge = StatusPill("", "warning", self.cloudSection)
        c_head_top.addWidget(self.cloudHeading)
        c_head_top.addWidget(self.ghStatusBadge)
        c_head_top.addStretch(1)

        c_head_btns = QHBoxLayout()
        c_head_btns.setSpacing(8)
        self.ghSigninBtn = PrimaryPushButton(
            FIF.GITHUB, escape_qt_mnemonic(tr("cloud.signin_device")), self.cloudSection
        )
        self.ghSwitchBtn = PushButton(
            FIF.PEOPLE, escape_qt_mnemonic(tr("cloud.reconnect_github")), self.cloudSection
        )
        self.ghSignoutBtn = PushButton(
            FIF.CLOSE, escape_qt_mnemonic(tr("cloud.signout")), self.cloudSection
        )
        self.ghSigninBtn.clicked.connect(self._start_github_device_flow)
        self.ghSwitchBtn.clicked.connect(self._start_github_device_flow)
        self.ghSignoutBtn.clicked.connect(self._sign_out_github)
        c_head_btns.addWidget(self.ghSigninBtn)
        c_head_btns.addWidget(self.ghSwitchBtn)
        c_head_btns.addWidget(self.ghSignoutBtn)
        c_head_btns.addStretch(1)
        c_head_grid.addLayout(c_head_top, 0, 0)
        c_head_grid.addLayout(c_head_btns, 1, 0)
        c_lay.addLayout(c_head_grid)

        # Collapsible PAT fallback (hidden behind quiet toggle when signed in)
        pat_toggle_row = QHBoxLayout()
        pat_toggle_row.setContentsMargins(0, 0, 0, 0)
        pat_toggle_row.setSpacing(6)
        self.patToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT, escape_qt_mnemonic(tr("cloud.pat_toggle")), self.cloudSection
        )
        self.patToggleBtn.setMinimumWidth(0)
        self.patToggleBtn.clicked.connect(self._toggle_pat_drawer)
        pat_toggle_row.addWidget(self.patToggleBtn)
        pat_toggle_row.addStretch(1)
        c_lay.addLayout(pat_toggle_row)

        self.patContainer = QWidget(self.cloudSection)
        pat_grid = QGridLayout(self.patContainer)
        pat_grid.setContentsMargins(0, 0, 0, 0)
        pat_grid.setHorizontalSpacing(8)
        pat_grid.setVerticalSpacing(6)
        pat_grid.setColumnStretch(0, 1)
        self.patLabel = CaptionLabel(tr("cloud.signin_pat"), self.patContainer)
        self.patLabel.setWordWrap(True)
        self.patEdit = PasswordLineEdit(self.patContainer)
        self.patEdit.setMinimumWidth(0)
        self.patEdit.setPlaceholderText(tr("schedule.pat_placeholder"))
        self.savePatBtn = PushButton(
            FIF.SAVE, escape_qt_mnemonic(tr("schedule.save_pat")), self.patContainer
        )
        self.savePatBtn.clicked.connect(self._save_pat)
        pat_grid.addWidget(self.patLabel, 0, 0, 1, 2)
        pat_grid.addWidget(self.patEdit, 1, 0)
        pat_grid.addWidget(self.savePatBtn, 1, 1)
        self.patContainer.hide()
        c_lay.addWidget(self.patContainer)

        # Existing repo picker row
        existing_repo_row = QHBoxLayout()
        existing_repo_row.setSpacing(8)
        self.existingRepoCombo = ComboBox(self.cloudSection)
        self.existingRepoCombo.setMinimumWidth(0)
        self.existingRepoCombo.addItem(tr("cloud.select_existing_repo"), userData=None)
        self.existingRepoCombo.currentIndexChanged.connect(self._on_existing_repo_selected)
        self.fetchReposBtn = PushButton(
            FIF.SYNC, escape_qt_mnemonic(tr("cloud.fetch_repos")), self.cloudSection
        )
        self.fetchReposBtn.clicked.connect(self._fetch_my_repos)
        existing_repo_row.addWidget(self.existingRepoCombo, 1)
        existing_repo_row.addWidget(self.fetchReposBtn)
        c_lay.addLayout(existing_repo_row)

        # Private repo switch
        priv_row = QHBoxLayout()
        priv_row.setSpacing(10)
        self.privateRepoLabel = BodyLabel(tr("cloud.private_repo"), self.cloudSection)
        self.privateRepoLabel.setWordWrap(True)
        self.privateRepoSwitch = SwitchButton(self.cloudSection)
        self.privateRepoSwitch.checkedChanged.connect(self._on_private_repo_changed)
        priv_row.addWidget(self.privateRepoLabel, 1)
        priv_row.addWidget(self.privateRepoSwitch)
        c_lay.addLayout(priv_row)

        # Repo name grid (2-row so long path / error badge never overflows)
        repo_grid = QGridLayout()
        repo_grid.setContentsMargins(0, 0, 0, 0)
        repo_grid.setHorizontalSpacing(8)
        repo_grid.setVerticalSpacing(6)
        repo_grid.setColumnStretch(0, 1)
        repo_top = QHBoxLayout()
        repo_top.setSpacing(8)
        self.repoNameLabel = BodyLabel(tr("cloud.repo_name"), self.cloudSection)
        self.repoValidBadge = StatusPill("", "warning", self.cloudSection)
        self.repoValidCaption = CaptionLabel("", self.cloudSection)
        self.repoValidCaption.setStyleSheet("color: #2f9e44;")
        repo_top.addWidget(self.repoNameLabel)
        repo_top.addWidget(self.repoValidBadge)
        repo_top.addWidget(self.repoValidCaption)
        repo_top.addStretch(1)
        self.repoNameEdit = LineEdit(self.cloudSection)
        self.repoNameEdit.setMinimumWidth(0)
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.repoNameEdit.textChanged.connect(self._on_repo_name_changed)
        repo_grid.addLayout(repo_top, 0, 0)
        repo_grid.addWidget(self.repoNameEdit, 1, 0)
        c_lay.addLayout(repo_grid)

        # Sync-only checkbox (for existing repositories)
        sync_only_box = QVBoxLayout()
        sync_only_box.setContentsMargins(0, 0, 0, 0)
        sync_only_box.setSpacing(2)
        self.syncOnlyCheck = CheckBox(
            escape_qt_mnemonic(tr("cloud.sync_only")), self.cloudSection
        )
        self.syncOnlyHint = CaptionLabel(tr("cloud.sync_only_hint"), self.cloudSection)
        self.syncOnlyHint.setWordWrap(True)
        sync_only_box.addWidget(self.syncOnlyCheck)
        sync_only_box.addWidget(self.syncOnlyHint)
        c_lay.addLayout(sync_only_box)

        # Cloud cron grid (3-row so label, combo+custom, and WIB summary never overflow)
        cron_grid = QGridLayout()
        cron_grid.setContentsMargins(0, 0, 0, 0)
        cron_grid.setHorizontalSpacing(8)
        cron_grid.setVerticalSpacing(6)
        self.cronLabel = BodyLabel(tr("schedule.cloud_cron"), self.cloudSection)
        self.cronLabel.setWordWrap(True)
        self.cronCombo = ComboBox(self.cloudSection)
        self.cronCombo.setMinimumWidth(0)
        for cron_val, label_key in _CRON_PRESETS:
            self.cronCombo.addItem(tr(label_key), userData=cron_val)
        self.cronCombo.currentIndexChanged.connect(self._on_cron_preset_changed)

        self.customCronEdit = LineEdit(self.cloudSection)
        self.customCronEdit.setMinimumWidth(0)
        self.customCronEdit.setPlaceholderText("0 1 * * *")
        self.customCronEdit.setMaximumWidth(170)
        self.customCronEdit.textChanged.connect(self._on_custom_cron_changed)

        self.cronSummaryLabel = CaptionLabel("", self.cloudSection)
        self.cronSummaryLabel.setWordWrap(True)
        cron_grid.addWidget(self.cronLabel, 0, 0, 1, 2)
        cron_grid.addWidget(self.cronCombo, 1, 0)
        cron_grid.addWidget(self.customCronEdit, 1, 1)
        cron_grid.addWidget(self.cronSummaryLabel, 2, 0, 1, 2)
        cron_grid.setColumnStretch(0, 1)
        c_lay.addLayout(cron_grid)

        # Deploy button row
        deploy_row = QHBoxLayout()
        deploy_row.setSpacing(10)
        self.deployBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("schedule.deploy_btn")), self.cloudSection
        )
        self.deployBtn.clicked.connect(self._deploy_to_github)
        self.deployStatusLabel = CaptionLabel("", self.cloudSection)
        self.deployStatusLabel.setWordWrap(True)
        deploy_row.addWidget(self.deployBtn)
        deploy_row.addWidget(self.deployStatusLabel, 1)
        c_lay.addLayout(deploy_row)

        mode_body.addWidget(self.cloudSection)
        self.modeCard.viewLayout.addLayout(mode_body)
        self.vBoxLayout.addWidget(self.modeCard)
        self.vBoxLayout.addStretch(1)

    # -- Load & Sync -----------------------------------------------------
    def refresh_from_state(self, state: AppState | None = None) -> None:
        if state is not None:
            self._state = state
        st = self._state
        self._loading = True
        try:
            self.msgEdit.setPlainText(st.message)

            self.videoSwitch.setChecked(bool(st.video_enabled))
            self.videoUrlEdit.setText(st.video_url)
            self.shareTimesSpin.setValue(max(1, min(10, int(st.share_times or 2))))
            self._sync_video_controls()

            self.photoSwitch.setChecked(bool(st.photo_enabled))
            self._sync_photo_preview()

            mode = st.mode if st.mode in ("local", "cloud", "both") else "cloud"
            self.modeSegment.setCurrentItem(mode)
            self._sync_mode_visibility(mode)

            self._reload_times_list()
            self.headlessSwitch.setChecked(bool(st.headless_local))
            self.dailyCapSpin.setValue(max(0, int(st.daily_cap)))

            self.privateRepoSwitch.setChecked(bool(st.github_private))
            self.repoNameEdit.setText(st.github_repo_name)
            self._update_repo_badge()

            # Select matching cron preset or custom
            matched_idx = len(_CRON_PRESETS) - 1
            for idx, (preset_val, _k) in enumerate(_CRON_PRESETS):
                if preset_val == st.cloud_cron:
                    matched_idx = idx
                    break
            self.cronCombo.setCurrentIndex(matched_idx)
            self.customCronEdit.setText(st.cloud_cron)
            self.customCronEdit.setVisible(matched_idx == len(_CRON_PRESETS) - 1)
            self._update_cron_summary()

            self._refresh_github_badge()
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
        if self._loading:
            return
        self._state.message = self.msgEdit.toPlainText()
        self._schedule_save()

    def _on_video_toggled(self, checked: bool) -> None:
        if not self._loading:
            self._state.video_enabled = bool(checked)
            self.state_changed.emit()
        self._sync_video_controls()

    def _on_video_url_changed(self, text: str) -> None:
        if not self._loading:
            self._state.video_url = text.strip()
            self._schedule_save()
        self._sync_video_controls()

    def _on_share_times_changed(self, val: int) -> None:
        if not self._loading:
            self._state.share_times = int(val)
            self._schedule_save()

    def _sync_video_controls(self) -> None:
        enabled = self.videoSwitch.isChecked()
        self.videoUrlEdit.setEnabled(enabled)
        self.resolveShortBtn.setEnabled(enabled)
        self.shareTimesSpin.setEnabled(enabled)

        if not enabled:
            self.videoBadge.setState("neutral", tr("common.disabled"))
            self.videoHintLabel.setText(tr("content.video_disabled"))
            return

        res = validate_video_url(self.videoUrlEdit.text(), resolve_short=False)
        if res["valid"]:
            self.videoBadge.setState("success", tr("content.video_valid", aweme_id=res["aweme_id"]))
            self.videoHintLabel.setText("")
        else:
            self.videoBadge.setState("warning", tr("common.warning"))
            self.videoHintLabel.setText(res.get("error") or "")

    def _resolve_short_link(self) -> None:
        raw = self.videoUrlEdit.text().strip()
        if not raw:
            return
        self.resolveShortBtn.setEnabled(False)
        self._workers.start_task(
            validate_video_url,
            raw,
            resolve_short=True,
            task_key="resolve_short_link",
            on_finished=self._on_short_resolved,
            on_error=self._on_short_error,
        )

    def _on_short_resolved(self, res: dict[str, Any]) -> None:
        self.resolveShortBtn.setEnabled(self.videoSwitch.isChecked())
        if res.get("valid"):
            self.videoUrlEdit.setText(res["normalized_url"])
            InfoBar.success(
                tr("common.success"),
                tr("content.video_valid", aweme_id=res["aweme_id"]),
                duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        else:
            InfoBar.warning(
                tr("common.warning"),
                str(res.get("error") or ""),
                duration=4500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_short_error(self, err: str) -> None:
        self.resolveShortBtn.setEnabled(self.videoSwitch.isChecked())
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
        self.state_changed.emit()

    def _use_default_photo(self) -> None:
        self._state.photo_path = ""
        self._state.photo_enabled = True
        self._loading = True
        self.photoSwitch.setChecked(True)
        self._loading = False
        self._sync_photo_preview()
        self.state_changed.emit()

    def _clear_reset_photo(self) -> None:
        self._state.photo_path = ""
        self._state.photo_enabled = False
        self._loading = True
        self.photoSwitch.setChecked(False)
        self._loading = False
        self._sync_photo_preview()
        self.state_changed.emit()

    def _sync_photo_preview(self) -> None:
        enabled = self.photoSwitch.isChecked()
        self.choosePhotoBtn.setEnabled(True)
        self.defaultPhotoBtn.setEnabled(True)

        if not enabled:
            self.photoPreview.setImage(None)
            self.photoStatusLabel.setText(tr("content.photo_disabled"))
            return

        custom = self._state.photo_path.strip()
        target = _resolve_asset_path(custom or DEFAULT_PHOTO_PATH)
        if Path(target).is_file():
            self.photoPreview.setImage(target)
            self.photoPreview.scaledToHeight(104)
        else:
            self.photoPreview.setImage(None)

        if custom:
            self.photoStatusLabel.setText(tr("content.photo_custom", path=Path(custom).name))
        else:
            self.photoStatusLabel.setText(tr("content.photo_default"))

    # -- Mode & Local Scheduler ------------------------------------------
    def _on_mode_changed(self, route_key: str) -> None:
        if self._loading:
            return
        if route_key not in ("local", "cloud", "both"):
            return
        self._state.mode = route_key
        self._sync_mode_visibility(route_key)
        self.state_changed.emit()

    def _sync_mode_visibility(self, mode: str) -> None:
        self.localSection.setVisible(mode in ("local", "both"))
        self.cloudSection.setVisible(mode in ("cloud", "both"))
        is_local_only = mode == "local"
        self._applyWinPrimaryBtn.setVisible(is_local_only)
        self._applyWinSecondaryBtn.setVisible(not is_local_only)
        self.applyWinScheduleBtn = (
            self._applyWinPrimaryBtn if is_local_only else self._applyWinSecondaryBtn
        )

    def _reload_times_list(self) -> None:
        self.timesList.clear()
        for t in self._state.local_times:
            item = QListWidgetItem(FIF.DATE_TIME.icon(), f"{t} WIB")
            item.setSizeHint(QSize(0, 32))
            self.timesList.addItem(item)

    def _add_local_time(self) -> None:
        qt = self.timePicker.getTime()
        hhmm = f"{qt.hour():02d}:{qt.minute():02d}"
        if hhmm not in self._state.local_times:
            self._state.local_times = sorted([*self._state.local_times, hhmm])
            self._reload_times_list()
            self.state_changed.emit()

    def _remove_selected_time(self) -> None:
        row = self.timesList.currentRow()
        if row < 0 and self.timesList.count() > 0:
            row = self.timesList.count() - 1
        if 0 <= row < len(self._state.local_times):
            if len(self._state.local_times) == 1:
                InfoBar.warning(
                    tr("common.warning"),
                    tr("schedule.local_times"),
                    duration=2500,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self.window(),
                )
                return
            del self._state.local_times[row]
            self._reload_times_list()
            self.state_changed.emit()

    def _update_windows_tasks_badge(self) -> None:
        cnt = max(0, int(self._windows_tasks_count or 0))
        if sys.platform != "win32" or self._windows_tasks_count is None:
            level = "info"
        else:
            level = "success" if cnt else "warning"
        self.windowsTasksBadge.setState(level, tr("schedule.local_tasks_count", count=cnt))

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
        self._loading = True
        try:
            self.headlessSwitch.setChecked(bool(self._state.headless_local))
        finally:
            self._loading = False

    def _on_headless_changed(self, checked: bool) -> None:
        if not self._loading:
            self._state.headless_local = bool(checked)
            self.state_changed.emit()

    def _on_daily_cap_changed(self, val: int) -> None:
        if not self._loading:
            self._state.daily_cap = int(val)
            self._schedule_save()

    # -- GitHub Auth & Deploy --------------------------------------------
    def _set_pat_expanded(self, expanded: bool) -> None:
        self._pat_expanded = bool(expanded)
        self.patContainer.setVisible(self._pat_expanded)
        self.patToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._pat_expanded else FIF.CHEVRON_RIGHT
        )

    def _toggle_pat_drawer(self) -> None:
        self._set_pat_expanded(not self._pat_expanded)

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
        self.ghSignoutBtn.setVisible(has_tok)
        self.ghSignoutBtn.setEnabled(has_tok)

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
            self._set_pat_expanded(False)
            self._refresh_github_badge()
            self.state_changed.emit()
            InfoBar.success(
                tr("common.success"),
                tr("cloud.signed_in_as", login=self._state.github_login),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

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
        self.patEdit.clear()
        self._set_pat_expanded(False)
        self._refresh_github_badge()
        self.state_changed.emit()
        InfoBar.success(
            tr("common.success"),
            tr("cloud.signed_in_as", login=self._state.github_login),
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_pat_err(self, err: str) -> None:
        self.savePatBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _sign_out_github(self) -> None:
        self._secrets.delete_github_token()
        self._state.github_login = ""
        self._refresh_github_badge()
        self.state_changed.emit()

    def _on_private_repo_changed(self, checked: bool) -> None:
        if not self._loading:
            self._state.github_private = bool(checked)
            self.state_changed.emit()

    def _on_repo_name_changed(self, text: str) -> None:
        if not self._loading:
            self._state.github_repo_name = text.strip()
            self._schedule_save()
        self._update_repo_badge()

    def _update_repo_badge(self) -> None:
        chk = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        if chk["valid"]:
            self.repoValidBadge.setState("success", tr("content.repo_valid"))
            self.repoValidBadge.hide()
            self.repoValidCaption.setText(f"✓ {tr('content.repo_valid')}")
            self.repoValidCaption.show()
            self.syncOnlyCheck.setEnabled(True)
        else:
            self.repoValidCaption.hide()
            self.repoValidBadge.setState("warning", chk.get("error") or tr("common.warning"))
            self.repoValidBadge.show()
            self.syncOnlyCheck.setEnabled(False)

    def _fetch_my_repos(self) -> None:
        if self._workers.is_running("fetch_user_repos"):
            return
        token = self._secrets.get_github_token().strip()
        if not token:
            InfoBar.warning(
                tr("common.warning"),
                tr("schedule.not_signed_in"),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self.fetchReposBtn.setEnabled(False)
        self._workers.start_task(
            _fetch_user_repos_task,
            token,
            task_key="fetch_user_repos",
            on_finished=self._on_repos_fetched,
            on_error=self._on_repos_fetch_error,
        )

    def _on_repos_fetched(self, repos: list[dict[str, Any]]) -> None:
        self.fetchReposBtn.setEnabled(True)
        self._fetched_repos = {}
        current_txt = self.repoNameEdit.text().strip().lower()
        match_idx = 0

        self.existingRepoCombo.blockSignals(True)
        self.existingRepoCombo.clear()
        self.existingRepoCombo.addItem(tr("cloud.select_existing_repo"), userData=None)
        for item in repos or []:
            full_name = str(item.get("full_name") or "").strip()
            if not full_name:
                continue
            self._fetched_repos[full_name.lower()] = item
            vis = tr("cloud.repo_private_tag") if item.get("private") else tr("cloud.repo_public_tag")
            self.existingRepoCombo.addItem(f"{full_name} — {vis}", userData=item)
            if current_txt and (
                current_txt == full_name.lower()
                or current_txt == str(item.get("name") or "").strip().lower()
            ):
                match_idx = self.existingRepoCombo.count() - 1
        self.existingRepoCombo.setCurrentIndex(match_idx)
        self.existingRepoCombo.blockSignals(False)

    def _on_repos_fetch_error(self, err: str) -> None:
        self.fetchReposBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=4500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_existing_repo_selected(self, _idx: int) -> None:
        if self._loading:
            return
        data = self.existingRepoCombo.currentData()
        if not isinstance(data, dict):
            return
        full_name = str(data.get("full_name") or "").strip()
        is_priv = bool(data.get("private", False))
        if full_name:
            self.repoNameEdit.setText(full_name)
        self.privateRepoSwitch.setChecked(is_priv)
        self.syncOnlyCheck.setEnabled(True)

    def _on_cron_preset_changed(self, _idx: int) -> None:
        val = self.cronCombo.currentData()
        is_custom = val == "custom"
        self.customCronEdit.setVisible(is_custom)
        if not self._loading:
            if not is_custom and val:
                self._state.cloud_cron = str(val)
                self.customCronEdit.setText(self._state.cloud_cron)
                self.state_changed.emit()
        self._update_cron_summary()

    def _on_custom_cron_changed(self, text: str) -> None:
        if not self._loading and self.cronCombo.currentData() == "custom":
            cleaned = " ".join(text.strip().split())
            if cleaned:
                self._state.cloud_cron = cleaned
                self._schedule_save()
        self._update_cron_summary()

    def _update_cron_summary(self) -> None:
        cron = self.customCronEdit.text().strip() if self.cronCombo.currentData() == "custom" else str(self.cronCombo.currentData() or self._state.cloud_cron)
        summary = cron_to_wib_summary(cron, lang=get_language())
        self.cronSummaryLabel.setText(tr("schedule.cloud_summary", summary=summary))

    def _deploy_to_github(self) -> None:
        if self._workers.is_running("deploy_github"):
            return
        self.flush_edits()

        token = self._secrets.get_github_token().strip()
        if not token:
            InfoBar.warning(
                tr("common.warning"),
                tr("schedule.not_signed_in"),
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        repo_chk = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        if not repo_chk["valid"]:
            InfoBar.warning(
                tr("common.warning"),
                repo_chk.get("error") or tr("common.warning"),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        if self._state.video_enabled:
            vchk = validate_video_url(self._state.video_url, resolve_short=False)
            if not vchk["valid"]:
                InfoBar.warning(
                    tr("common.warning"),
                    vchk.get("error") or tr("common.warning"),
                    duration=4500,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self.window(),
                )
                return

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

        cron_expr = (
            " ".join(self.customCronEdit.text().strip().split())
            if self.cronCombo.currentData() == "custom"
            else str(self.cronCombo.currentData() or self._state.cloud_cron)
        )
        if len(cron_expr.split(" ")) != 5:
            InfoBar.warning(
                tr("common.warning"),
                "Invalid 5-field UTC cron expression.",
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return

        raw_repo_arg = (
            repo_chk["full_name"]
            if "/" in self.repoNameEdit.text().strip()
            else repo_chk["name"]
        )
        sync_only = bool(self.syncOnlyCheck.isEnabled() and self.syncOnlyCheck.isChecked())

        self.deployBtn.setEnabled(False)
        self.deployStatusLabel.setText(tr("common.loading"))
        self._workers.start_task(
            _deploy_github_task,
            token,
            raw_repo_arg,
            bool(self._state.github_private),
            self._state.github_source_repo,
            cron_expr,
            desired_secrets,
            sync_only=sync_only,
            task_key="deploy_github",
            pass_log_cb=True,
            on_log=self._on_deploy_log,
            on_finished=self._on_deploy_finished,
            on_error=self._on_deploy_error,
        )

    def _on_deploy_log(self, msg: str) -> None:
        self.deployStatusLabel.setText(msg)
        self.log_emitted.emit(msg)

    def _on_deploy_finished(self, res: dict[str, Any]) -> None:
        self.deployBtn.setEnabled(True)
        full_name = res.get("full_name", "")
        if res.get("login"):
            self._state.github_login = res["login"]
        self._refresh_github_badge()
        self.state_changed.emit()
        self.deployStatusLabel.setText(f"{full_name} ({len(res.get('written', []))} secrets synced)")
        InfoBar.success(
            tr("common.success"),
            f"{full_name} — {len(res.get('written', []))} secrets",
            duration=5000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _on_deploy_error(self, err: str) -> None:
        self.deployBtn.setEnabled(True)
        self.deployStatusLabel.setText(err)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=6500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def retranslate(self) -> None:
        self.titleLabel.setText(tr("content.title"))
        self.msgCard.setTitle(tr("content.message_label"))
        self.videoCard.setTitle(tr("content.video_toggle"))
        self.videoUrlLabel.setText(tr("content.video_url_label"))
        self.resolveShortBtn.setText(escape_qt_mnemonic(tr("content.video_resolve")))
        self.videoHintCaption.setText(tr("content.video_hint"))
        self.shareTimesLabel.setText(tr("content.share_times_label"))
        self.photoCard.setTitle(tr("content.photo_toggle"))
        self.choosePhotoBtn.setText(escape_qt_mnemonic(tr("content.photo_select")))
        self.defaultPhotoBtn.setText(escape_qt_mnemonic(tr("content.photo_use_default")))
        self.resetPhotoBtn.setText(escape_qt_mnemonic(tr("content.photo_reset")))
        self.modeCard.setTitle(tr("schedule.title"))
        self.modeSegment.items["local"].setText(tr("schedule.mode_local"))
        self.modeSegment.items["cloud"].setText(tr("schedule.mode_cloud"))
        self.modeSegment.items["both"].setText(tr("schedule.mode_both"))
        self.localHeading.setText(tr("dashboard.mode_local"))
        self.localTimesLabel.setText(tr("schedule.local_times"))
        self.addTimeBtn.setText(escape_qt_mnemonic(tr("schedule.add_time")))
        self.removeTimeBtn.setText(escape_qt_mnemonic(tr("common.remove")))
        win_sync_txt = escape_qt_mnemonic(tr("schedule.sync_windows"))
        self._applyWinPrimaryBtn.setText(win_sync_txt)
        self._applyWinSecondaryBtn.setText(win_sync_txt)
        self.removeWinScheduleBtn.setText(escape_qt_mnemonic(tr("schedule.remove_windows")))
        self.headlessLabel.setText(tr("schedule.local_headless"))
        self.dailyCapLabel.setText(tr("content.daily_cap_label"))
        self.cloudHeading.setText(tr("cloud.title"))
        self.ghSigninBtn.setText(escape_qt_mnemonic(tr("cloud.signin_device")))
        self.ghSwitchBtn.setText(escape_qt_mnemonic(tr("cloud.reconnect_github")))
        self.ghSignoutBtn.setText(escape_qt_mnemonic(tr("cloud.signout")))
        self.patToggleBtn.setText(escape_qt_mnemonic(tr("cloud.pat_toggle")))
        self.patLabel.setText(tr("cloud.signin_pat"))
        self.patEdit.setPlaceholderText(tr("schedule.pat_placeholder"))
        self.savePatBtn.setText(escape_qt_mnemonic(tr("schedule.save_pat")))
        self.fetchReposBtn.setText(escape_qt_mnemonic(tr("cloud.fetch_repos")))
        if self.existingRepoCombo.count() > 0 and self.existingRepoCombo.itemData(0) is None:
            self.existingRepoCombo.setItemText(0, tr("cloud.select_existing_repo"))
        self.privateRepoLabel.setText(tr("cloud.private_repo"))
        self.repoNameLabel.setText(tr("cloud.repo_name"))
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.syncOnlyCheck.setText(escape_qt_mnemonic(tr("cloud.sync_only")))
        self.syncOnlyHint.setText(tr("cloud.sync_only_hint"))
        self.cronLabel.setText(tr("schedule.cloud_cron"))
        for idx, (_val, key) in enumerate(_CRON_PRESETS):
            self.cronCombo.setItemText(idx, tr(key))
        self.deployBtn.setText(escape_qt_mnemonic(tr("schedule.deploy_btn")))
        self._update_windows_tasks_badge()
        self._sync_video_controls()
        self._sync_photo_preview()
        self._update_repo_badge()
        self._update_cron_summary()
        self._refresh_github_badge()


__all__ = ["ContentPage"]
