"""PySide6 + PySide6-Fluent-Widgets desktop application entry point."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
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
from gui.services.updater import CHECK_INTERVAL_SECONDS, check_for_update
from gui.widgets.update_dialog import UpdateDialog
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
    """Main FluentWindow hosting the 5 desktop pages."""

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

        if (
            not self.smoke_test
            and getattr(self.state, "auto_check_updates", True)
            and (time.time() - int(getattr(self.state, "last_update_check_ts", 0) or 0) >= CHECK_INTERVAL_SECONDS)
        ):
            QTimer.singleShot(2000, lambda: self.check_for_app_updates(manual=False))

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
            tr("nav.content_schedule"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["run"] = self.addSubInterface(
            self.run_interface,
            FIF.PLAY,
            tr("nav.run_logs"),
            NavigationItemPosition.TOP,
        )
        self._nav_items["settings"] = self.addSubInterface(
            self.settings_interface,
            FIF.SETTING,
            tr("nav.settings"),
            NavigationItemPosition.BOTTOM,
        )

        # Wire cross-page interactions
        self.home_interface.navigate_requested.connect(self.navigate_to)
        self.home_interface.update_requested.connect(self._open_update_dialog)
        self.home_interface.skip_update_requested.connect(self._skip_update_version)
        self.accounts_interface.state_changed.connect(self._on_state_changed)
        self.content_interface.state_changed.connect(self._on_state_changed)
        self.run_interface.state_changed.connect(self._on_state_changed)
        self.settings_interface.state_changed.connect(self._on_state_changed)

        self.content_interface.cloud_repos_updated.connect(self._on_cloud_repos_updated)
        self.run_interface.cloud_schedule_toggled.connect(self.content_interface.set_repo_workflow_state)

        self.settings_interface.theme_changed.connect(self.set_theme)
        if hasattr(self.settings_interface, "accent_changed"):
            self.settings_interface.accent_changed.connect(self.set_accent_mode)
        self.settings_interface.workspace_reloaded.connect(self._on_workspace_reloaded)
        self.settings_interface.check_updates_requested.connect(lambda: self.check_for_app_updates(manual=True))

        self.accounts_interface.log_emitted.connect(self.run_interface.append_log)
        self.content_interface.log_emitted.connect(self.run_interface.append_log)
        self.stackedWidget.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, _index: int) -> None:
        self.accounts_interface.flush_edits()
        self.content_interface.flush_edits()
        self.settings_interface.flush_edits()
        self.home_interface.refresh_state(self.state)
        self.run_interface.refresh_from_state(self.state)
        if self.stackedWidget.currentWidget() is self.content_interface:
            self.content_interface.maybe_auto_scan_bot_repos()

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
        self.home_interface.set_available_update(info if info.get("has_update") else None)

        latest = str(info.get("latest_version") or "").strip().lstrip("vV")
        skipped = str(getattr(self.state, "skipped_update_version", "") or "").strip().lstrip("vV")
        if info.get("has_update") and latest:
            if manual:
                self._open_update_dialog()
            elif latest != skipped:
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
        dlg = UpdateDialog(
            parent=self,
            update_info=self._latest_update_info,
            worker_controller=self.workers,
            download_dir=self.config_store.config_dir / "updates",
        )
        dlg.exec()
        if dlg.user_skipped_version:
            latest = str(self._latest_update_info.get("latest_version") or "").strip().lstrip("vV")
            self._skip_update_version(latest)

    def _skip_update_version(self, version: str) -> None:
        clean = str(version or "").strip().lstrip("vV")
        if not clean:
            return
        self.state.skipped_update_version = clean
        self.home_interface.set_available_update(self._latest_update_info)
        self._on_state_changed()

    def navigate_to(self, route_key: str) -> None:
        target = self._routes.get(route_key)
        if target is not None:
            self.switchTo(target)

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
            "content": tr("nav.content_schedule"),
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
            self.run_interface,
            self.settings_interface,
        ):
            page.retranslate()

    def _on_state_changed(self) -> None:
        self._dirty = True
        if not self.smoke_test:
            try:
                self.config_store.save(self.state)
            except Exception as exc:
                logger.warning("Failed saving AppState: %s", exc)
        self.home_interface.refresh_state(self.state)
        self.content_interface.sync_headless()
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
        self.run_interface.refresh_from_state(self.state)
        self.settings_interface.refresh_from_state(self.state)
        self.home_interface.refresh_state(self.state)

    def closeEvent(self, event) -> None:
        if not self.smoke_test and (self._dirty or self.config_store.exists()):
            try:
                self.accounts_interface.flush_edits()
                self.content_interface.flush_edits()
                self.settings_interface.flush_edits()
                self.config_store.save(self.state)
            except Exception as exc:
                logger.warning("Failed flushing config on exit: %s", exc)

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
