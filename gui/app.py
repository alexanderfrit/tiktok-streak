"""PySide6 + PySide6-Fluent-Widgets desktop application entry point."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QLocale, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentTranslator,
    FluentWindow,
    InfoBadge,
    InfoBadgePosition,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    SplashScreen,
    SystemThemeListener,
    Theme,
    isDarkTheme,
    setTheme,
    setThemeColor,
)

from gui.pages.accounts_page import AccountsPage
from gui.pages.automation_page import AutomationPage
from gui.pages.content_page import ContentPage
from gui.pages.home_page import HomePage
from gui.pages.run_page import RunPage
from gui.pages.settings_page import SettingsPage
from gui.services.github_api import SECRET_NAMES
from gui.services.i18n import set_language, subscribe, tr, unsubscribe
from gui.services.storage import (
    AccountProfile,
    AppState,
    ConfigStore,
    SecretStore,
)
from gui.services.theme import (
    DEFAULT_TIKTOK_ACCENT,
    create_app_icon,
    get_windows_personalization,
    resolve_accent_color,
)
from gui.services.updater import (
    CHECK_INTERVAL_SECONDS,
    check_for_update,
    launch_windows_self_update,
)
from gui.widgets.update_dialog import UpdateDialog, _download_update_task
from gui.workers import WorkerController

logger = logging.getLogger("tiktok-streak")

BRAND_COLOR = DEFAULT_TIKTOK_ACCENT

_WM_SETTINGCHANGE = 0x001A
_WM_DWMCOLORIZATIONCOLORCHANGED = 0x0320


def apply_theme(theme_name: str | None, accent_mode: str = "system") -> Theme:
    """Apply ``system`` / ``light`` / ``dark`` theme and resolved accent color."""
    low = str(theme_name or "system").strip().lower()
    if low == "light":
        target = Theme.LIGHT
    elif low == "dark":
        target = Theme.DARK
    else:
        target = Theme.AUTO
    accent = resolve_accent_color(accent_mode)
    setTheme(target, save=False)
    setThemeColor(accent, save=False)
    return target


def _resolve_app_icon(accent_mode: str = "system") -> QIcon:
    try:
        accent_hex = resolve_accent_color(accent_mode)
        icon = create_app_icon(accent_hex=accent_hex, dark=isDarkTheme())
        if not icon.isNull():
            return icon
    except Exception:
        pass
    return FIF.SEND.icon()


class MainWindow(FluentWindow):
    """Main FluentWindow hosting the 6 redesigned desktop pages."""

    _language_changed_sig = Signal(str)

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        secret_store: SecretStore | None = None,
        smoke_test: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent=parent)
        self.smoke_test = bool(smoke_test)
        self._dirty = False
        self.config_store = config_store or ConfigStore()
        self.secret_store = secret_store or SecretStore(self.config_store.config_dir)
        self.workers = WorkerController(self)

        self.state: AppState = self.config_store.load()
        if not self.state.accounts:
            self.state.accounts.append(AccountProfile(name="Main"))

        accent_mode = getattr(self.state, "accent_mode", "system")
        set_language(self.state.language)
        apply_theme(self.state.theme, accent_mode)

        self._personalization_snapshot: tuple[str, bool, bool] | None = None
        self._app_icon = _resolve_app_icon(accent_mode)
        self.setWindowIcon(self._app_icon)
        self.setWindowTitle(tr("app.title"))
        self.resize(1080, 760)
        self.setMinimumSize(860, 620)

        if sys.platform == "win32":
            try:
                pers = get_windows_personalization()
                self._personalization_snapshot = (
                    str(pers.get("accent_color", "")),
                    bool(pers.get("dark_mode", False)),
                    bool(pers.get("transparency", True)),
                )
                self.setMicaEffectEnabled(bool(pers.get("transparency", True)))
            except Exception:
                pass

        self.splashScreen: SplashScreen | None = None
        if not self.smoke_test:
            self.splashScreen = SplashScreen(self._app_icon, self)
            self.splashScreen.setIconSize(QSize(96, 96))
            self.show()
            QApplication.processEvents()

        self._nav_items: dict[str, object] = {}
        self._latest_update_info: dict[str, Any] | None = None
        self._startup_update_prompted: bool = False
        self._update_download_state: str = "idle"
        self._update_download_progress: tuple[int, int] = (0, 0)
        self._update_download_error: str = ""
        self._update_cancel_event: threading.Event | None = None
        self._active_update_dialog: UpdateDialog | None = None
        self._update_nav_badge: InfoBadge | None = None
        self._init_pages()

        self.navigationInterface.setExpandWidth(210)
        self.navigationInterface.setMinimumExpandWidth(940)
        if not self.smoke_test and self.width() >= 940:
            self.navigationInterface.expand(useAni=False)

        self._personalization_debounce = QTimer(self)
        self._personalization_debounce.setSingleShot(True)
        self._personalization_debounce.setInterval(80)
        self._personalization_debounce.timeout.connect(self._on_system_personalization_changed)

        self.themeListener = SystemThemeListener(self)
        self.themeListener.systemThemeChanged.connect(self._on_system_personalization_changed)
        if not self.smoke_test:
            self.themeListener.start()

        self._language_changed_sig.connect(self._apply_retranslate)
        subscribe(self._on_i18n_language_changed)

        if self.splashScreen is not None:
            self.splashScreen.finish()

        if self._should_auto_check_updates():
            QTimer.singleShot(1200, lambda: self.check_for_app_updates(manual=False))

    def _init_pages(self) -> None:
        self.home_interface = HomePage(
            state=self.state,
            secret_store=self.secret_store,
            parent=self,
        )
        self.accounts_interface = AccountsPage(
            state=self.state,
            config_store=self.config_store,
            secret_store=self.secret_store,
            worker_controller=self.workers,
            smoke_test=self.smoke_test,
            parent=self,
        )
        self.content_interface = ContentPage(
            state=self.state,
            config_store=self.config_store,
            secret_store=self.secret_store,
            worker_controller=self.workers,
            smoke_test=self.smoke_test,
            parent=self,
        )
        self.automation_interface = AutomationPage(
            state=self.state,
            config_store=self.config_store,
            secret_store=self.secret_store,
            worker_controller=self.workers,
            smoke_test=self.smoke_test,
            parent=self,
        )
        self.run_interface = RunPage(
            state=self.state,
            config_store=self.config_store,
            secret_store=self.secret_store,
            worker_controller=self.workers,
            smoke_test=self.smoke_test,
            parent=self,
        )
        self.settings_interface = SettingsPage(
            state=self.state,
            config_store=self.config_store,
            secret_store=self.secret_store,
            worker_controller=self.workers,
            parent=self,
        )

        self._routes = {
            "home": self.home_interface,
            "accounts": self.accounts_interface,
            "content": self.content_interface,
            "automation": self.automation_interface,
            "run": self.run_interface,
            "settings": self.settings_interface,
        }

        self._nav_items["home"] = self.addSubInterface(
            self.home_interface,
            FIF.HOME,
            tr("nav.dashboard"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["accounts"] = self.addSubInterface(
            self.accounts_interface,
            FIF.PEOPLE,
            tr("nav.accounts"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["content"] = self.addSubInterface(
            self.content_interface,
            FIF.EDIT,
            tr("nav.content"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["automation"] = self.addSubInterface(
            self.automation_interface,
            FIF.CLOUD,
            tr("nav.automation"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["run"] = self.addSubInterface(
            self.run_interface,
            FIF.PLAY,
            tr("nav.run_logs"),
            NavigationItemPosition.TOP,
        )
        update_item = self.navigationInterface.addItem(
            routeKey="update",
            icon=FIF.UPDATE,
            text="",
            onClick=self._open_update_dialog,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self._nav_items["update"] = update_item
        self._update_nav_badge = InfoBadge.attension(
            tr("nav.update_badge_new"),
            parent=update_item.parent(),
            target=update_item,
            position=InfoBadgePosition.NAVIGATION_ITEM,
        )
        update_item.setVisible(False)
        self._update_nav_badge.setVisible(False)

        self._nav_items["settings"] = self.addSubInterface(
            self.settings_interface,
            FIF.SETTING,
            tr("nav.settings"),
            NavigationItemPosition.BOTTOM,
        )

        # Wire cross-page interactions
        self.home_interface.navigate_requested.connect(self.navigate_to)
        self.home_interface.quick_run_requested.connect(self._on_home_quick_run)
        self.content_interface.navigate_requested.connect(self.navigate_to)
        self.automation_interface.navigate_requested.connect(self.navigate_to)

        self.accounts_interface.state_changed.connect(self._on_state_changed)
        self.content_interface.state_changed.connect(self._on_state_changed)
        self.automation_interface.state_changed.connect(self._on_state_changed)
        self.run_interface.state_changed.connect(self._on_state_changed)
        self.settings_interface.state_changed.connect(self._on_state_changed)

        self.automation_interface.cloud_repos_updated.connect(self._on_cloud_repos_updated)
        self.run_interface.cloud_schedule_toggled.connect(
            self.automation_interface.set_repo_workflow_state
        )

        self.settings_interface.theme_changed.connect(self.set_theme)
        if hasattr(self.settings_interface, "accent_changed"):
            self.settings_interface.accent_changed.connect(self.set_accent_mode)
        self.settings_interface.workspace_reloaded.connect(self._on_workspace_reloaded)
        self.settings_interface.github_signed_out.connect(
            self.automation_interface.clear_github_session_ui
        )
        self.settings_interface.check_updates_requested.connect(
            lambda: self.check_for_app_updates(manual=True)
        )

        self.accounts_interface.log_emitted.connect(self.run_interface.append_log)
        self.content_interface.log_emitted.connect(self.run_interface.append_log)
        self.automation_interface.log_emitted.connect(self.run_interface.append_log)
        self.stackedWidget.currentChanged.connect(self._on_tab_changed)

    def _on_home_quick_run(self, kind: str) -> None:
        self.navigate_to("run")
        if kind == "full":
            self.run_interface.start_full_run()
        else:
            self.run_interface.start_smoke_test()

    def _on_tab_changed(self, _index: int) -> None:
        self.accounts_interface.flush_edits()
        self.content_interface.flush_edits()
        self.automation_interface.flush_edits()
        self.settings_interface.flush_edits()
        self.home_interface.refresh_state(self.state)
        self.run_interface.refresh_from_state(self.state)
        cur = self.stackedWidget.currentWidget()
        if cur is self.content_interface:
            self.content_interface.refresh_from_state(self.state)
        elif cur is self.automation_interface:
            self.automation_interface.sync_timezone_display()
            self.automation_interface.refresh_preflight()
            self.automation_interface.maybe_auto_scan_bot_repos()
            self.automation_interface.maybe_refresh_windows_tasks()

    def _on_cloud_repos_updated(self, repos: Any) -> None:
        if not isinstance(repos, list):
            return
        target = self.run_interface._full_repo_name().strip().lower()
        if not target:
            return
        match = next(
            (r for r in repos if isinstance(r, dict) and str(r.get("full_name") or "").strip().lower() == target),
            None,
        )
        if match is not None:
            self.run_interface.set_cloud_workflow_state(
                str(match.get("full_name") or target),
                str(match.get("workflow_state") or ("active" if match.get("active") else "disabled_manually")),
            )

    def _should_auto_check_updates(self, now_ts: float | None = None) -> bool:
        """True on startup when auto-check is enabled and outside the double-launch cooldown."""
        if self.smoke_test or not getattr(self.state, "auto_check_updates", True):
            return False
        last_ts = int(getattr(self.state, "last_update_check_ts", 0) or 0)
        if last_ts <= 0:
            return True
        cur = time.time() if now_ts is None else float(now_ts)
        return cur < last_ts or (cur - last_ts) >= CHECK_INTERVAL_SECONDS

    def check_for_app_updates(self, manual: bool = False) -> None:
        if self.workers.is_running("app_update_check"):
            return
        if manual:
            self.settings_interface.set_update_check_status(checking=True)
        self.workers.start_task(
            check_for_update,
            task_key="app_update_check",
            on_finished=lambda res, m=manual: self._on_update_check_finished(res, manual=m),
            on_error=lambda err, m=manual: self._on_update_check_error(err, manual=m),
        )

    def _sync_sidebar_update_item(self) -> None:
        item = self._nav_items.get("update")
        badge = self._update_nav_badge
        if item is None:
            return
        info = self._latest_update_info or {}
        latest = str(info.get("latest_version") or "").strip().lstrip("vV")
        skipped = str(getattr(self.state, "skipped_update_version", "") or "").strip().lstrip("vV")
        downloading = self._update_download_state in ("downloading", "installing")
        has_upd = bool(info.get("has_update")) and bool(latest)
        show = bool(downloading or (has_upd and latest != skipped))

        if not show:
            item.setVisible(False)
            if badge is not None:
                badge.setVisible(False)
            return

        dl_bytes, total_bytes = self._update_download_progress
        if self._update_download_state == "installing":
            label = tr("nav.update_installing")
            badge_txt = "..."
            icon = FIF.SYNC
        elif self._update_download_state == "downloading":
            if total_bytes > 0:
                pct = max(0, min(100, int((dl_bytes * 100) / total_bytes)))
                label = tr("nav.update_downloading")
                badge_txt = f"{pct}%"
            else:
                label = tr("nav.update_downloading_ind")
                badge_txt = "..."
            icon = FIF.DOWNLOAD
        else:
            label = tr("nav.update_available", version=latest)
            badge_txt = tr("nav.update_badge_new")
            icon = FIF.UPDATE

        if hasattr(item, "setIcon"):
            item.setIcon(icon)
        if hasattr(item, "setText"):
            item.setText(label)
        if hasattr(item, "setToolTip"):
            item.setToolTip(label)
        item.setVisible(True)
        if badge is not None:
            badge.setText(badge_txt)
            badge.adjustSize()
            badge.setVisible(True)

    def _on_update_check_finished(self, info: Any, manual: bool = False) -> None:
        if not isinstance(info, dict):
            info = {"ok": False, "has_update": False}
        if info.get("ok"):
            self.state.last_update_check_ts = int(time.time())
            if not self.smoke_test:
                try:
                    self.config_store.save(self.state)
                except Exception:
                    pass
        if info.get("has_update"):
            self._latest_update_info = dict(info)
        self.settings_interface.set_update_check_status(checking=False, info=info)
        self._sync_sidebar_update_item()

        latest = str(info.get("latest_version") or "").strip().lstrip("vV")
        skipped = str(getattr(self.state, "skipped_update_version", "") or "").strip().lstrip("vV")
        if info.get("has_update") and latest:
            if manual:
                self._open_update_dialog()
            elif latest != skipped:
                if not self.smoke_test and not self._startup_update_prompted:
                    self._startup_update_prompted = True
                    try:
                        self.raise_()
                        self.activateWindow()
                    except Exception:
                        pass
                    self._open_update_dialog()
                else:
                    InfoBar.info(
                        tr("update.banner_title", version=latest),
                        tr("update.banner_desc"),
                        duration=6000,
                        position=InfoBarPosition.TOP_RIGHT,
                        parent=self,
                    )
        elif manual:
            if info.get("ok"):
                InfoBar.success(
                    tr("common.ready"),
                    tr("settings.app_version_uptodate"),
                    duration=3500,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self,
                )
            elif info.get("error"):
                InfoBar.warning(
                    tr("common.warning"),
                    str(info["error"]),
                    duration=4500,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self,
                )

    def _on_update_check_error(self, err: str, manual: bool = False) -> None:
        self.settings_interface.set_update_check_status(
            checking=False, info={"ok": False, "has_update": False, "error": err} if manual else None
        )
        if manual:
            InfoBar.warning(
                tr("common.warning"),
                str(err),
                duration=4500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )

    def _open_update_dialog(self) -> None:
        if not self._latest_update_info or not self._latest_update_info.get("has_update"):
            self.check_for_app_updates(manual=True)
            return
        if self._active_update_dialog is not None:
            try:
                self._active_update_dialog.raise_()
                self._active_update_dialog.activateWindow()
            except Exception:
                pass
            return

        total_bytes = self._update_download_progress[1] or int(
            self._latest_update_info.get("asset_size") or 0
        )
        dlg = UpdateDialog(
            parent=self,
            update_info=self._latest_update_info,
            worker_controller=self.workers,
            download_dir=self.config_store.config_dir / "updates",
            start_download_cb=self.start_app_update_download,
            cancel_download_cb=self.cancel_app_update_download,
            download_state=self._update_download_state,
            downloaded_bytes=self._update_download_progress[0],
            total_bytes=total_bytes,
            download_error=self._update_download_error,
        )
        self._active_update_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._active_update_dialog = None
        if dlg.user_skipped_version:
            latest = str(self._latest_update_info.get("latest_version") or "").strip().lstrip("vV")
            self._skip_update_version(latest)

    def start_app_update_download(self) -> None:
        """Start downloading the update asset in the background while keeping the UI usable."""
        if self._update_download_state in ("downloading", "installing"):
            return
        info = self._latest_update_info or {}
        dl_url = str(info.get("download_url") or "").strip()
        latest_ver = str(info.get("latest_version") or "").strip().lstrip("vV")
        if not dl_url or not latest_ver:
            return

        expected_size = int(info.get("asset_size") or 0)
        self._update_cancel_event = threading.Event()
        self._update_download_state = "downloading"
        self._update_download_error = ""
        self._update_download_progress = (0, expected_size)
        self._sync_sidebar_update_item()
        if self._active_update_dialog is not None:
            self._active_update_dialog.sync_download_state(
                "downloading", downloaded=0, total=expected_size
            )

        dest_path = self.config_store.config_dir / "updates" / f"TikTokStreakSetup-v{latest_ver}.exe"
        self.workers.start_task(
            _download_update_task,
            dl_url,
            str(dest_path),
            str(info.get("asset_sha256") or ""),
            expected_size,
            self._update_cancel_event,
            task_key="app_update_download",
            cancel_event=self._update_cancel_event,
            pass_progress_cb=True,
            on_progress=self._on_update_download_progress,
            on_finished=self._on_update_download_finished,
            on_error=self._on_update_download_error,
        )

    def cancel_app_update_download(self) -> None:
        """Cancel an in-flight background update download and return to idle."""
        if self._update_cancel_event is not None:
            self._update_cancel_event.set()
        self._update_download_state = "idle"
        self._update_download_progress = (0, 0)
        self._update_download_error = ""
        self._sync_sidebar_update_item()
        if self._active_update_dialog is not None:
            self._active_update_dialog.sync_download_state("idle")

    def _on_update_download_progress(self, payload: Any) -> None:
        if self._update_download_state != "downloading" or not isinstance(payload, dict):
            return
        dl = int(payload.get("downloaded") or 0)
        total = int(payload.get("total") or 0)
        self._update_download_progress = (dl, total)
        self._sync_sidebar_update_item()
        if self._active_update_dialog is not None:
            self._active_update_dialog.sync_download_state("downloading", downloaded=dl, total=total)

    def _on_update_download_finished(self, payload: Any) -> None:
        ev = self._update_cancel_event
        if ev is not None and ev.is_set():
            return
        path_str = str((payload or {}).get("path") or "").strip()
        if not path_str:
            self._on_update_download_error("Downloaded file path is missing.")
            return
        self._update_download_state = "installing"
        self._sync_sidebar_update_item()
        if self._active_update_dialog is not None:
            self._active_update_dialog.sync_download_state("installing")
        try:
            self._flush_and_save_state()
            launch_windows_self_update(Path(path_str))
            if self._active_update_dialog is not None:
                self._active_update_dialog.install_launched = True
                self._active_update_dialog.accept()
            self.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception as exc:
            self._on_update_download_error(str(exc))

    def _on_update_download_error(self, err: str) -> None:
        ev = self._update_cancel_event
        if ev is not None and ev.is_set():
            self._update_download_state = "idle"
            self._sync_sidebar_update_item()
            if self._active_update_dialog is not None:
                self._active_update_dialog.sync_download_state("idle")
            return
        self._update_download_state = "error"
        self._update_download_error = str(err)
        self._sync_sidebar_update_item()
        if self._active_update_dialog is not None:
            self._active_update_dialog.sync_download_state("error", error=str(err))
        else:
            InfoBar.warning(
                tr("common.warning"),
                str(err),
                duration=5000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )

    def _skip_update_version(self, version: str) -> None:
        clean = str(version or "").strip().lstrip("vV")
        if not clean:
            return
        self.state.skipped_update_version = clean
        self._sync_sidebar_update_item()
        self._on_state_changed()

    def navigate_to(self, route_key: str) -> None:
        raw = str(route_key or "").strip()
        subtab = ""
        if ":" in raw:
            base, subtab = raw.split(":", 1)
        else:
            base = raw
        target = self._routes.get(base)
        if target is not None:
            self.switchTo(target)
            if base == "settings" and subtab:
                self.settings_interface.select_tab(subtab)

    def _refresh_app_icon(self) -> None:
        accent_mode = getattr(self.state, "accent_mode", "system")
        self._app_icon = _resolve_app_icon(accent_mode)
        self.setWindowIcon(self._app_icon)

    def set_theme(self, theme_name: str) -> None:
        self.state.theme = theme_name
        apply_theme(theme_name, getattr(self.state, "accent_mode", "system"))
        self._refresh_app_icon()

    def set_accent_mode(self, accent_mode: str) -> None:
        mode = str(accent_mode or "system").strip().lower()
        if mode not in ("system", "tiktok", "custom"):
            mode = "system"
        self.state.accent_mode = mode
        apply_theme(self.state.theme, mode)
        self._refresh_app_icon()

    def _on_system_personalization_changed(self) -> None:
        pers = get_windows_personalization()
        snap = (
            str(pers.get("accent_color", "")),
            bool(pers.get("dark_mode", False)),
            bool(pers.get("transparency", True)),
        )
        theme_is_system = str(self.state.theme or "system").strip().lower() in ("system", "auto")
        accent_is_system = str(getattr(self.state, "accent_mode", "system") or "system").strip().lower() == "system"

        if snap == self._personalization_snapshot and not theme_is_system:
            return
        self._personalization_snapshot = snap

        if sys.platform == "win32":
            try:
                self.setMicaEffectEnabled(bool(pers.get("transparency", True)))
            except Exception:
                pass

        if theme_is_system or accent_is_system:
            apply_theme(self.state.theme, getattr(self.state, "accent_mode", "system"))
            self._refresh_app_icon()

    def nativeEvent(self, eventType, message):  # noqa: N802
        if sys.platform == "win32" and message is not None:
            try:
                if bytes(eventType) == b"windows_generic_MSG":
                    msg = ctypes.wintypes.MSG.from_address(int(message))
                    if msg.message in (_WM_DWMCOLORIZATIONCOLORCHANGED, _WM_SETTINGCHANGE):
                        debounce = getattr(self, "_personalization_debounce", None)
                        if debounce is not None:
                            debounce.start()
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _on_i18n_language_changed(self, lang: str) -> None:
        self.state.language = lang
        self._language_changed_sig.emit(lang)

    def _apply_retranslate(self, _lang: str = "") -> None:
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("app.title"))
        nav_labels = {
            "home": tr("nav.dashboard"),
            "accounts": tr("nav.accounts"),
            "content": tr("nav.content"),
            "automation": tr("nav.automation"),
            "run": tr("nav.run_logs"),
            "settings": tr("nav.settings"),
        }
        for key, label in nav_labels.items():
            item = self._nav_items.get(key)
            if item is not None and hasattr(item, "setText"):
                item.setText(label)

        for page in (
            self.home_interface,
            self.accounts_interface,
            self.content_interface,
            self.automation_interface,
            self.run_interface,
            self.settings_interface,
        ):
            page.retranslate()
        self._sync_sidebar_update_item()

    def _on_state_changed(self) -> None:
        self._dirty = True
        if not self.smoke_test:
            try:
                self.config_store.save(self.state)
            except Exception as exc:
                logger.warning("Failed saving AppState: %s", exc)
        self.home_interface.refresh_state(self.state)
        self.automation_interface.sync_headless()
        self.automation_interface.sync_timezone_display()
        self.automation_interface.refresh_preflight()
        self.run_interface.refresh_from_state(self.state)
        self.settings_interface.refresh_from_state(self.state)

    def _on_workspace_reloaded(self, new_state: AppState) -> None:
        self.state = new_state
        self._dirty = True
        if not self.smoke_test:
            try:
                self.config_store.save(self.state)
            except Exception as exc:
                logger.warning("Failed saving reloaded AppState: %s", exc)
        self.accounts_interface.refresh_from_state(self.state)
        self.content_interface.refresh_from_state(self.state)
        self.automation_interface.refresh_from_state(self.state)
        self.run_interface.refresh_from_state(self.state)
        self.settings_interface.refresh_from_state(self.state)
        self.home_interface.refresh_state(self.state)

    def _flush_and_save_state(self) -> None:
        if self.smoke_test or not (self._dirty or self.config_store.exists()):
            return
        try:
            self.accounts_interface.flush_edits()
            self.content_interface.flush_edits()
            self.automation_interface.flush_edits()
            self.settings_interface.flush_edits()
            self.config_store.save(self.state)
        except Exception as exc:
            logger.warning("Failed flushing config on exit: %s", exc)

    def closeEvent(self, event) -> None:
        self._flush_and_save_state()
        if self._update_cancel_event is not None:
            self._update_cancel_event.set()

        unsubscribe(self._on_i18n_language_changed)
        listener = getattr(self, "themeListener", None)
        if listener is not None and listener.isRunning():
            listener.requestInterruption()
            listener.quit()
            if not listener.wait(150):
                listener.terminate()
                listener.wait(500)
        self.workers.shutdown()
        super().closeEvent(event)


def main(
    smoke_test: bool = False,
    config_dir: str | Path | None = None,
) -> int:
    """Create QApplication, apply translator/theme, and start MainWindow."""
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    store = ConfigStore(config_dir)
    secrets = SecretStore(store.config_dir)
    saved = store.load()

    locale = (
        QLocale(QLocale.Indonesian, QLocale.Indonesia)
        if saved.language == "id"
        else QLocale(QLocale.English, QLocale.UnitedStates)
    )
    translator = FluentTranslator(locale, app)
    app.installTranslator(translator)

    window = MainWindow(
        config_store=store,
        secret_store=secrets,
        smoke_test=smoke_test,
    )

    if smoke_test:
        window.show()
        app.processEvents()
        required = (
            window.home_interface,
            window.accounts_interface,
            window.content_interface,
            window.automation_interface,
            window.run_interface,
            window.settings_interface,
        )
        if not all(w is not None and w.objectName() for w in required):
            window.close()
            return 1
        window.close()
        app.processEvents()
        return 0

    window.show()
    return app.exec()


__all__ = [
    "BRAND_COLOR",
    "MainWindow",
    "SECRET_NAMES",
    "apply_theme",
    "main",
]
