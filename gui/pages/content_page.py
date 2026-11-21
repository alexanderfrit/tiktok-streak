"""Content, media, execution mode, Windows Task Scheduler, and GitHub Cloud deploy page."""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
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
    HorizontalSeparator,
    ImageLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    PasswordLineEdit,
    PlainTextEdit,
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
    cron_to_wib_summary,
    prepare_photo_secret,
    times_to_utc_cron,
    validate_repo_name,
    validate_video_url,
)
try:
    from gui.services.validators import (
        TIMEZONE_CHOICES,
        crons_to_local_times,
        format_schedule_summary,
        get_timezone_info,
        times_to_utc_crons,
    )
except ImportError:  # pragma: no cover
    TIMEZONE_CHOICES = (
        {"id": "Asia/Jakarta", "short": "WIB", "label": "WIB — Jakarta / Western Indonesia (UTC+7)", "offset_minutes": 420},
    )

    def get_timezone_info(tz_id: str | None) -> dict[str, Any]:  # type: ignore[misc]
        return TIMEZONE_CHOICES[0]

    def times_to_utc_crons(times_hhmm, tz_id: str = "Asia/Jakarta") -> list[str]:  # type: ignore[misc]
        return [times_to_utc_cron(times_hhmm)]

    def crons_to_local_times(crons, tz_id: str = "Asia/Jakarta") -> list[str]:  # type: ignore[misc]
        raw = str(crons or "").strip()
        parts = raw.split()
        if len(parts) == 5 and parts[2:] == ["*", "*", "*"] and parts[0].isdigit():
            mm = int(parts[0])
            out = []
            for h in parts[1].split(","):
                if h.strip().isdigit():
                    tot = (int(h.strip()) * 60 + mm + 420) % 1440
                    out.append(f"{tot // 60:02d}:{tot % 60:02d}")
            return sorted(set(out))
        return []

    def format_schedule_summary(crons, tz_id: str = "Asia/Jakarta", lang: str = "en") -> str:  # type: ignore[misc]
        raw = "\n".join(crons) if isinstance(crons, (list, tuple)) else str(crons or "")
        return cron_to_wib_summary(raw, lang=lang)

from gui.widgets.chip_list import ChipFlowWidget
from gui.widgets.device_login_dialog import DeviceLoginDialog
from gui.widgets.fluent_spin_box import FluentSpinBox
from gui.widgets.split_row import ResponsiveSplitRow
from gui.widgets.status_pill import StatusPill
from gui.version import APP_VERSION
from gui.workers import WorkerController


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


def _validate_pat_task(token: str) -> dict[str, Any]:
    gh = GitHub(token.strip())
    me = gh.whoami()
    return {"token": token.strip(), "user": me}


def _discover_bot_repos_task(token: str) -> dict[str, Any]:
    gh = GitHub(token.strip())
    return gh.discover_bot_repos(limit=100, workflow_file=WORKFLOW_FILE)


def _toggle_bot_repo_workflow_task(token: str, full_name: str, pause: bool) -> dict[str, Any]:
    gh = GitHub(token.strip())
    if pause:
        res = gh.pause_workflow(full_name, WORKFLOW_FILE, cancel_running=True)
        return {
            "full_name": full_name,
            "active": False,
            "workflow_state": "disabled_manually",
            "cancelled_run_ids": list(res.get("cancelled_run_ids") or []),
        }
    gh.enable_workflow(full_name, WORKFLOW_FILE)
    return {
        "full_name": full_name,
        "active": True,
        "workflow_state": "active",
        "cancelled_run_ids": [],
    }


def _pause_multiple_bot_repos_task(token: str, repo_names: list[str]) -> dict[str, Any]:
    gh = GitHub(token.strip())
    paused: list[str] = []
    for full_name in repo_names:
        clean = str(full_name or "").strip()
        if not clean:
            continue
        gh.pause_workflow(clean, WORKFLOW_FILE, cancel_running=True)
        paused.append(clean)
    return {"paused": paused}


class _SelectionDot(QWidget):
    """Compact circular radio indicator showing which repository row is active."""

    def __init__(self, selected: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = bool(selected)
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.update()

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
    """6px semantic dot used inline with calm status text instead of pill badges."""

    def __init__(self, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = bool(active)
        self.setFixedSize(8, 8)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = isDarkTheme()
        if self._active:
            color = QColor("#6ccb5f" if dark else "#0f7b0f")
        else:
            color = QColor("#f59e0b" if dark else "#9d5d00")
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(1, 1, 6, 6)


class _BotRepoRow(QFrame):
    """Flat, clickable repository row with split-aligned uniform actions."""

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
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self.radioDot = _SelectionDot(is_selected, self)
        lay.addWidget(self.radioDot, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.nameLabel = StrongBodyLabel(full_name, self) if is_selected else BodyLabel(full_name, self)
        self.nameLabel.setStyleSheet("background: transparent;")
        self.nameLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_col.addWidget(self.nameLabel)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(5)
        self.statusDot = _StatusDot(is_active, self)
        state_lbl = tr("cloud.repo_state_active") if is_active else tr("cloud.repo_state_paused")
        vis_lbl = tr("cloud.repo_private_tag") if is_priv else tr("cloud.repo_public_tag")
        meta_text = f"{state_lbl}  \u00b7  {vis_lbl}"
        if is_selected:
            meta_text = f"{meta_text}  \u00b7  {tr('cloud.repo_selected_tag')}"
        self.metaLabel = CaptionLabel(meta_text, self)
        self.metaLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        meta_row.addWidget(self.statusDot, 0, Qt.AlignVCenter)
        meta_row.addWidget(self.metaLabel, 0, Qt.AlignVCenter)
        meta_row.addStretch(1)
        text_col.addLayout(meta_row)

        lay.addLayout(text_col, 1)

        btns_row = QHBoxLayout()
        btns_row.setContentsMargins(0, 0, 0, 0)
        btns_row.setSpacing(6)

        fm = self.fontMetrics()
        max_lbl_w = max(
            fm.horizontalAdvance(tr("cloud.pause_short")),
            fm.horizontalAdvance(tr("cloud.resume_short")),
        )
        uniform_w = max(108, max_lbl_w + 52)
        toggle_txt = tr("cloud.pause_short") if is_active else tr("cloud.resume_short")
        self.toggleBtn = PushButton(
            FIF.PAUSE if is_active else FIF.PLAY,
            escape_qt_mnemonic(toggle_txt),
            self,
        )
        self.toggleBtn.setFixedHeight(32)
        self.toggleBtn.setFixedWidth(uniform_w)
        self.toggleBtn.setCursor(Qt.ArrowCursor)

        self.openBtn = TransparentToolButton(FIF.LINK, self)
        self.openBtn.setFixedSize(32, 32)
        self.openBtn.setToolTip(f"{tr('cloud.open_repo_tooltip')}\n{html_url}")
        self.openBtn.setCursor(Qt.ArrowCursor)
        self.openBtn.clicked.connect(lambda _=False, u=html_url: QDesktopServices.openUrl(QUrl(u)))

        btns_row.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)
        btns_row.addWidget(self.openBtn, 0, Qt.AlignVCenter)
        lay.addLayout(btns_row)

        self.apply_theme(is_selected)

    def apply_theme(self, is_selected: bool) -> None:
        dark = isDarkTheme()
        if is_selected:
            bg = "rgba(76, 194, 255, 0.08)" if dark else "rgba(0, 95, 184, 0.06)"
            border = "rgba(76, 194, 255, 0.30)" if dark else "rgba(0, 95, 184, 0.24)"
            hover_bg = "rgba(76, 194, 255, 0.12)" if dark else "rgba(0, 95, 184, 0.10)"
            meta_fg = "#b8c7d4" if dark else "#455463"
        else:
            bg = "transparent"
            border = "rgba(255, 255, 255, 0.07)" if dark else "rgba(0, 0, 0, 0.07)"
            hover_bg = "rgba(255, 255, 255, 0.04)" if dark else "rgba(0, 0, 0, 0.035)"
            meta_fg = "#a3a3a3" if dark else "#616161"
        self.setStyleSheet(
            f"QFrame#botRepoRow {{"
            f" background-color: {bg};"
            f" border: 1px solid {border};"
            f" border-radius: 6px;"
            f"}}"
            f"QFrame#botRepoRow:hover {{"
            f" background-color: {hover_bg};"
            f"}}"
        )
        self.metaLabel.setStyleSheet(f"color: {meta_fg}; background: transparent;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


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
    pause_repos: tuple[str, ...] | list[str] = (),
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

    for old_repo in pause_repos or ():
        cleaned_old = str(old_repo or "").strip()
        if not cleaned_old or cleaned_old.lower() == full_name.lower():
            continue
        _log(f"Pausing extra bot schedule in {cleaned_old}...")
        try:
            gh.pause_workflow(cleaned_old, WORKFLOW_FILE, cancel_running=True)
        except GitHubError as exc:
            _log(f"Note pausing {cleaned_old}: {exc}")

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
        "sync_only": bool(sync_only),
    }


class ContentPage(SmoothScrollArea):
    """Configure message, video/photo cards, local scheduler, and GitHub deployment."""

    state_changed = Signal()
    log_emitted = Signal(str)
    cloud_repos_updated = Signal(object)

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
        self._discovered_all_repos: dict[str, dict[str, Any]] = {}
        self._discovered_bot_repos: list[dict[str, Any]] = []
        self._bot_scan_done_for_token: str = ""
        self._bot_repo_row_widgets: list[QWidget] = []
        self._cloud_advanced_expanded = False
        self._pat_expanded = False
        self._raw_cron_expanded = False
        self._custom_cron_dirty = False
        self._video_verify_state: str = "unverified"
        self._verified_video_url: str = ""
        self._verified_video_author: str = ""
        self._video_verify_error: str = ""

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
        self.videoCard.headerView.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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

        tz_row = QHBoxLayout()
        tz_row.setSpacing(10)
        self.timezoneLabel = BodyLabel(tr("schedule.timezone"), self.modeCard)
        self.timezoneCombo = ComboBox(self.modeCard)
        self.timezoneCombo.setMinimumWidth(0)
        self.timezoneCombo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for tz in TIMEZONE_CHOICES:
            self.timezoneCombo.addItem(tz["label"], userData=tz["id"])
        self.timezoneCombo.currentIndexChanged.connect(self._on_timezone_changed)
        tz_row.addWidget(self.timezoneLabel)
        tz_row.addWidget(self.timezoneCombo, 1)
        mode_body.addLayout(tz_row)

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
        self.localSection = SimpleCardWidget(self.modeCard)
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
        self.removeTimeBtn.hide()
        self.addTimeBtn.clicked.connect(self._add_local_time)
        self.removeTimeBtn.clicked.connect(self._remove_selected_time)
        time_picker_grid.addWidget(self.localTimesLabel, 0, 0, 1, 3)
        time_picker_grid.addWidget(self.timePicker, 1, 0)
        time_picker_grid.addWidget(self.addTimeBtn, 1, 1)
        time_picker_grid.setColumnStretch(2, 1)
        l_lay.addLayout(time_picker_grid)

        self.localTimesChips = ChipFlowWidget(
            empty_text=tr("schedule.times_empty"),
            icon=FIF.DATE_TIME,
            parent=self.localSection,
        )
        self.localTimesChips.set_remove_tooltip(tr("common.remove"))
        self.localTimesChips.item_removed.connect(self._remove_local_time_by_key)
        l_lay.addWidget(self.localTimesChips)

        # Hidden backing ListWidget kept synced for backward test compatibility
        self.timesList = ListWidget(self.localSection)
        self.timesList.hide()

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

        # 4b. GitHub Cloud section (single flat card surface with 1px hairlines)
        self.cloudSection = SimpleCardWidget(self.modeCard)
        c_lay = QVBoxLayout(self.cloudSection)
        c_lay.setContentsMargins(18, 16, 18, 18)
        c_lay.setSpacing(14)

        # Split-aligned header bar: title + status pill left, account actions flush right
        c_title_grp = QWidget(self.cloudSection)
        c_title_grp.setFixedHeight(32)
        c_title_lay = QHBoxLayout(c_title_grp)
        c_title_lay.setContentsMargins(0, 0, 0, 0)
        c_title_lay.setSpacing(8)
        self.cloudHeading = StrongBodyLabel(tr("cloud.title"), c_title_grp)
        self.ghStatusBadge = StatusPill("", "warning", c_title_grp)
        c_title_lay.addWidget(self.cloudHeading, 0, Qt.AlignVCenter)
        c_title_lay.addWidget(self.ghStatusBadge, 0, Qt.AlignVCenter)

        c_btns_grp = QWidget(self.cloudSection)
        c_btns_grp.setFixedHeight(32)
        c_btns_lay = QHBoxLayout(c_btns_grp)
        c_btns_lay.setContentsMargins(0, 0, 0, 0)
        c_btns_lay.setSpacing(8)
        self.ghSigninBtn = PrimaryPushButton(
            FIF.GITHUB, escape_qt_mnemonic(tr("cloud.signin_device")), c_btns_grp
        )
        self.ghSwitchBtn = PushButton(
            FIF.PEOPLE, escape_qt_mnemonic(tr("cloud.reconnect_github")), c_btns_grp
        )
        self.ghSignoutBtn = PushButton(
            FIF.CLOSE, escape_qt_mnemonic(tr("cloud.signout")), c_btns_grp
        )
        self.ghSigninBtn.clicked.connect(self._start_github_device_flow)
        self.ghSwitchBtn.clicked.connect(self._start_github_device_flow)
        self.ghSignoutBtn.clicked.connect(self._sign_out_github)
        c_btns_lay.addWidget(self.ghSigninBtn, 0, Qt.AlignVCenter)
        c_btns_lay.addWidget(self.ghSwitchBtn, 0, Qt.AlignVCenter)
        c_btns_lay.addWidget(self.ghSignoutBtn, 0, Qt.AlignVCenter)

        self.cloudHeaderRow = ResponsiveSplitRow(
            c_title_grp, c_btns_grp, h_spacing=12, v_spacing=8, parent=self.cloudSection
        )
        c_lay.addWidget(self.cloudHeaderRow)
        c_lay.addWidget(HorizontalSeparator(self.cloudSection))

        # Group 1: Target Repository (borderless flat container)
        self.cloudRepoBox = QWidget(self.cloudSection)
        self.cloudRepoBox.setObjectName("cloudRepoBox")
        repo_box_lay = QVBoxLayout(self.cloudRepoBox)
        repo_box_lay.setContentsMargins(0, 0, 0, 0)
        repo_box_lay.setSpacing(10)

        self.cloudRepoSectionLabel = StrongBodyLabel(
            tr("cloud.section_account_repo"), self.cloudRepoBox
        )
        repo_box_lay.addWidget(self.cloudRepoSectionLabel)

        # Repo name field with calm helper caption underneath
        repo_grid = QGridLayout()
        repo_grid.setContentsMargins(0, 0, 0, 0)
        repo_grid.setHorizontalSpacing(8)
        repo_grid.setVerticalSpacing(6)
        repo_grid.setColumnStretch(0, 1)
        repo_top = QHBoxLayout()
        repo_top.setSpacing(8)
        self.repoNameLabel = BodyLabel(tr("cloud.repo_name"), self.cloudRepoBox)
        self.repoValidBadge = StatusPill("", "warning", self.cloudRepoBox)
        repo_top.addWidget(self.repoNameLabel, 0, Qt.AlignVCenter)
        repo_top.addWidget(self.repoValidBadge, 0, Qt.AlignVCenter)
        repo_top.addStretch(1)
        self.repoNameEdit = LineEdit(self.cloudRepoBox)
        self.repoNameEdit.setMinimumWidth(0)
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.repoNameEdit.textChanged.connect(self._on_repo_name_changed)
        self.repoValidCaption = CaptionLabel("", self.cloudRepoBox)
        self.repoValidCaption.setWordWrap(True)
        repo_grid.addLayout(repo_top, 0, 0)
        repo_grid.addWidget(self.repoNameEdit, 1, 0)
        repo_grid.addWidget(self.repoValidCaption, 2, 0)
        repo_box_lay.addLayout(repo_grid)

        # Private repo switch
        priv_row = QHBoxLayout()
        priv_row.setSpacing(10)
        self.privateRepoLabel = BodyLabel(tr("cloud.private_repo"), self.cloudRepoBox)
        self.privateRepoLabel.setWordWrap(True)
        self.privateRepoSwitch = SwitchButton(self.cloudRepoBox)
        self.privateRepoSwitch.checkedChanged.connect(self._on_private_repo_changed)
        priv_row.addWidget(self.privateRepoLabel, 1, Qt.AlignVCenter)
        priv_row.addWidget(self.privateRepoSwitch, 0, Qt.AlignVCenter)
        repo_box_lay.addLayout(priv_row)

        # Detected Bot Repositories panel (borderless flat list separated by hairline)
        self.botReposPanel = QWidget(self.cloudRepoBox)
        self.botReposPanel.setObjectName("botReposPanel")
        bot_panel_lay = QVBoxLayout(self.botReposPanel)
        bot_panel_lay.setContentsMargins(0, 2, 0, 0)
        bot_panel_lay.setSpacing(8)
        bot_panel_lay.addWidget(HorizontalSeparator(self.botReposPanel))

        bot_hdr = QHBoxLayout()
        bot_hdr.setSpacing(8)
        self.botReposHeading = StrongBodyLabel(tr("cloud.bot_repos_title"), self.botReposPanel)
        self.botReposSummaryBadge = StatusPill("", "info", self.botReposPanel)
        self.botReposSummaryBadge.hide()
        self.scanBotReposBtn = TransparentToolButton(FIF.SYNC, self.botReposPanel)
        self.scanBotReposBtn.setFixedSize(32, 32)
        self.scanBotReposBtn.setToolTip(tr("cloud.scan_bot_repos_tooltip"))
        self.scanBotReposBtn.clicked.connect(self.scan_bot_repos)
        bot_hdr.addWidget(self.botReposHeading, 0, Qt.AlignVCenter)
        bot_hdr.addWidget(self.botReposSummaryBadge, 0, Qt.AlignVCenter)
        bot_hdr.addStretch(1)
        bot_hdr.addWidget(self.scanBotReposBtn, 0, Qt.AlignVCenter)
        bot_panel_lay.addLayout(bot_hdr)

        self.duplicateBotBanner = QFrame(self.botReposPanel)
        self.duplicateBotBanner.setObjectName("duplicateBotBanner")
        dup_lay = QHBoxLayout(self.duplicateBotBanner)
        dup_lay.setContentsMargins(10, 7, 10, 7)
        dup_lay.setSpacing(10)
        self.duplicateBotLabel = CaptionLabel("", self.duplicateBotBanner)
        self.duplicateBotLabel.setWordWrap(True)
        self.pauseOtherBotsBtn = PushButton(
            FIF.PAUSE, escape_qt_mnemonic(tr("cloud.pause_others_btn")), self.duplicateBotBanner
        )
        self.pauseOtherBotsBtn.setFixedHeight(30)
        self.pauseOtherBotsBtn.clicked.connect(self._pause_other_bot_repos)
        dup_lay.addWidget(self.duplicateBotLabel, 1, Qt.AlignVCenter)
        dup_lay.addWidget(self.pauseOtherBotsBtn, 0, Qt.AlignVCenter)
        self.duplicateBotBanner.hide()
        bot_panel_lay.addWidget(self.duplicateBotBanner)

        self.botReposEmptyLabel = CaptionLabel(tr("cloud.bot_repos_none"), self.botReposPanel)
        self.botReposEmptyLabel.setWordWrap(True)
        self.botReposEmptyLabel.hide()
        bot_panel_lay.addWidget(self.botReposEmptyLabel)

        self.botReposRowsBox = QWidget(self.botReposPanel)
        self.botReposRowsLayout = QVBoxLayout(self.botReposRowsBox)
        self.botReposRowsLayout.setContentsMargins(0, 0, 0, 0)
        self.botReposRowsLayout.setSpacing(6)
        bot_panel_lay.addWidget(self.botReposRowsBox)

        self.botReposPanel.hide()
        repo_box_lay.addWidget(self.botReposPanel)

        # Progressive Disclosure Drawer: Sync-Only & PAT
        adv_repo_toggle_row = QHBoxLayout()
        adv_repo_toggle_row.setContentsMargins(0, 0, 0, 0)
        adv_repo_toggle_row.setSpacing(6)
        self.cloudAdvancedToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT,
            escape_qt_mnemonic(tr("cloud.repo_advanced_toggle")),
            self.cloudRepoBox,
        )
        self.cloudAdvancedToggleBtn.setMinimumWidth(0)
        self.cloudAdvancedToggleBtn.clicked.connect(self._toggle_cloud_advanced_drawer)
        adv_repo_toggle_row.addWidget(self.cloudAdvancedToggleBtn)
        adv_repo_toggle_row.addStretch(1)
        repo_box_lay.addLayout(adv_repo_toggle_row)

        self.cloudAdvancedContainer = QWidget(self.cloudRepoBox)
        adv_lay = QVBoxLayout(self.cloudAdvancedContainer)
        adv_lay.setContentsMargins(0, 4, 0, 0)
        adv_lay.setSpacing(8)

        # Sync-only checkbox (for existing repositories)
        sync_only_box = QVBoxLayout()
        sync_only_box.setContentsMargins(0, 0, 0, 0)
        sync_only_box.setSpacing(2)
        self.syncOnlyCheck = CheckBox(
            escape_qt_mnemonic(tr("cloud.sync_only")), self.cloudAdvancedContainer
        )
        self.syncOnlyHint = CaptionLabel(tr("cloud.sync_only_hint"), self.cloudAdvancedContainer)
        self.syncOnlyHint.setWordWrap(True)
        sync_only_box.addWidget(self.syncOnlyCheck)
        sync_only_box.addWidget(self.syncOnlyHint)
        adv_lay.addLayout(sync_only_box)

        # Collapsible PAT fallback
        pat_toggle_row = QHBoxLayout()
        pat_toggle_row.setContentsMargins(0, 0, 0, 0)
        pat_toggle_row.setSpacing(6)
        self.patToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT, escape_qt_mnemonic(tr("cloud.pat_toggle")), self.cloudAdvancedContainer
        )
        self.patToggleBtn.setMinimumWidth(0)
        self.patToggleBtn.clicked.connect(self._toggle_pat_drawer)
        pat_toggle_row.addWidget(self.patToggleBtn)
        pat_toggle_row.addStretch(1)
        adv_lay.addLayout(pat_toggle_row)

        self.patContainer = QWidget(self.cloudAdvancedContainer)
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
        adv_lay.addWidget(self.patContainer)

        self.cloudAdvancedContainer.hide()
        repo_box_lay.addWidget(self.cloudAdvancedContainer)
        c_lay.addWidget(self.cloudRepoBox)
        c_lay.addWidget(HorizontalSeparator(self.cloudSection))

        # Group 2: Daily Cloud Schedule (borderless flat container)
        self.cloudScheduleBox = QWidget(self.cloudSection)
        self.cloudScheduleBox.setObjectName("cloudScheduleBox")
        sched_lay = QVBoxLayout(self.cloudScheduleBox)
        sched_lay.setContentsMargins(0, 0, 0, 0)
        sched_lay.setSpacing(10)

        tz_short = self._active_tz_info()["short"]
        self.cloudTimeLabel = StrongBodyLabel(
            tr("cloud.section_schedule", tz=tz_short), self.cloudScheduleBox
        )
        self.cloudTimeLabel.setWordWrap(True)
        sched_lay.addWidget(self.cloudTimeLabel)

        cloud_picker_row = QHBoxLayout()
        cloud_picker_row.setContentsMargins(0, 0, 0, 0)
        cloud_picker_row.setSpacing(8)
        self.cloudTimePicker = TimePicker(self.cloudScheduleBox)
        self.cloudTimePicker.setTime(QTime(8, 15))
        self.addCloudTimeBtn = PushButton(
            FIF.ADD, escape_qt_mnemonic(tr("schedule.add_time")), self.cloudScheduleBox
        )
        self.removeCloudTimeBtn = PushButton(
            FIF.REMOVE, escape_qt_mnemonic(tr("common.remove")), self.cloudScheduleBox
        )
        self.removeCloudTimeBtn.hide()
        self.addCloudTimeBtn.clicked.connect(self._add_cloud_time)
        self.removeCloudTimeBtn.clicked.connect(self._remove_selected_cloud_time)
        cloud_picker_row.addWidget(self.cloudTimePicker, 0, Qt.AlignVCenter)
        cloud_picker_row.addWidget(self.addCloudTimeBtn, 0, Qt.AlignVCenter)
        cloud_picker_row.addStretch(1)
        sched_lay.addLayout(cloud_picker_row)

        self.cloudTimesChips = ChipFlowWidget(
            empty_text=tr("schedule.times_empty"),
            icon=FIF.DATE_TIME,
            parent=self.cloudScheduleBox,
        )
        self.cloudTimesChips.set_remove_tooltip(tr("common.remove"))
        self.cloudTimesChips.item_removed.connect(self._remove_cloud_time_by_key)
        sched_lay.addWidget(self.cloudTimesChips)

        # Hidden backing ListWidget kept synced for backward test compatibility
        self.cloudTimesList = ListWidget(self.cloudScheduleBox)
        self.cloudTimesList.hide()

        presets_flow = FlowLayout(isTight=True)
        presets_flow.setContentsMargins(0, 0, 0, 0)
        presets_flow.setHorizontalSpacing(8)
        presets_flow.setVerticalSpacing(6)
        self.quickPresetsLabel = CaptionLabel(tr("schedule.quick_presets"), self.cloudScheduleBox)
        self.quickPresetsLabel.setFixedHeight(32)
        self.quickPresetsLabel.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.preset1Btn = PushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_1")), self.cloudScheduleBox
        )
        self.preset2Btn = PushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_2")), self.cloudScheduleBox
        )
        self.preset3Btn = PushButton(
            escape_qt_mnemonic(tr("schedule.preset_offpeak_3")), self.cloudScheduleBox
        )
        self.preset1Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15"]))
        self.preset2Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15", "20:15"]))
        self.preset3Btn.clicked.connect(lambda: self._apply_cloud_preset(["08:15", "14:15", "20:15"]))
        presets_flow.addWidget(self.quickPresetsLabel)
        presets_flow.addWidget(self.preset1Btn)
        presets_flow.addWidget(self.preset2Btn)
        presets_flow.addWidget(self.preset3Btn)
        sched_lay.addLayout(presets_flow)

        self.cronSummaryLabel = CaptionLabel("", self.cloudScheduleBox)
        self.cronSummaryLabel.setWordWrap(True)
        sched_lay.addWidget(self.cronSummaryLabel)

        self.cloudDelayNote = CaptionLabel(tr("schedule.cloud_delay_note"), self.cloudScheduleBox)
        self.cloudDelayNote.setWordWrap(True)
        sched_lay.addWidget(self.cloudDelayNote)

        raw_cron_toggle_row = QHBoxLayout()
        raw_cron_toggle_row.setContentsMargins(0, 0, 0, 0)
        raw_cron_toggle_row.setSpacing(6)
        self.rawCronToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT, escape_qt_mnemonic(tr("schedule.raw_cron_toggle")), self.cloudScheduleBox
        )
        self.rawCronToggleBtn.setMinimumWidth(0)
        self.rawCronToggleBtn.clicked.connect(self._toggle_raw_cron_drawer)
        raw_cron_toggle_row.addWidget(self.rawCronToggleBtn)
        raw_cron_toggle_row.addStretch(1)
        sched_lay.addLayout(raw_cron_toggle_row)

        self.rawCronContainer = QWidget(self.cloudScheduleBox)
        rc_lay = QVBoxLayout(self.rawCronContainer)
        rc_lay.setContentsMargins(0, 0, 0, 0)
        rc_lay.setSpacing(6)
        self.cronLabel = CaptionLabel(tr("schedule.custom_cron"), self.rawCronContainer)
        self.cronLabel.setWordWrap(True)
        self.customCronEdit = LineEdit(self.rawCronContainer)
        self.customCronEdit.setMinimumWidth(0)
        self.customCronEdit.setPlaceholderText("0 1 * * *")
        self.customCronEdit.textChanged.connect(self._on_custom_cron_changed)
        rc_lay.addWidget(self.cronLabel)
        rc_lay.addWidget(self.customCronEdit)
        self.rawCronContainer.hide()
        sched_lay.addWidget(self.rawCronContainer)

        c_lay.addWidget(self.cloudScheduleBox)
        c_lay.addWidget(HorizontalSeparator(self.cloudSection))

        # Group 3: Deploy Action Footer (status left, primary CTA flush right)
        deploy_row = QHBoxLayout()
        deploy_row.setSpacing(12)
        self.deployStatusLabel = CaptionLabel("", self.cloudSection)
        self.deployStatusLabel.setWordWrap(True)
        self.deployStatusLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.deployBtn = PrimaryPushButton(
            FIF.CLOUD, escape_qt_mnemonic(tr("schedule.deploy_btn")), self.cloudSection
        )
        self.deployBtn.clicked.connect(self._deploy_to_github)
        deploy_row.addWidget(self.deployStatusLabel, 1, Qt.AlignVCenter)
        deploy_row.addWidget(self.deployBtn, 0, Qt.AlignVCenter)
        c_lay.addLayout(deploy_row)

        self._apply_cloud_subbox_styles()
        try:
            qconfig.themeChanged.connect(self._apply_cloud_subbox_styles)
        except Exception:
            pass

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

            tz_id = self._active_tz_id()
            idx_tz = self.timezoneCombo.findData(tz_id)
            self.timezoneCombo.setCurrentIndex(idx_tz if idx_tz >= 0 else 0)
            tz_short = self._active_tz_info()["short"]
            self.localTimesLabel.setText(tr("schedule.local_times_tz", tz=tz_short))
            self.cloudTimeLabel.setText(tr("cloud.section_schedule", tz=tz_short))

            cloud_times = list(getattr(st, "cloud_times", None) or ["08:00"])
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
                self._set_raw_cron_expanded(True)
            else:
                try:
                    self.customCronEdit.setText("\n".join(times_to_utc_crons(cloud_times, tz_id)))
                except Exception:
                    self.customCronEdit.setText(raw_cron)
                self._set_raw_cron_expanded(False)

            self._reload_cloud_times_list()
            self._update_cron_summary()

            self._refresh_github_badge()
        finally:
            self._loading = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loading:
            self._sync_timezone_views()

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
        clean = text.strip()
        if clean != self._verified_video_url:
            self._video_verify_state = "unverified"
            self._verified_video_author = ""
            self._video_verify_error = ""
        if not self._loading:
            self._state.video_url = clean
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

        cur_url = self.videoUrlEdit.text().strip()
        res = validate_video_url(cur_url, resolve_short=False)
        if not res["valid"]:
            self.videoBadge.setState("warning", tr("common.warning"))
            self.videoHintLabel.setText(res.get("error") or "")
            return

        if cur_url == self._verified_video_url:
            if self._video_verify_state == "available":
                badge_txt = (
                    tr("content.video_verified_author", author=self._verified_video_author)
                    if self._verified_video_author
                    else tr("content.video_valid", aweme_id=res["aweme_id"])
                )
                self.videoBadge.setState("success", badge_txt)
                self.videoHintLabel.setText("")
                return
            if self._video_verify_state in ("unavailable", "private", "share_disabled"):
                self.videoBadge.setState("error", tr("content.video_unavailable"))
                self.videoHintLabel.setText(self._video_verify_error)
                return
            if self._video_verify_state == "offline_warning":
                self.videoBadge.setState("warning", tr("content.video_verify_offline"))
                self.videoHintLabel.setText(self._video_verify_error)
                return

        self.videoBadge.setState("neutral", tr("content.video_unverified"))
        self.videoHintLabel.setText("")

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
            self._video_verify_state = "available"
            self._verified_video_url = clean_url
            self._verified_video_author = author
            self._video_verify_error = ""
            if clean_url and self.videoUrlEdit.text().strip() != clean_url:
                self.videoUrlEdit.setText(clean_url)
            else:
                self._sync_video_controls()
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

    def _active_tz_info(self) -> dict[str, Any]:
        return get_timezone_info(getattr(self._state, "timezone", None))

    def _active_tz_id(self) -> str:
        return str(self._active_tz_info()["id"])

    def _active_cloud_times(self) -> list[str]:
        raw = getattr(self._state, "cloud_times", None)
        if isinstance(raw, list) and raw:
            return list(raw)
        return ["08:00"]

    def _sync_timezone_views(self) -> None:
        tz_info = self._active_tz_info()
        tz_id = str(tz_info["id"])
        tz_short = str(tz_info["short"])
        prev_loading = self._loading
        self._loading = True
        try:
            idx_tz = self.timezoneCombo.findData(tz_id)
            if idx_tz >= 0 and self.timezoneCombo.currentIndex() != idx_tz:
                self.timezoneCombo.setCurrentIndex(idx_tz)
            self.localTimesLabel.setText(tr("schedule.local_times_tz", tz=tz_short))
            self.cloudTimeLabel.setText(tr("cloud.section_schedule", tz=tz_short))
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

    def _on_timezone_changed(self, _idx: int) -> None:
        if self._loading:
            return
        tz_id = str(self.timezoneCombo.currentData() or "Asia/Jakarta")
        self._state.timezone = tz_id
        if not self._custom_cron_dirty:
            try:
                self._state.cloud_cron = "\n".join(times_to_utc_crons(self._active_cloud_times(), tz_id))
            except Exception:
                pass
        self._sync_timezone_views()
        self.state_changed.emit()

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
                tr("schedule.local_times"),
                duration=2500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            return
        self._state.local_times = [t for t in self._state.local_times if t != target]
        self._reload_times_list()
        self.state_changed.emit()

    def _remove_selected_time(self) -> None:
        row = self.timesList.currentRow()
        if row < 0 and self.timesList.count() > 0:
            row = self.timesList.count() - 1
        if 0 <= row < len(self._state.local_times):
            self._remove_local_time_by_key(self._state.local_times[row])

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
    def _apply_cloud_subbox_styles(self, _theme=None) -> None:
        if isDarkTheme():
            muted_fg = "#9a9a9a"
            warn_bg = "rgba(252, 225, 0, 0.07)"
            warn_border = "rgba(252, 225, 0, 0.24)"
            warn_fg = "#fce100"
            valid_fg = "#6ccb5f"
        else:
            muted_fg = "#616161"
            warn_bg = "rgba(157, 93, 0, 0.06)"
            warn_border = "rgba(157, 93, 0, 0.24)"
            warn_fg = "#9d5d00"
            valid_fg = "#0f7b0f"

        self.duplicateBotBanner.setStyleSheet(
            f"QFrame#duplicateBotBanner {{"
            f" background-color: {warn_bg};"
            f" border: 1px solid {warn_border};"
            f" border-radius: 6px;"
            f"}}"
        )
        self.duplicateBotLabel.setStyleSheet(f"color: {warn_fg}; background: transparent;")
        self.cloudDelayNote.setStyleSheet(f"color: {muted_fg}; background: transparent;")
        self.repoValidCaption.setStyleSheet(f"color: {valid_fg}; background: transparent;")
        cur_parsed = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        cur_full = str(cur_parsed.get("full_name") or "").strip().lower()
        for row in self._bot_repo_row_widgets:
            if isinstance(row, _BotRepoRow):
                fn = str(row.repo_item.get("full_name") or "").strip().lower()
                row.apply_theme(bool(cur_full and cur_full == fn))
                row.radioDot.update()
                row.statusDot.update()

    def _set_cloud_advanced_expanded(self, expanded: bool) -> None:
        self._cloud_advanced_expanded = bool(expanded)
        self.cloudAdvancedContainer.setVisible(self._cloud_advanced_expanded)
        self.cloudAdvancedToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._cloud_advanced_expanded else FIF.CHEVRON_RIGHT
        )

    def _toggle_cloud_advanced_drawer(self) -> None:
        self._set_cloud_advanced_expanded(not self._cloud_advanced_expanded)

    def _set_pat_expanded(self, expanded: bool) -> None:
        self._pat_expanded = bool(expanded)
        if self._pat_expanded and not self._cloud_advanced_expanded:
            self._set_cloud_advanced_expanded(True)
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

    def _sign_out_github(self) -> None:
        self._secrets.delete_github_token()
        self._state.github_login = ""
        self._bot_scan_done_for_token = ""
        self._discovered_bot_repos = []
        self._discovered_all_repos = {}
        self._render_bot_repos_panel()
        self.cloud_repos_updated.emit([])
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
        if self._discovered_bot_repos:
            self._render_bot_repos_panel()

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
                self.repoValidBadge.setState("success", tr("content.repo_valid"))
                self.repoValidBadge.hide()
                self.repoValidCaption.setText(f"✓ {tr('cloud.repo_status_existing_bot', state=state_lbl)}")
                self.repoValidCaption.show()
            elif target_full and target_full in self._discovered_all_repos:
                self.repoValidCaption.hide()
                self.repoValidBadge.setState("warning", tr("cloud.repo_status_non_bot"))
                self.repoValidBadge.show()
            elif self._bot_scan_done_for_token and target_full and "/" in target_full:
                self.repoValidBadge.setState("success", tr("content.repo_valid"))
                self.repoValidBadge.hide()
                self.repoValidCaption.setText(f"✓ {tr('cloud.repo_status_new')}")
                self.repoValidCaption.show()
            else:
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

    # -- Bot Repo Discovery & Cloud Schedule Control ---------------------
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

    def _select_bot_repo(self, repo_item: dict[str, Any]) -> None:
        full_name = str(repo_item.get("full_name") or "").strip()
        is_priv = bool(repo_item.get("private", False))
        if full_name:
            self.repoNameEdit.setText(full_name)
        self.privateRepoSwitch.setChecked(is_priv)
        self._render_bot_repos_panel()

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
        active_repos = [r for r in repos if r.get("active")]
        has_dup = len(active_repos) >= 2

        self.botReposSummaryBadge.hide()

        if not repos:
            if self._bot_scan_done_for_token:
                self.botReposEmptyLabel.setText(tr("cloud.bot_repos_none"))
                self.botReposEmptyLabel.show()
            else:
                self.botReposEmptyLabel.hide()
            self.duplicateBotBanner.hide()
            return

        self.botReposEmptyLabel.hide()

        if has_dup:
            names = ", ".join(str(r.get("full_name") or "") for r in active_repos)
            self.duplicateBotLabel.setText(
                tr("cloud.duplicate_warning", count=len(active_repos), repos=names)
            )
            self.pauseOtherBotsBtn.setVisible(True)
            self.duplicateBotBanner.show()
        else:
            self.duplicateBotBanner.hide()

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
        active_repos = [r for r in self._discovered_bot_repos if r.get("active")]
        if len(active_repos) < 2:
            return
        cur_parsed = _parse_repo_input(self.repoNameEdit.text(), default_owner=self._state.github_login)
        cur_full = str(cur_parsed.get("full_name") or "").strip().lower()
        active_names = [str(r.get("full_name") or "").strip() for r in active_repos if r.get("full_name")]
        keep = next((n for n in active_names if n.lower() == cur_full), active_names[0] if active_names else "")
        to_pause = [n for n in active_names if n.lower() != keep.lower()]
        if not to_pause:
            return
        self.pauseOtherBotsBtn.setEnabled(False)
        self.scanBotReposBtn.setEnabled(False)
        self._workers.start_task(
            _pause_multiple_bot_repos_task,
            token,
            to_pause,
            task_key="pause_other_bot_repos",
            on_finished=self._on_other_bots_paused,
            on_error=self._on_other_bots_pause_error,
        )

    def _on_other_bots_paused(self, payload: Any) -> None:
        self.pauseOtherBotsBtn.setEnabled(True)
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
        self.pauseOtherBotsBtn.setEnabled(True)
        self.scanBotReposBtn.setEnabled(True)
        InfoBar.error(
            tr("common.error"),
            err,
            duration=5500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

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
            cleaned = ["08:00"]
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

    def _remove_selected_cloud_time(self) -> None:
        current = self._active_cloud_times()
        row = self.cloudTimesList.currentRow()
        if row < 0 and self.cloudTimesList.count() > 0:
            row = self.cloudTimesList.count() - 1
        if 0 <= row < len(current):
            self._remove_cloud_time_by_key(current[row])

    def _apply_cloud_preset(self, times: list[str]) -> None:
        self._set_cloud_times(times)

    def _set_raw_cron_expanded(self, expanded: bool) -> None:
        self._raw_cron_expanded = bool(expanded)
        self.rawCronContainer.setVisible(self._raw_cron_expanded)
        self.rawCronToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._raw_cron_expanded else FIF.CHEVRON_RIGHT
        )

    def _toggle_raw_cron_drawer(self) -> None:
        self._set_raw_cron_expanded(not self._raw_cron_expanded)

    def _on_custom_cron_changed(self, text: str) -> None:
        if not self._loading and self._raw_cron_expanded:
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
        if self._raw_cron_expanded and self._custom_cron_dirty:
            crons_arg: Any = self.customCronEdit.text().strip() or self._state.cloud_cron
        else:
            try:
                crons_arg = times_to_utc_crons(self._active_cloud_times(), tz_id)
            except Exception:
                crons_arg = self._state.cloud_cron
        summary = format_schedule_summary(crons_arg, tz_id=tz_id, lang=get_language())
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

        active_accounts = (
            list(self._state.accounts)
            if self._state.multi_account
            else ([self._state.accounts[0]] if self._state.accounts else [])
        )
        unready_names: list[str] = []
        for acc in active_accounts:
            cookie = self._secrets.get_account_cookie(acc.id).strip()
            if cookie and not check_cloud_cookie_readiness(cookie).get("cloud_ready", False):
                unready_names.append(acc.name or acc.username or "Main")
        if unready_names:
            box = MessageBox(
                tr("cloud.bare_deploy_title"),
                tr("cloud.bare_deploy_confirm", accounts=", ".join(unready_names)),
                self.window(),
            )
            box.yesButton.setText(tr("common.continue"))
            box.cancelButton.setText(tr("common.cancel"))
            if not box.exec():
                return

        if self._raw_cron_expanded and self._custom_cron_dirty:
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
                cron_list = times_to_utc_crons(self._active_cloud_times(), self._active_tz_id())
                cron_expr = "\n".join(cron_list)
            except Exception as exc:
                InfoBar.warning(
                    tr("common.warning"),
                    str(exc),
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
        target_full_low = str(repo_chk.get("full_name") or "").strip().lower()

        if (
            not sync_only
            and target_full_low
            and target_full_low in self._discovered_all_repos
            and not any(
                str(r.get("full_name") or "").strip().lower() == target_full_low
                for r in self._discovered_bot_repos
            )
        ):
            non_bot_box = MessageBox(
                tr("cloud.non_bot_confirm_title"),
                tr("cloud.non_bot_confirm_body", repo=repo_chk["full_name"] or raw_repo_arg),
                self.window(),
            )
            non_bot_box.yesButton.setText(tr("common.continue"))
            non_bot_box.cancelButton.setText(tr("common.cancel"))
            if not non_bot_box.exec():
                return

        pause_repos: list[str] = []
        other_active = [
            str(r.get("full_name") or "")
            for r in self._discovered_bot_repos
            if r.get("active")
            and str(r.get("full_name") or "").strip().lower() != target_full_low
        ]
        if other_active:
            dup_choice = {"mode": "pause"}
            dup_box = MessageBox(
                tr("cloud.dup_deploy_title"),
                tr(
                    "cloud.dup_deploy_body",
                    repos="\n".join(f"• {name}" for name in other_active),
                    target=repo_chk["full_name"] or raw_repo_arg,
                ),
                self.window(),
            )
            dup_box.yesButton.setText(tr("cloud.dup_deploy_pause_btn"))
            dup_box.cancelButton.setText(tr("common.cancel"))
            keep_btn = PushButton(escape_qt_mnemonic(tr("cloud.dup_deploy_keep_btn")), dup_box)
            keep_btn.clicked.connect(lambda: (dup_choice.__setitem__("mode", "keep"), dup_box.accept()))
            dup_box.buttonLayout.insertWidget(1, keep_btn)
            if not dup_box.exec():
                return
            if dup_choice["mode"] == "pause":
                pause_repos = other_active

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
        self.deployBtn.setEnabled(True)
        full_name = res.get("full_name", "")
        if res.get("login"):
            self._state.github_login = res["login"]
        if not res.get("sync_only"):
            self._state.last_deployed_version = APP_VERSION
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
        self._bot_scan_done_for_token = ""
        self.maybe_auto_scan_bot_repos()

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
        self.timezoneLabel.setText(tr("schedule.timezone"))
        self.modeSegment.items["local"].setText(tr("schedule.mode_local"))
        self.modeSegment.items["cloud"].setText(tr("schedule.mode_cloud"))
        self.modeSegment.items["both"].setText(tr("schedule.mode_both"))
        self.localHeading.setText(tr("dashboard.mode_local"))
        tz_short = self._active_tz_info()["short"]
        self.localTimesLabel.setText(tr("schedule.local_times_tz", tz=tz_short))
        self.localTimesChips.set_empty_text(tr("schedule.times_empty"))
        self.localTimesChips.set_remove_tooltip(tr("common.remove"))
        self.addTimeBtn.setText(escape_qt_mnemonic(tr("schedule.add_time")))
        self.removeTimeBtn.setText(escape_qt_mnemonic(tr("common.remove")))
        win_sync_txt = escape_qt_mnemonic(tr("schedule.sync_windows"))
        self._applyWinPrimaryBtn.setText(win_sync_txt)
        self._applyWinSecondaryBtn.setText(win_sync_txt)
        self.removeWinScheduleBtn.setText(escape_qt_mnemonic(tr("schedule.remove_windows")))
        self.headlessLabel.setText(tr("schedule.local_headless"))
        self.dailyCapLabel.setText(tr("content.daily_cap_label"))
        self.cloudHeading.setText(tr("cloud.title"))
        self.cloudRepoSectionLabel.setText(tr("cloud.section_account_repo"))
        self.cloudAdvancedToggleBtn.setText(escape_qt_mnemonic(tr("cloud.repo_advanced_toggle")))
        self.ghSigninBtn.setText(escape_qt_mnemonic(tr("cloud.signin_device")))
        self.ghSwitchBtn.setText(escape_qt_mnemonic(tr("cloud.reconnect_github")))
        self.ghSignoutBtn.setText(escape_qt_mnemonic(tr("cloud.signout")))
        self.patToggleBtn.setText(escape_qt_mnemonic(tr("cloud.pat_toggle")))
        self.patLabel.setText(tr("cloud.signin_pat"))
        self.patEdit.setPlaceholderText(tr("schedule.pat_placeholder"))
        self.savePatBtn.setText(escape_qt_mnemonic(tr("schedule.save_pat")))
        self.privateRepoLabel.setText(tr("cloud.private_repo"))
        self.botReposHeading.setText(tr("cloud.bot_repos_title"))
        self.scanBotReposBtn.setToolTip(tr("cloud.scan_bot_repos_tooltip"))
        self.pauseOtherBotsBtn.setText(escape_qt_mnemonic(tr("cloud.pause_others_btn")))
        self.repoNameLabel.setText(tr("cloud.repo_name"))
        self.repoNameEdit.setPlaceholderText(tr("cloud.repo_name_hint"))
        self.syncOnlyCheck.setText(escape_qt_mnemonic(tr("cloud.sync_only")))
        self.syncOnlyHint.setText(tr("cloud.sync_only_hint"))
        self.cloudTimeLabel.setText(tr("cloud.section_schedule", tz=tz_short))
        self.cloudTimesChips.set_empty_text(tr("schedule.times_empty"))
        self.cloudTimesChips.set_remove_tooltip(tr("common.remove"))
        self.addCloudTimeBtn.setText(escape_qt_mnemonic(tr("schedule.add_time")))
        self.removeCloudTimeBtn.setText(escape_qt_mnemonic(tr("common.remove")))
        self.quickPresetsLabel.setText(tr("schedule.quick_presets"))
        self.preset1Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_1")))
        self.preset2Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_2")))
        self.preset3Btn.setText(escape_qt_mnemonic(tr("schedule.preset_offpeak_3")))
        self.cloudDelayNote.setText(tr("schedule.cloud_delay_note"))
        self.rawCronToggleBtn.setText(escape_qt_mnemonic(tr("schedule.raw_cron_toggle")))
        self.cronLabel.setText(tr("schedule.custom_cron"))
        self.deployBtn.setText(escape_qt_mnemonic(tr("schedule.deploy_btn")))
        self._update_windows_tasks_badge()
        self._sync_video_controls()
        self._sync_photo_preview()
        self._update_repo_badge()
        self._reload_times_list()
        self._reload_cloud_times_list()
        self._update_cron_summary()
        self._refresh_github_badge()
        self._render_bot_repos_panel()


__all__ = ["ContentPage"]
