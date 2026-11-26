"""Headless PySide6 + Fluent Widgets verification tests for the desktop GUI.

All tests run under QT_QPA_PLATFORM=offscreen with isolated temporary config
directories and mocked SecretStore / network / Selenium / schtasks dependencies
so real user %APPDATA% and Windows Credential Manager are never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTime
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame
from qfluentwidgets import (
    InfoLevel,
    ListWidget,
    NavigationDisplayMode,
    PrimaryPushButton,
    Theme,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
    setTheme,
    themeColor,
)

from gui.app import MainWindow
from gui.pages.content_page import _build_cloud_secrets
from gui.services.github_api import SECRET_NAMES
from gui.services.i18n import i18n, set_language, tr
from gui.services.storage import AccountProfile, AppState, ConfigStore, SecretStore
from gui.services.theme import DEFAULT_TIKTOK_ACCENT, resolve_accent_color
from gui.widgets.cookie_dialog import CookieDialog
from gui.widgets.device_login_dialog import DeviceLoginDialog
from gui.widgets.log_viewer import LogViewer
from gui.widgets.status_pill import StatusPill


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _pump_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    """Process Qt events until `predicate()` is truthy or raise AssertionError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    app.processEvents()
    assert predicate(), f"Timed out after {timeout}s waiting for Qt condition"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate keyring, browser discovery, and Windows Task Scheduler for GUI tests."""
    mem_keyring: dict[tuple[str, str], str] = {}
    fake_keyring = types.SimpleNamespace(
        get_password=lambda svc, key: mem_keyring.get((svc, key)),
        set_password=lambda svc, key, val: mem_keyring.__setitem__((svc, key), val),
        delete_password=lambda svc, key: mem_keyring.pop((svc, key), None),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setattr("gui.pages.accounts_page.list_sources", lambda enrich=True: [])
    monkeypatch.setattr("gui.pages.automation_page.WindowsScheduler.list_tasks", lambda self: [])
    monkeypatch.setattr(
        "gui.pages.content_page.fetch_tiktok_video_preview",
        lambda url, meta=None, **kw: {
            "url": url,
            "cover_bytes": b"",
            "avatar_bytes": b"",
            "author_nickname": "",
        },
    )

    cfg_dir = tmp_path / "cfg"
    store = ConfigStore(cfg_dir)
    secrets = SecretStore(cfg_dir)

    yield store, secrets, mem_keyring, tmp_path

    set_language("en")


@pytest.fixture
def make_window(qapp: QApplication, isolated_env):
    """Factory that builds MainWindow in an isolated temp dir and closes it cleanly."""
    store, secrets, mem_keyring, tmp_path = isolated_env
    created: list[MainWindow] = []

    def _factory(
        state: AppState | None = None,
        smoke_test: bool = True,
    ) -> tuple[MainWindow, ConfigStore, SecretStore, dict, Path]:
        if state is not None:
            store.save(state)
        win = MainWindow(
            config_store=store,
            secret_store=secrets,
            smoke_test=smoke_test,
        )
        created.append(win)
        return win, store, secrets, mem_keyring, tmp_path

    yield _factory

    for win in created:
        try:
            _pump_until(qapp, lambda w=win: w.workers.active_count() == 0, timeout=3.0)
        finally:
            win.close()
            qapp.processEvents()


# ---------------------------------------------------------------------------
# 1. MainWindow, 5 Sub-Interfaces, Retranslate, Themes, Live Paint
# ---------------------------------------------------------------------------

def test_mainwindow_five_pages_paint_retranslate_and_themes(qapp: QApplication, make_window):
    win, _store, _secrets, _mem, _tmp = make_window(smoke_test=False)
    win.show()
    qapp.processEvents()

    pages = [
        win.home_interface,
        win.accounts_interface,
        win.content_interface,
        win.automation_interface,
        win.run_interface,
        win.settings_interface,
    ]
    names = [p.objectName() for p in pages]
    assert names == [
        "home_interface",
        "accounts_interface",
        "content_interface",
        "automation_interface",
        "run_interface",
        "settings_interface",
    ]
    assert len(set(names)) == 6

    # Navigate across all 6 sub-interfaces + Settings Pivot deep links
    for route, expected_widget in (
        ("accounts", win.accounts_interface),
        ("content", win.content_interface),
        ("automation", win.automation_interface),
        ("run", win.run_interface),
        ("settings:accounts", win.settings_interface),
        ("settings:general", win.settings_interface),
        ("home", win.home_interface),
    ):
        win.navigate_to(route)
        qapp.processEvents()
        assert win.stackedWidget.currentWidget() is expected_widget
        if route == "settings:accounts":
            assert win.settings_interface.current_tab() == "accounts"
        elif route == "settings:general":
            assert win.settings_interface.current_tab() == "general"

    # EN -> ID -> EN retranslation via SettingsPage language combo
    idx_id = win.settings_interface.langCombo.findData("id")
    assert idx_id >= 0
    win.settings_interface.langCombo.setCurrentIndex(idx_id)
    qapp.processEvents()

    assert i18n.get_language() == "id"
    assert win.windowTitle() == "Pengaturan TikTok Streak"
    assert win.home_interface.welcomeTitle.text() == "Beranda"
    assert win.accounts_interface.titleLabel.text() == "Akun"
    assert win.accounts_interface.modeSegment.items["single"].text() == "1 Akun"
    assert win.content_interface.titleLabel.text() == "Pesan & Media"
    assert win.automation_interface.titleLabel.text() == "Otomatisasi"
    assert win.run_interface.titleLabel.text() == "Jalankan & Log"
    assert win.run_interface.logViewer.titleLabel.text() == "Log Langsung"
    assert win.settings_interface.titleLabel.text() == "Pengaturan"
    assert win.settings_interface.themeCombo.itemText(0) == "Sistem"
    assert win.settings_interface.securityCard.titleLabel.text() == "Privasi & Keamanan"
    assert "CLI" in win.settings_interface.cliExportCard.card.titleLabel.text()
    assert win.settings_interface.resetTitleLabel.text() == "Reset Data Lokal Aplikasi"

    idx_en = win.settings_interface.langCombo.findData("en")
    assert idx_en >= 0
    win.settings_interface.langCombo.setCurrentIndex(idx_en)
    qapp.processEvents()

    assert i18n.get_language() == "en"
    assert win.windowTitle() == "TikTok Streak Setup"
    assert win.home_interface.welcomeTitle.text() == "Home"
    assert win.accounts_interface.titleLabel.text() == "Accounts"
    assert win.content_interface.titleLabel.text() == "Message & Media"
    assert win.automation_interface.titleLabel.text() == "Automation"
    assert win.run_interface.titleLabel.text() == "Run & Logs"
    assert win.settings_interface.securityCard.titleLabel.text() == "Privacy & Security"
    assert win.settings_interface.configFolderCard.titleLabel.text() == "App Config Folder"
    assert win.settings_interface.cliExportCard.card.titleLabel.text() == "Developer CLI Export"
    assert win.settings_interface.resetTitleLabel.text() == "Reset Local App Data"
    assert win.settings_interface.autoUpdateLabel.text() == "Auto-check on startup"
    assert win.settings_interface.titleLabel.text() == "Settings"
    assert win.settings_interface.themeCombo.itemText(0) == "System"

    # Light -> Dark -> System theme switching via SettingsPage theme combo
    for theme_key, expect_dark in (("light", False), ("dark", True)):
        idx_t = win.settings_interface.themeCombo.findData(theme_key)
        win.settings_interface.themeCombo.setCurrentIndex(idx_t)
        qapp.processEvents()
        assert win.state.theme == theme_key
        assert isDarkTheme() is expect_dark

    idx_sys = win.settings_interface.themeCombo.findData("system")
    win.settings_interface.themeCombo.setCurrentIndex(idx_sys)
    qapp.processEvents()
    assert win.state.theme == "system"

    # Wait for initial mocked background workers before closing
    _pump_until(qapp, lambda: win.workers.active_count() == 0, timeout=3.0)
    win.close()
    qapp.processEvents()
    assert win._on_i18n_language_changed not in i18n._subscribers


# ---------------------------------------------------------------------------
# 2. run_gui.py --smoke-test and --run-bot (no PySide6)
# ---------------------------------------------------------------------------

def test_run_gui_smoke_test_cli(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    cfg_dir = tmp_path / "smoke_cfg"
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"

    proc = subprocess.run(
        [sys.executable, "-B", "run_gui.py", "--smoke-test", "--config-dir", str(cfg_dir)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    # Smoke test must not persist config.json on exit
    assert not (cfg_dir / "config.json").exists()


def test_run_bot_does_not_import_pyside6_and_handles_system32_cwd(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    cfg_dir = tmp_path / "bot_cfg"
    ws_dir = tmp_path / "bot_ws"
    ws_dir.mkdir(parents=True, exist_ok=True)

    script = f"""
import pathlib, sys, types
from unittest.mock import patch

blocked = {{'PySide6', 'qfluentwidgets'}}
class QtBlocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, QtBlocker())

mem = {{}}
sys.modules['keyring'] = types.SimpleNamespace(
    get_password=lambda s, k: mem.get((s, k)),
    set_password=lambda s, k, v: mem.__setitem__((s, k), v),
    delete_password=lambda s, k: mem.pop((s, k), None),
)

import run_gui
from gui.services.storage import AccountProfile, AppState, ConfigStore, SecretStore

cfg_dir = pathlib.Path({str(cfg_dir)!r})
ws_dir = pathlib.Path({str(ws_dir)!r})
store = ConfigStore(cfg_dir)
secrets = SecretStore(cfg_dir)
acc = AccountProfile(id='acc_1', name='Main', friends=['alice', 'bob'])
state = AppState(
    accounts=[acc],
    video_enabled=False,
    photo_enabled=False,
    workspace_dir=str(ws_dir),
)
store.save(state)
secrets.set_account_cookie('acc_1', 'sessionid=bot_cookie_123')

seen = {{}}
def fake_run(accounts_override=None, headless=None, only_account=None, only_friend=None, stop_event=None, progress_cb=None):
    seen['cwd'] = pathlib.Path.cwd()
    seen['headless'] = headless
    seen['only_friend'] = only_friend
    seen['accounts'] = [a.name for a in (accounts_override or [])]
    return {{'ok': True, 'results': [{{'name': 'Main', 'sent': 1}}], 'elapsed': 1, 'critical_failure': False}}

# Simulate launching from Windows System32 CWD
with patch('run_gui._is_system_dir', return_value=True), \\
     patch('main.run', new=fake_run):
    rc = run_gui.main(['--run-bot', '--config-dir', str(cfg_dir), '--headful', '--only-friend', 'alice'])

assert rc == 0, f'Expected exit 0, got {{rc}}'
assert 'PySide6' not in sys.modules
assert 'qfluentwidgets' not in sys.modules
assert seen['cwd'].resolve() == ws_dir.resolve()
assert seen['headless'] is False
assert seen['only_friend'] == 'alice'
assert seen['accounts'] == ['Main']
assert (cfg_dir / 'scheduled_run.log').is_file()
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_run_bot_manual_env_fallback_without_pyside6(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    cfg_dir = tmp_path / "manual_cfg"
    ws_dir = tmp_path / "manual_ws"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / ".env").write_text('TIKTOK_SESSION_ID="manual_sid"\nFRIENDS_LIST="bob"\n', encoding="utf-8")

    script = f"""
import pathlib, sys, types
from unittest.mock import patch

blocked = {{'PySide6', 'qfluentwidgets'}}
class QtBlocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, QtBlocker())

mem = {{}}
sys.modules['keyring'] = types.SimpleNamespace(
    get_password=lambda s, k: mem.get((s, k)),
    set_password=lambda s, k, v: mem.__setitem__((s, k), v),
    delete_password=lambda s, k: mem.pop((s, k), None),
)

import run_gui
from gui.services.storage import AppState, ConfigStore

cfg_dir = pathlib.Path({str(cfg_dir)!r})
ws_dir = pathlib.Path({str(ws_dir)!r})
ConfigStore(cfg_dir).save(AppState(accounts=[], workspace_dir=str(ws_dir)))

called = {{}}
def fake_cli_main(argv=None):
    called['argv'] = list(argv or [])
    called['cwd'] = pathlib.Path.cwd()

with patch('main.cli_main', side_effect=fake_cli_main):
    rc = run_gui.main(['--run-bot', '--config-dir', str(cfg_dir), '--headful', '--only-friend', 'bob'])

assert rc == 0
assert 'PySide6' not in sys.modules
assert called['cwd'].resolve() == ws_dir.resolve()
assert called['argv'] == ['--headful', '--only-friend', 'bob']
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


# ---------------------------------------------------------------------------
# 3. AccountsPage: Single <-> Multi Toggle, v20 UX, Guided Login, CookieDialog
# ---------------------------------------------------------------------------

def test_single_multi_account_toggle_preserves_account_one_and_extras(qapp: QApplication, make_window):
    acc1 = AccountProfile(
        id="acc_1",
        name="Primary",
        friends=["alice", "bob"],
        username="primary_user",
        inbox_verified=True,
        auth_method="guided (chrome)",
    )
    state = AppState(multi_account=False, accounts=[acc1])
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("acc_1", "sessionid=primary_cookie_value")
    win.accounts_interface.refresh_from_state()
    qapp.processEvents()

    ap = win.accounts_interface
    assert len(ap._cards) == 1
    assert ap.addAccountBtn.isHidden() is True
    assert ap._cards[0].overridesContainer.isHidden() is True

    # Switch Single -> Multi
    ap.modeSegment.setCurrentItem("multi")
    ap._on_mode_changed("multi")
    qapp.processEvents()
    assert win.state.multi_account is True
    assert ap.addAccountBtn.isHidden() is False
    assert len(ap._cards) == 1
    assert ap._cards[0].profile.id == "acc_1"
    assert ap._cards[0].profile.friends == ["alice", "bob"]
    assert ap._cards[0].overridesContainer.isHidden() is False

    # Add Account #2 and populate it
    ap.addAccountBtn.click()
    qapp.processEvents()
    assert len(win.state.accounts) == 2
    assert len(ap._cards) == 2
    acc2 = win.state.accounts[1]
    secrets.set_account_cookie(acc2.id, "sessionid=second_cookie_value")
    ap._cards[1].nameEdit.setText("AltAccount")
    ap._cards[1].friendsEdit.setPlainText("@carol\ndave")
    ap._cards[1].flush_edits()
    qapp.processEvents()
    assert acc2.name == "AltAccount"
    assert acc2.friends == ["carol", "dave"]

    # Switch Multi -> Single: Account #1 stays active, Account #2 stays preserved
    ap._on_mode_changed("single")
    qapp.processEvents()
    assert win.state.multi_account is False
    assert len(ap._cards) == 1
    assert ap._cards[0].profile.id == "acc_1"
    assert ap._cards[0].profile.friends == ["alice", "bob"]
    assert secrets.get_account_cookie("acc_1") == "sessionid=primary_cookie_value"
    assert secrets.get_account_cookie(acc2.id) == "sessionid=second_cookie_value"
    assert ap.multiHiddenHint.isHidden() is False
    assert "1" in ap.multiHiddenHint.text()

    # Switch back Single -> Multi: Account #2 returns intact
    ap._on_mode_changed("multi")
    qapp.processEvents()
    assert len(ap._cards) == 2
    assert ap._cards[1].nameEdit.text() == "AltAccount"
    assert ap._cards[1].profile.friends == ["carol", "dave"]

    # Removing Account #2 removes it from state and deletes its secret
    ap._remove_account(acc2.id)
    qapp.processEvents()
    assert len(win.state.accounts) == 1
    assert secrets.get_account_cookie(acc2.id) == ""


def test_accounts_page_v20_ux_and_guided_login(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch):
    from gui.widgets.browser_import_dialog import BrowserImportDialog

    win, _store, secrets, _mem, _tmp = make_window(smoke_test=True)
    ap = win.accounts_interface

    v20_src = {
        "id": "chromium::chrome::Default",
        "label": "Google Chrome — Default",
        "browser_label": "Google Chrome",
        "kind": "chromium",
        "cookies_path": "C:/Fake/Chrome/Cookies",
        "user_data_dir": "C:/Fake/Chrome",
        "has_tiktok": 2,
        "v20_blocked": True,
        "locked": False,
    }
    ap._on_browsers_scanned([v20_src])
    qapp.processEvents()

    dlg = BrowserImportDialog(win, account_name="Main", sources=ap._sources, scan_completed=True)
    assert "[v20]" in dlg.browserCombo.currentText()
    assert dlg.browserBadge.text() == tr("accounts.v20_badge")
    assert dlg.v20Banner.isHidden() is False
    assert dlg.yesButton.isEnabled() is False
    assert dlg.validate() is False
    dlg.useBrowserLoginBtn.click()
    assert dlg.switch_to_browser_login is True
    dlg.close()

    # Cancelling or closing BrowserImportDialog never calls read_source
    monkeypatch.setattr(BrowserImportDialog, "exec", lambda self: 0)
    with patch("gui.pages.accounts_page.read_source") as mock_read:
        ap._start_import_browser(win.state.accounts[0].id)
        qapp.processEvents()
        mock_read.assert_not_called()

    # Guided login success stores cookie & sets inbox_verified=True
    def fake_guided_ok(profile_dir, browser_kind="auto", cancel_event=None, status_cb=None):
        if status_cb:
            status_cb("Checking /messages inbox availability...")
        return {
            "ok": True,
            "sessionid": "guided_sid",
            "cookie_text": "sessionid=guided_sid; ttwid=abc",
            "username": "guided_user",
            "inbox_verified": True,
            "business_suite": False,
            "browser": "chrome",
        }

    monkeypatch.setattr("gui.pages.accounts_page.guided_browser_login", fake_guided_ok)
    ap._start_guided_login(win.state.accounts[0].id)
    _pump_until(qapp, lambda: not win.workers.is_running("guided_login"), timeout=4.0)
    qapp.processEvents()

    acc = win.state.accounts[0]
    assert acc.username == "guided_user"
    assert acc.inbox_verified is True
    assert secrets.get_account_cookie(acc.id) == "sessionid=guided_sid; ttwid=abc"
    assert ap._cards[0].inboxBadge.text() == tr("accounts.local_only_badge")


def test_cookie_dialog_validate_and_paste_flow(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch):
    win, _store, secrets, _mem, _tmp = make_window(smoke_test=True)
    dlg = CookieDialog(win)

    # Empty input fails validate()
    dlg.textEdit.setPlainText("   ")
    assert dlg.validate() is False
    assert dlg.errorLabel.isHidden() is False

    # JSON object lacking sessionid/sessionid_ss/sid_tt fails validate()
    dlg.textEdit.setPlainText('{"unrelated_cookie": "value123"}')
    assert dlg.validate() is False

    # Valid Cookie header passes validate() and masks preview
    dlg.textEdit.setPlainText("Cookie: sessionid=abcd1234efgh5678; ttwid=xyz999")
    qapp.processEvents()
    assert "abcd...5678" in dlg.previewBadge.text()
    assert "abcd1234efgh5678" not in dlg.previewBadge.text()
    assert dlg.validate() is True
    assert dlg.cookie_payload == "sessionid=abcd1234efgh5678; ttwid=xyz999"
    dlg.close()

    # AccountsPage._open_paste_dialog integration with mocked CookieDialog.exec + API check
    monkeypatch.setattr(CookieDialog, "exec", lambda self: (
        self.textEdit.setPlainText("sessionid=pasted_sid_999"),
        self.validate(),
        True,
    )[-1])
    monkeypatch.setattr(
        "gui.pages.accounts_page._check_pasted_cookie",
        lambda payload: {"ok": True, "username": "pasted_alice"},
    )
    acc_id = win.state.accounts[0].id
    win.accounts_interface._open_paste_dialog(acc_id)
    _pump_until(qapp, lambda: not win.workers.is_running(f"check_cookie_{acc_id}"), timeout=4.0)
    qapp.processEvents()

    assert secrets.get_account_cookie(acc_id) == "sessionid=pasted_sid_999"
    assert win.state.accounts[0].username == "pasted_alice"


# ---------------------------------------------------------------------------
# 4. DeviceLoginDialog
# ---------------------------------------------------------------------------

def test_device_login_dialog_copy_open_authorize_and_cancel(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch):
    win, _store, _secrets, _mem, _tmp = make_window(smoke_test=True)
    opened_urls: list[str] = []
    monkeypatch.setattr("gui.widgets.device_login_dialog.QDesktopServices.openUrl", lambda u: opened_urls.append(u.toString()) or True)

    # 1) Successful authorization flow
    monkeypatch.setattr(
        "gui.widgets.device_login_dialog.device_flow_poll",
        lambda **kwargs: "gho_authorized_token_123",
    )
    monkeypatch.setattr(
        "gui.widgets.device_login_dialog.GitHub.whoami",
        lambda self: {"login": "octocat", "scopes_header_present": True, "scopes": ["repo", "workflow"]},
    )

    dlg_ok = DeviceLoginDialog(
        parent=win,
        worker_controller=win.workers,
        user_code="WXYZ-5678",
        verification_uri="https://github.com/login/device",
        device_code="dev_ok",
        interval=1,
        expires_in=60,
    )
    dlg_ok.copyOpenBtn.click()
    assert QApplication.clipboard().text() == "WXYZ-5678"
    assert opened_urls == ["https://github.com/login/device"]

    _pump_until(qapp, lambda: dlg_ok.result() == QDialog.Accepted, timeout=4.0)
    assert dlg_ok.result_token == "gho_authorized_token_123"
    assert dlg_ok.result_user["login"] == "octocat"
    _pump_until(qapp, lambda: win.workers.active_count() == 0, timeout=3.0)

    # 2) Cancel stops polling cleanly via cancel_event
    def slow_poll(device_code, interval, expires_in, cancel_event):
        cancel_event.wait(5.0)
        raise RuntimeError("Device flow authorization cancelled.")

    monkeypatch.setattr("gui.widgets.device_login_dialog.device_flow_poll", slow_poll)
    dlg_cancel = DeviceLoginDialog(
        parent=win,
        worker_controller=win.workers,
        user_code="CANC-0001",
        verification_uri="https://github.com/login/device",
        device_code="dev_cancel",
        interval=1,
        expires_in=60,
    )
    dlg_cancel.reject()
    assert dlg_cancel.cancel_event.is_set() is True
    _pump_until(qapp, lambda: win.workers.active_count() == 0, timeout=4.0)


# ---------------------------------------------------------------------------
# 5. ContentPage: Disable Flags, Photo/Video Toggles, GitHub Deploy Payload
# ---------------------------------------------------------------------------

def test_content_page_disable_flags_and_cloud_secrets_payload(qapp: QApplication, make_window, tmp_path: Path):
    acc1 = AccountProfile(id="a1", name="Main", friends=["alice", "bob"])
    acc2 = AccountProfile(
        id="a2",
        name="CustomAcc",
        friends=["carol"],
        message_override="Hey Carol",
        video_url_override="",
        share_times_override=0,
        photo_path_override="",
    )
    state = AppState(
        multi_account=False,
        accounts=[acc1, acc2],
        message="Daily Hello",
        video_enabled=True,
        photo_enabled=True,
        daily_cap=5,
        telegram_enabled=True,
        telegram_chat_id="998877",
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_1")
    secrets.set_account_cookie("a2", "sessionid=sid_2")
    secrets.set_telegram_token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    cp = win.content_interface
    cp.refresh_from_state()
    qapp.processEvents()

    # Disable video & photo via UI switches
    cp.videoSwitch.setChecked(False)
    cp.photoSwitch.setChecked(False)
    qapp.processEvents()

    assert win.state.video_enabled is False
    assert cp.videoUrlEdit.isEnabled() is False
    assert cp.shareTimesSpin.isEnabled() is False
    assert win.state.photo_enabled is False

    secrets_single_disabled = _build_cloud_secrets(win.state, secrets)
    assert secrets_single_disabled["DISABLE_VIDEO"] == "true"
    assert secrets_single_disabled["STREAK_VIDEO_URL"] == "none"
    assert secrets_single_disabled["SHARE_TIMES"] == "0"
    assert secrets_single_disabled["DISABLE_PHOTO"] == "true"
    assert secrets_single_disabled["PHOTO_PATH"] == "none"
    assert "PHOTO_DATA_URL" not in secrets_single_disabled
    assert secrets_single_disabled["DAILY_CAP"] == "5"
    assert secrets_single_disabled["TIKTOK_SESSION_ID"] == "sessionid=sid_1"
    assert secrets_single_disabled["FRIENDS_LIST"] == "alice,bob"
    assert "TIKTOK_ACCOUNTS_JSON" not in secrets_single_disabled

    # Re-enable video and test custom photo upload -> PHOTO_DATA_URL
    PIL_Image = pytest.importorskip("PIL.Image")
    custom_png = tmp_path / "custom_streak.png"
    PIL_Image.new("RGB", (120, 120), (20, 120, 220)).save(custom_png, format="PNG")

    cp.videoSwitch.setChecked(True)
    win.state.photo_path = str(custom_png)
    win.state.photo_enabled = True
    cp.refresh_from_state()
    qapp.processEvents()

    secrets_custom = _build_cloud_secrets(win.state, secrets)
    assert "DISABLE_VIDEO" not in secrets_custom
    assert "DISABLE_PHOTO" not in secrets_custom
    assert secrets_custom["PHOTO_DATA_URL"].startswith("data:image/jpeg;base64,")

    # Reset to Default clears custom photo & omits PHOTO_DATA_URL
    cp.resetPhotoBtn.click()
    qapp.processEvents()
    secrets_default = _build_cloud_secrets(win.state, secrets)
    assert "PHOTO_DATA_URL" not in secrets_default
    assert "PHOTO_PATH" not in secrets_default

    # Multi-account payload carries overrides and omits single-account keys
    win.state.multi_account = True
    secrets_multi = _build_cloud_secrets(win.state, secrets)
    assert "TIKTOK_SESSION_ID" not in secrets_multi
    assert "FRIENDS_LIST" not in secrets_multi
    payload = json.loads(secrets_multi["TIKTOK_ACCOUNTS_JSON"])
    assert len(payload) == 2
    assert payload[0]["name"] == "Main"
    assert payload[1]["message"] == "Hey Carol"
    assert payload[1]["video_url"] == "none"
    assert payload[1]["share_times"] == 0
    assert payload[1]["photo_path"] == "none"


def test_content_page_github_deploy_and_local_schedule(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch):
    acc1 = AccountProfile(id="a1", name="Main", friends=["alice"])
    state = AppState(
        accounts=[acc1],
        github_private=True,
        github_repo_name="my-private-streak",
        timezone="Asia/Jakarta",
        cloud_cron="0 1,13 * * *",
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_deploy; tt-target-idc=useast2a")
    secrets.set_github_token("ghp_deploy_token_1234567890")

    ap = win.automation_interface
    ap.refresh_from_state()
    qapp.processEvents()

    captured: dict = {}

    def fake_whoami(self):
        return {"login": "octocat", "scopes_header_present": True, "scopes": ["repo", "workflow"]}

    def fake_create_private(self, owner, repo_name, source_dir=None, cron_expr=None, progress_cb=None):
        captured["create_private"] = {
            "owner": owner,
            "repo_name": repo_name,
            "cron_expr": cron_expr,
        }
        if progress_cb:
            progress_cb("Private repo created")
        return {"html_url": f"https://github.com/{owner}/{repo_name}"}

    def fake_sync_secrets(self, full_name, desired, managed_names=None):
        captured["sync_secrets"] = {
            "full_name": full_name,
            "desired": dict(desired),
            "managed_names": list(managed_names or []),
        }
        return {"written": list(desired.keys()), "deleted": ["PHOTO_DATA_URL"]}

    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.whoami", fake_whoami)
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.create_private_repo_with_bot", fake_create_private)
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.sync_secrets", fake_sync_secrets)

    ap.deployBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("deploy_github"), timeout=4.0)
    qapp.processEvents()

    assert captured["create_private"] == {
        "owner": "octocat",
        "repo_name": "my-private-streak",
        "cron_expr": "0 1,13 * * *",
    }
    assert captured["sync_secrets"]["full_name"] == "octocat/my-private-streak"
    assert captured["sync_secrets"]["managed_names"] == SECRET_NAMES
    assert captured["sync_secrets"]["desired"]["DISABLE_VIDEO"] == "true"
    assert captured["sync_secrets"]["desired"]["DISABLE_PHOTO"] == "true"
    assert win.state.github_login == "octocat"

    # Local time add/remove and Windows Task Scheduler sync
    ap.timePicker.setTime(QTime(20, 30))
    ap.addTimeBtn.click()
    qapp.processEvents()
    assert win.state.local_times == ["08:00", "20:30"]

    sync_calls: list[list[str]] = []
    monkeypatch.setattr(
        "gui.pages.automation_page.WindowsScheduler.sync_daily_tasks",
        lambda self, times: (sync_calls.append(list(times)), {"ok": True, "created": [{"name": "T1"}, {"name": "T2"}], "removed": []})[-1],
    )
    ap.applyWinScheduleBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("sync_windows_tasks"), timeout=4.0)
    qapp.processEvents()
    assert sync_calls == [["08:00", "20:30"]]


# ---------------------------------------------------------------------------
# 6. RunPage Local/Cloud Actions & LogViewer Secret Redaction
# ---------------------------------------------------------------------------

def test_run_page_local_cloud_and_log_viewer_redaction(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    acc = AccountProfile(id="a1", name="Main", friends=["alice", "bob"])
    state = AppState(
        accounts=[acc],
        github_login="octocat",
        github_repo_name="my-streak",
        timezone="Asia/Jakarta",
        headless_local=True,
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secret_sid = "super_secret_sid_value_987"
    secret_gh = "ghp_superSecretGitHubToken12345"
    secrets.set_account_cookie("a1", f"sessionid={secret_sid}")
    secrets.set_github_token(secret_gh)

    rp = win.run_interface
    rp.refresh_from_state()
    qapp.processEvents()

    assert rp.friendCombo.count() == 2
    assert rp.runCloudBtn.isEnabled() is True

    # 1) Quick test honors showBrowserSwitch (Off -> headless=True, On -> headless=False) + LogViewer redaction
    smoke_calls: list[dict] = []

    def fake_smoke(self, st, ss, friend, account_id=None, headless=False, stop_event=None, log_cb=None, progress_cb=None):
        smoke_calls.append({"friend": friend, "account_id": account_id, "headless": headless})
        if log_cb:
            log_cb(f"Using sessionid={secret_sid} raw={secret_sid} tok={secret_gh}")
        if progress_cb:
            progress_cb({"message": f"Messaged @{friend}"})
        return {"ok": True, "elapsed": 2, "results": [{"name": "Main", "sent": 1}]}

    monkeypatch.setattr("gui.pages.run_page.LocalRunner.run_smoke_test", fake_smoke)
    assert rp.showBrowserSwitch.isChecked() is False
    rp.quickTestBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("local_run"), timeout=4.0)
    qapp.processEvents()

    rp.showBrowserSwitch.setChecked(True)
    rp.quickTestBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("local_run"), timeout=4.0)
    qapp.processEvents()

    assert smoke_calls == [
        {"friend": "alice", "account_id": "a1", "headless": True},
        {"friend": "alice", "account_id": "a1", "headless": False},
    ]
    assert win.state.last_test_ok is True
    log_text = rp.logViewer.editor.toPlainText()
    assert secret_sid not in log_text
    assert secret_gh not in log_text
    assert "<REDACTED" in log_text

    # 2) Full local run honors showBrowserSwitch
    full_calls: list[dict] = []

    def fake_full(self, st, ss, only_account=None, headless=None, stop_event=None, log_cb=None, progress_cb=None):
        full_calls.append({"headless": headless})
        return {"ok": True, "elapsed": 3, "results": []}

    monkeypatch.setattr("gui.pages.run_page.LocalRunner.run", fake_full)
    rp.showBrowserSwitch.setChecked(False)
    rp.fullRunBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("local_run"), timeout=4.0)
    qapp.processEvents()
    assert full_calls == [{"headless": True}]

    # 3) Cloud monitor: refresh runs, dispatch, view logs, download diagnostics
    fake_run_row = {
        "id": 4242,
        "status": "completed",
        "conclusion": "failure",
        "event": "workflow_dispatch",
        "created_at": "2026-11-18T01:00:00Z",
        "html_url": "https://github.com/octocat/my-streak/actions/runs/4242",
    }
    monkeypatch.setattr("gui.pages.run_page.GitHub.runs", lambda self, fn, wf, limit=8: [fake_run_row])
    monkeypatch.setattr("gui.pages.run_page.GitHub.enable_workflow", lambda self, fn, wf: None)
    monkeypatch.setattr("gui.pages.run_page.GitHub.dispatch_and_track", lambda self, fn, wf, timeout=30.0: fake_run_row)
    monkeypatch.setattr("gui.pages.run_page.GitHub.get_run_jobs", lambda self, fn, rid: [{"id": 77, "name": "run-streak", "conclusion": "failure"}])
    monkeypatch.setattr(
        "gui.pages.run_page.GitHub.get_job_logs",
        lambda self, fn, jid: f"Job failed with Cookie: sessionid={secret_sid} and {secret_gh}",
    )

    PIL_Image = pytest.importorskip("PIL.Image")
    diag_png = tmp_path / "diag_sample.png"
    PIL_Image.new("RGB", (64, 64), (10, 20, 30)).save(diag_png, format="PNG")
    monkeypatch.setattr(
        "gui.pages.run_page.GitHub.download_diagnostics_artifact",
        lambda self, fn, rid, dest: [diag_png],
    )

    rp.refreshRunsBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("fetch_cloud_runs"), timeout=4.0)
    qapp.processEvents()
    assert rp.runsTable.rowCount() == 1
    assert "Failed" in rp.runsTable.item(0, 0).text()
    assert rp.runsTable.item(0, 1).text() == "Manual"

    rp.runCloudBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("dispatch_cloud_run"), timeout=4.0)
    qapp.processEvents()
    assert rp.runsTable.rowCount() == 1

    rp.viewLogsBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("fetch_job_logs"), timeout=4.0)
    qapp.processEvents()
    job_log_text = rp.logViewer.editor.toPlainText()
    assert "run-streak" in job_log_text
    assert secret_sid not in job_log_text
    assert secret_gh not in job_log_text

    rp.downloadDiagBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("download_diag"), timeout=4.0)
    qapp.processEvents()
    assert rp.diagPreviewBox.isHidden() is False
    assert rp.diagCombo.count() == 1

    # 4) LogViewer diagnostic report & clipboard copy redaction
    rp.logViewer.copy_diagnostic_report()
    copied = QApplication.clipboard().text()
    assert "TikTok Streak Diagnostic Report" in copied
    assert secret_sid not in copied
    assert secret_gh not in copied

    # Standalone LogViewer with custom extra secrets
    lv = LogViewer(secrets_provider=lambda: ["customSecretToken9876"])
    lv.append_log("Line with customSecretToken9876 and 123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    assert "customSecretToken9876" not in lv.editor.toPlainText()
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in lv.editor.toPlainText()
    lv.close()


# ---------------------------------------------------------------------------
# 7. SettingsPage: Telegram Detect/Test, Workspace Export/Import, Reset All
# ---------------------------------------------------------------------------

def test_settings_page_telegram_workspace_sync_and_reset(qapp: QApplication, make_window, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    acc = AccountProfile(id="a1", name="Main", friends=["alice", "bob"])
    state = AppState(accounts=[acc], message="Streak Hi!")
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=ws_sid_123")
    secrets.set_github_token("ghp_ws_token_12345678")

    sp = win.settings_interface
    sp.refresh_from_state()
    qapp.processEvents()

    valid_tg_tok = "1234567:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    monkeypatch.setattr(
        "gui.pages.settings_page.telegram_find_chat_id",
        lambda tok: {"ok": True, "chat_id": "777888999"},
    )
    monkeypatch.setattr(
        "gui.pages.settings_page.telegram_test",
        lambda tok, cid: {"ok": True, "chat_id": cid or "777888999"},
    )

    sp.tgTokenEdit.setText(valid_tg_tok)
    sp.tgDetectBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("tg_detect"), timeout=4.0)
    qapp.processEvents()
    assert sp.tgChatEdit.text() == "777888999"
    assert win.state.telegram_chat_id == "777888999"

    sp.tgTestBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("tg_test"), timeout=4.0)
    qapp.processEvents()
    assert win.state.telegram_enabled is True
    assert secrets.get_telegram_token() == valid_tg_tok

    # Export workspace to temp dir
    ws_dir = tmp_path / "exported_ws"
    sp.workspaceDirEdit.setText(str(ws_dir))
    sp.syncWorkspaceBtn.click()
    qapp.processEvents()
    assert (ws_dir / ".env").is_file()
    assert (ws_dir / "friends.csv").is_file()
    assert 'MESSAGE="Streak Hi!"' in (ws_dir / ".env").read_text(encoding="utf-8")

    # Modify .env and import back into GUI state
    (ws_dir / ".env").write_text(
        'TIKTOK_SESSION_ID="imported_sid_555"\n'
        'MESSAGE="Imported Message"\n'
        'FRIENDS_LIST="charlie, dana"\n'
        'DISABLE_VIDEO="true"\n'
        'DISABLE_PHOTO="true"\n',
        encoding="utf-8",
    )
    sp.importWorkspaceBtn.click()
    qapp.processEvents()
    assert win.state.message == "Imported Message"
    assert win.state.video_enabled is False
    assert win.state.photo_enabled is False
    assert win.state.accounts[0].friends == ["charlie", "dana"]
    assert secrets.get_account_cookie(win.state.accounts[0].id) == "imported_sid_555"

    # Reset all settings & secrets (mock ResetConfirmDialog.exec -> 1)
    monkeypatch.setattr("gui.pages.settings_page.ResetConfirmDialog.exec", lambda self: 1)
    sp.resetAllBtn.click()
    qapp.processEvents()
    assert secrets.get_github_token() == ""
    assert secrets.get_telegram_token() == ""
    assert len(win.state.accounts) == 1
    assert win.state.accounts[0].friends == []


# ---------------------------------------------------------------------------
# 8. Regression checks for f48c70c fixes
# ---------------------------------------------------------------------------

def test_regressions_unsubscribe_workspace_overrides_reopen_and_focus(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from gui.services.i18n import I18nManager
    from gui.services.storage import (
        DEFAULT_PHOTO_PATH,
        export_local_workspace,
        import_local_workspace,
    )

    # 1) I18nManager.unsubscribe removes bound methods cleanly
    class _Listener:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def on_lang(self, lang: str) -> None:
            self.calls.append(lang)

    mgr = I18nManager("en")
    lst = _Listener()
    mgr.subscribe(lst.on_lang)
    mgr.unsubscribe(lst.on_lang)
    mgr.set_language("id")
    assert lst.calls == []

    # 2) export_local_workspace honors multi_account=False even with preserved extra accounts
    acc1 = AccountProfile(id="a1", name="Main", friends=["alice"])
    acc2 = AccountProfile(id="a2", name="Backup", friends=["bob"])
    st = AppState(
        multi_account=False,
        accounts=[acc1, acc2],
        headless_local=True,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=st, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=primary_sid")
    secrets.set_account_cookie("a2", "sessionid=backup_sid")

    ws_dir = tmp_path / "single_with_extra"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "accounts.json").write_text('[{"name": "Stale"}]', encoding="utf-8")
    exp = export_local_workspace(win.state, secrets, ws_dir)
    assert exp["mode"] == "single"
    assert not (ws_dir / "accounts.json").exists()
    env_text = (ws_dir / ".env").read_text(encoding="utf-8")
    assert 'TIKTOK_SESSION_ID="sessionid=primary_sid"' in env_text
    assert 'FRIENDS_LIST="alice"' in env_text

    # 3) import_local_workspace preserves explicit DEFAULT_PHOTO_PATH when global photo disabled
    (ws_dir / ".env").write_text('DISABLE_PHOTO="true"\nPHOTO_PATH="none"\n', encoding="utf-8")
    (ws_dir / "accounts.json").write_text(
        json.dumps([
            {"name": "Main", "session_id": "s1", "friends": ["alice"], "photo_path": DEFAULT_PHOTO_PATH},
            {"name": "Alt", "session_id": "s2", "friends": ["bob"], "photo_path": "none"},
        ]),
        encoding="utf-8",
    )
    imp = import_local_workspace(ws_dir)
    assert imp["state"].photo_enabled is False
    assert imp["state"].accounts[0].photo_path_override == DEFAULT_PHOTO_PATH
    assert imp["state"].accounts[1].photo_path_override == "none"

    # 4) RunPage showBrowserSwitch persists to state.headless_local and syncs AutomationPage with identical polarity
    win.run_interface.refresh_from_state()
    win.automation_interface.refresh_from_state()
    qapp.processEvents()
    assert win.run_interface.showBrowserSwitch.isChecked() is False
    assert win.automation_interface.showBrowserSwitch.isChecked() is False
    win.run_interface.showBrowserSwitch.setChecked(True)
    qapp.processEvents()
    assert win.state.headless_local is False
    assert win.automation_interface.showBrowserSwitch.isChecked() is True
    win.run_interface._on_local_finished({"ok": True, "elapsed": 1}, is_smoke=True)
    qapp.processEvents()
    assert win.run_interface.showBrowserSwitch.isChecked() is True

    # 5) AccountCard empty ovVideoEdit with ovVideoCheck checked does not auto-enable Disable
    win.accounts_interface._on_mode_changed("multi")
    qapp.processEvents()
    card0 = win.accounts_interface._cards[0]
    card0.ovVideoCheck.setChecked(True)
    card0.ovVideoDisableCheck.setChecked(False)
    card0.ovVideoEdit.setText("")
    card0.ovSharesSpin.setValue(4)
    qapp.processEvents()
    assert card0.profile.video_url_override is None
    assert card0.profile.share_times_override == 4
    card0.load_profile(card0.profile, multi_account=True)
    assert card0.ovVideoDisableCheck.isChecked() is False
    assert card0.ovVideoEdit.isEnabled() is True

    # 6) retranslate() updates multiHiddenHint and windowsTasksBadge
    win.accounts_interface._on_mode_changed("single")
    qapp.processEvents()
    set_language("id")
    qapp.processEvents()
    assert "Mode 1 akun" in win.accounts_interface.multiHiddenHint.text()
    assert "tugas aktif" in win.automation_interface.windowsTasksBadge.text()
    set_language("en")
    qapp.processEvents()
    assert "Single-account mode" in win.accounts_interface.multiHiddenHint.text()
    assert "task(s) active" in win.automation_interface.windowsTasksBadge.text()

    # 7) DeviceLoginDialog uses unique task_key so reopening right after cancel does not fail
    gate = threading.Event()

    def slow_poll(device_code, interval, expires_in, cancel_event):
        cancel_event.wait(2.0)
        gate.wait(2.0)
        raise RuntimeError("cancelled")

    monkeypatch.setattr("gui.widgets.device_login_dialog.device_flow_poll", slow_poll)
    dlg1 = DeviceLoginDialog(
        parent=win,
        worker_controller=win.workers,
        user_code="CODE-1111",
        verification_uri="https://github.com/login/device",
        device_code="dev1",
        interval=1,
        expires_in=60,
    )
    dlg1.reject()
    dlg2 = DeviceLoginDialog(
        parent=win,
        worker_controller=win.workers,
        user_code="CODE-2222",
        verification_uri="https://github.com/login/device",
        device_code="dev2",
        interval=1,
        expires_in=60,
    )
    assert dlg1._task_key != dlg2._task_key
    dlg2.reject()
    gate.set()
    _pump_until(qapp, lambda: win.workers.active_count() == 0, timeout=4.0)

    # 8) SettingsPage _saving_from_self / focus guard preserves mid-typing trailing spaces
    sp = win.settings_interface
    sp.workspaceDirEdit.setText("C:/My ")
    sp._on_debounced_save()
    qapp.processEvents()
    assert sp.workspaceDirEdit.text() == "C:/My "


# ---------------------------------------------------------------------------
# 9. Regression checks for v0.2.1 (#1-#9) UI, SpinBox, HomePage, Grab & Repo Picker
# ---------------------------------------------------------------------------

def test_v021_spinbox_layout_homepage_badge_drawer_and_manual_grab(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    acc1 = AccountProfile(id="a1", name="Main", friends=["alice"], username="", inbox_verified=False)
    acc2 = AccountProfile(id="a2", name="WithOv", friends=["bob"], message_override="Custom msg")
    state = AppState(
        multi_account=True,
        accounts=[acc1, acc2],
        mode="both",
        video_enabled=True,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_unverified")
    win.resize(840, 720)
    win.show()
    qapp.processEvents()

    # 1) HomePage Step 1 StepPill: done when cookie + friends configured; current when incomplete
    hp = win.home_interface
    hp.refresh_state()
    qapp.processEvents()
    assert hp.step1.stepPill.state() == "done"

    live_acc1 = win.state.accounts[0]
    live_acc1.username = "alice_tt"
    hp.refresh_state()
    qapp.processEvents()
    assert "@alice_tt" in hp.step1.detailLabel.text()

    # 2) ContentPage & AutomationPage ('both' mode): no horizontal overflow & compact SpinBox
    cp = win.content_interface
    aut = win.automation_interface
    win.navigate_to("content")
    qapp.processEvents()
    _pump_until(qapp, lambda: cp.shareTimesSpin.lineEdit().width() >= 30, timeout=3.0)
    assert cp.shareTimesSpin.lineEdit().width() >= 30

    win.navigate_to("automation")
    aut.modeSegment.setCurrentItem("both")
    aut._on_mode_changed("both")
    _pump_until(qapp, lambda: aut.dailyCapSpin.lineEdit().width() >= 30, timeout=3.0)
    assert aut.modeSegment.height() <= 44
    assert aut.dailyCapSpin.lineEdit().width() >= 30

    # 3) AccountCard drawer collapsed by default, expanded when overrides active, Shares label & SpinBox width
    ap = win.accounts_interface
    win.navigate_to("accounts")
    qapp.processEvents()
    card_no_ov, card_with_ov = ap._cards[0], ap._cards[1]
    assert card_no_ov.overridesDrawer.isHidden() is True
    assert card_with_ov.overridesDrawer.isHidden() is False
    assert card_no_ov.ovSharesLabel.text() == tr("accounts.override_shares")
    assert "Share" in card_no_ov.ovSharesLabel.text()

    card_no_ov.overridesToggleBtn.click()
    _pump_until(qapp, lambda: card_no_ov.ovSharesSpin.lineEdit().width() >= 30, timeout=3.0)
    assert card_no_ov.overridesDrawer.isHidden() is False
    assert card_no_ov.ovSharesSpin.lineEdit().width() >= 30
    assert card_with_ov.ovSharesSpin.lineEdit().width() >= 30

    # 4) AccountsPage taskGrabBtn sets manual_grab_event
    seen_grab_events: list[threading.Event] = []

    def fake_guided_manual(profile_dir, browser_kind="auto", cancel_event=None, status_cb=None, manual_grab_event=None):
        assert manual_grab_event is not None
        seen_grab_events.append(manual_grab_event)
        if status_cb:
            status_cb("Opening TikTok login page...")
        manual_grab_event.wait(3.0)
        return {
            "ok": True,
            "sessionid": "grabbed_sid_123",
            "cookie_text": "sessionid=grabbed_sid_123",
            "username": "grabbed_user",
            "inbox_verified": False,
            "business_suite": False,
            "browser": "chrome",
        }

    monkeypatch.setattr("gui.pages.accounts_page.guided_browser_login", fake_guided_manual)
    ap._start_guided_login("a1")
    _pump_until(qapp, lambda: len(seen_grab_events) == 1 and not ap.activeTaskCard.isHidden(), timeout=3.0)
    assert ap.taskGrabBtn.isEnabled() is True
    ap.taskGrabBtn.click()
    assert seen_grab_events[0].is_set() is True
    _pump_until(qapp, lambda: not win.workers.is_running("guided_login"), timeout=4.0)
    qapp.processEvents()
    assert live_acc1.username == "grabbed_user"
    assert secrets.get_account_cookie("a1") == "sessionid=grabbed_sid_123"


def test_v021_existing_repo_picker_owner_repo_sync_only_and_run_page(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    acc = AccountProfile(id="a1", name="Main", friends=["alice"])
    state = AppState(
        accounts=[acc],
        mode="cloud",
        github_login="octocat",
        github_repo_name="",
        github_private=False,
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_repo; tt-target-idc=useast2a")
    secrets.set_github_token("ghp_repo_picker_token_12345")

    cp = win.automation_interface
    rp = win.run_interface
    cp.refresh_from_state()
    rp.refresh_from_state()
    qapp.processEvents()

    # 1) Bot repo discovery populates clickable _BotRepoRow items and clicking a row updates state + badge + private switch
    monkeypatch.setattr(
        "gui.services.cloud_deploy.GitHub.discover_bot_repos",
        lambda self, limit=100, workflow_file="streak.yml": {
            "all_repos": [
                {
                    "full_name": "myorg/streak-bot",
                    "name": "streak-bot",
                    "owner": "myorg",
                    "private": True,
                    "archived": False,
                    "fork": False,
                    "default_branch": "main",
                    "html_url": "https://github.com/myorg/streak-bot",
                },
                {
                    "full_name": "octocat/public-streak",
                    "name": "public-streak",
                    "owner": "octocat",
                    "private": False,
                    "archived": False,
                    "fork": False,
                    "default_branch": "main",
                    "html_url": "https://github.com/octocat/public-streak",
                },
            ],
            "bot_repos": [
                {
                    "full_name": "myorg/streak-bot",
                    "name": "streak-bot",
                    "owner": "myorg",
                    "private": True,
                    "fork": False,
                    "html_url": "https://github.com/myorg/streak-bot",
                    "workflow_id": 10,
                    "workflow_state": "active",
                    "active": True,
                },
                {
                    "full_name": "octocat/public-streak",
                    "name": "public-streak",
                    "owner": "octocat",
                    "private": False,
                    "fork": False,
                    "html_url": "https://github.com/octocat/public-streak",
                    "workflow_id": 11,
                    "workflow_state": "disabled_manually",
                    "active": False,
                },
            ],
            "active_bot_repos": [{"full_name": "myorg/streak-bot", "active": True}],
            "has_duplicates": False,
            "scanned_count": 2,
        },
    )
    assert not hasattr(cp, "existingRepoCombo")
    assert not hasattr(cp, "fetchReposBtn")
    cp.scan_bot_repos()
    _pump_until(qapp, lambda: not win.workers.is_running("discover_bot_repos"), timeout=4.0)
    qapp.processEvents()
    assert len(cp._bot_repo_row_widgets) == 2

    QTest.mouseClick(cp._bot_repo_row_widgets[0], Qt.LeftButton)
    qapp.processEvents()
    assert cp.repoNameEdit.text() == "myorg/streak-bot"
    assert cp.privateRepoSwitch.isChecked() is True
    assert win.state.github_repo_name == "myorg/streak-bot"
    assert isinstance(cp.repoValidBadge, StatusPill)
    assert cp.repoValidBadge.isHidden() is True
    assert cp.repoValidCaption.isHidden() is False
    assert cp.syncOnlyCheck.isEnabled() is True

    # 2) RunPage._full_repo_name handles owner/repo and bare repo + login
    rp.refresh_from_state()
    assert rp._full_repo_name() == "myorg/streak-bot"
    win.state.github_repo_name = "bare-repo"
    assert rp._full_repo_name() == "octocat/bare-repo"
    win.state.github_repo_name = "myorg/streak-bot"

    # 3) syncOnlyCheck passes push_files=False for owner/repo private deploy
    captured: dict = {}

    def fake_whoami(self):
        return {"login": "octocat", "scopes_header_present": True, "scopes": ["repo", "workflow"]}

    def fake_create_private(self, owner, repo_name, source_dir=None, cron_expr=None, progress_cb=None, push_files=True):
        captured["create_private"] = {
            "owner": owner,
            "repo_name": repo_name,
            "push_files": push_files,
        }
        return {"full_name": f"{owner}/{repo_name}", "html_url": f"https://github.com/{owner}/{repo_name}", "files_pushed": 0}

    def fake_sync_secrets(self, full_name, desired, managed_names=None):
        captured["sync_full_name"] = full_name
        return {"written": list(desired.keys()), "deleted": []}

    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.whoami", fake_whoami)
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.create_private_repo_with_bot", fake_create_private)
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.sync_secrets", fake_sync_secrets)

    cp.syncOnlyCheck.setChecked(True)
    cp.deployBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("deploy_github"), timeout=4.0)
    qapp.processEvents()

    assert captured["create_private"] == {
        "owner": "myorg",
        "repo_name": "streak-bot",
        "push_files": False,
    }
    assert captured["sync_full_name"] == "myorg/streak-bot"


# ---------------------------------------------------------------------------
# 10. v0.2.2 UI Craftsmanship & Windows Personalization Checks
# ---------------------------------------------------------------------------

def test_status_pill_levels_autohide_and_theme_refresh(qapp: QApplication):
    pill = StatusPill("", "neutral")
    assert pill.isHidden() is True
    assert pill.level() == "neutral"

    # InfoLevel & string mapping
    cases = [
        (InfoLevel.SUCCESS, "success"),
        (InfoLevel.WARNING, "warning"),
        (InfoLevel.ERROR, "error"),
        (InfoLevel.INFOAMTION, "info"),
        (InfoLevel.ATTENTION, "warning"),
        ("success", "success"),
        ("warning", "warning"),
        ("error", "error"),
        ("info", "info"),
        ("information", "info"),
        ("attention", "warning"),
        ("neutral", "neutral"),
        ("unknown-level", "neutral"),
    ]
    for raw_lvl, expected in cases:
        pill.setState(raw_lvl, "Active")
        assert pill.level() == expected
        assert pill.isHidden() is False

    # Auto-hide on empty/whitespace text
    pill.setText("   ")
    assert pill.isHidden() is True
    pill.setText("Ready")
    assert pill.isHidden() is False
    pill.setState("success", "")
    assert pill.isHidden() is True

    # Theme change refreshes palette stylesheet and dot color
    pill.setState("success", "Verified")
    setTheme(Theme.LIGHT, save=False)
    qapp.processEvents()
    light_ss = pill.styleSheet()
    light_dot = pill._dot._color.name().lower()

    setTheme(Theme.DARK, save=False)
    qapp.processEvents()
    dark_ss = pill.styleSheet()
    dark_dot = pill._dot._color.name().lower()

    assert light_ss != dark_ss
    assert light_dot != dark_dot
    setTheme(Theme.AUTO, save=False)
    qapp.processEvents()
    pill.close()


def test_v022_mainwindow_accent_sidebar_and_homepage_cta(
    qapp: QApplication,
    make_window,
):
    acc = AccountProfile(id="a1", name="Main", friends=[])
    state = AppState(accounts=[acc], mode="cloud", accent_mode="system")
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=False)
    win.resize(1080, 760)
    win.show()
    qapp.processEvents()

    # Sidebar expanded on 1080x760 and minimumExpandWidth == 940
    panel = win.navigationInterface.panel
    assert getattr(panel, "_minimumExpandWidth", 940) == 940
    assert panel.displayMode == NavigationDisplayMode.EXPAND
    assert not panel.isCollapsed()

    # SettingsPage accent_changed updates state.accent_mode and themeColor()
    sp = win.settings_interface
    sys_theme_color = themeColor().name().lower()
    idx_tiktok = sp.accentCombo.findData("tiktok")
    assert idx_tiktok >= 0
    sp.accentCombo.setCurrentIndex(idx_tiktok)
    qapp.processEvents()
    assert win.state.accent_mode == "tiktok"
    assert qconfig.themeColor.value.name().lower() == DEFAULT_TIKTOK_ACCENT
    assert themeColor().isValid()
    assert themeColor().name().lower() != sys_theme_color

    idx_sys = sp.accentCombo.findData("system")
    assert idx_sys >= 0
    sp.accentCombo.setCurrentIndex(idx_sys)
    qapp.processEvents()
    assert win.state.accent_mode == "system"
    assert qconfig.themeColor.value.name().lower() == resolve_accent_color("system").lower()
    assert themeColor().name().lower() == sys_theme_color

    # HomePage: single "Setup Checklist" HeaderCardWidget, StepPills highlighting first incomplete step, and header quickTestBtn
    hp = win.home_interface
    assert hp.checklistCard.title == "Setup Checklist"
    assert isinstance(hp.overallBadge, StatusPill)

    def _current_steps() -> list[int]:
        return [
            idx
            for idx, row in enumerate((hp.step1, hp.step2, hp.step3, hp.step4), start=1)
            if row.stepPill.state() == "current"
        ]

    # Step 1 incomplete (no cookie, no friends) -> step 1 is current
    hp.refresh_state()
    qapp.processEvents()
    assert _current_steps() == [1]
    assert not isinstance(hp.quickTestBtn, PrimaryPushButton)

    # Complete Step 1 (cookie + friends) -> Step 2 is already valid (default message) -> Step 3 (Automation) is current
    secrets.set_account_cookie("a1", "sessionid=sid_step1")
    win.state.accounts[0].friends = ["alice"]
    hp.refresh_state()
    qapp.processEvents()
    assert _current_steps() == [3]

    # Complete Step 3 (GitHub token) -> Step 4 is current & core_ready activates header primary quickTestBtn
    secrets.set_github_token("ghp_step3_token_12345")
    hp.refresh_state()
    qapp.processEvents()
    assert _current_steps() == [4]
    assert isinstance(hp.quickTestBtn, PrimaryPushButton)

    # Complete Step 4 -> all 4 steps done
    win.state.last_test_ok = True
    hp.refresh_state()
    qapp.processEvents()
    assert _current_steps() == []
    assert "4 / 4" in hp.checklistProgressBadge.text()
    assert isinstance(hp.quickTestBtn, PrimaryPushButton)


def test_v022_accounts_content_run_and_logviewer_craftsmanship(
    qapp: QApplication,
    make_window,
):
    acc = AccountProfile(id="a1", name="Main", friends=["alice"])
    state = AppState(
        multi_account=True,
        accounts=[acc],
        mode="both",
        github_login="octocat",
        github_repo_name="tiktok-streak",
        local_times=["08:00"],
        video_enabled=False,
        photo_enabled=True,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_github_token("ghp_signed_in_token_123")
    win.resize(1080, 760)
    win.show()
    qapp.processEvents()

    # AccountsPage & AccountCard (browser profile picker moved cleanly into BrowserImportDialog)
    from gui.widgets.browser_import_dialog import BrowserImportDialog

    ap = win.accounts_interface
    ap.refresh_from_state()
    qapp.processEvents()
    assert not hasattr(ap, "browserDisclosureCard")
    assert not hasattr(ap, "browserCard")
    import_dlg = BrowserImportDialog(win, account_name="Main", sources=[], scan_completed=False)
    assert isinstance(import_dlg.browserBadge, StatusPill)
    assert import_dlg.browserBadge.level() == "neutral"
    assert import_dlg.browserBadge.text() == tr("accounts.scan_not_run")
    import_dlg.close()
    assert ap.addAccountBtn.isHidden() is False
    assert ap.addAccountBtn.width() < ap.view.width() // 2

    card = ap._cards[0]
    assert card.nameEdit.minimumWidth() >= 200
    for badge in (card.usernameBadge, card.inboxBadge, card.friendsCountBadge):
        assert isinstance(badge, StatusPill)

    # ContentPage & AutomationPage
    cp = win.content_interface
    aut = win.automation_interface
    cp.refresh_from_state()
    aut.refresh_from_state()
    qapp.processEvents()
    assert aut.deployBtn.text() == "Deploy to GitHub"
    assert aut.syncOnlyCheck.text() == "Sync secrets && schedule only (keep repo commits)"
    assert cp.resetPhotoBtn.text() == "Reset to Default"
    assert isinstance(aut.timesList, ListWidget)
    assert aut.timesList.count() == 1
    assert aut.advancedContainer.isHidden() is True

    # Single cloud primary CTA in "both" / "cloud" mode (Windows sync stays secondary)
    assert isinstance(aut.deployBtn, PrimaryPushButton)
    assert aut._applyWinPrimaryBtn.isHidden() is True
    assert aut._applyWinSecondaryBtn.isHidden() is False
    assert aut.applyWinScheduleBtn is aut._applyWinSecondaryBtn

    # repoValidBadge hidden when valid, shown when invalid
    assert aut.repoValidBadge.isHidden() is True
    assert aut.repoValidCaption.isHidden() is False
    aut.repoNameEdit.setText("bad repo name!")
    qapp.processEvents()
    assert aut.repoValidBadge.isHidden() is False
    assert aut.repoValidCaption.isHidden() is True
    aut.repoNameEdit.setText("tiktok-streak")
    qapp.processEvents()
    assert aut.repoValidBadge.isHidden() is True

    # RunPage & LogViewer
    rp = win.run_interface
    rp.refresh_from_state()
    qapp.processEvents()
    assert rp.runsTable.rowCount() == 0
    assert rp.runEmptyBox.isHidden() is False
    assert rp.runsTable.isHidden() is True

    rp._populate_runs_table([
        {
            "id": 99,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "created_at": "2026-11-18T01:00:00Z",
        }
    ])
    qapp.processEvents()
    assert rp.runEmptyBox.isHidden() is True
    assert rp.runsTable.isHidden() is False

    created_font = rp.runsTable.item(0, 2).font()
    assert created_font.fixedPitch() is True
    assert created_font.families()[0] == "Cascadia Mono"

    editor_font = rp.logViewer.editor.font()
    assert editor_font.fixedPitch() is True
    assert editor_font.families()[0] == "Cascadia Mono"


# ---------------------------------------------------------------------------
# 11. v1.1.0 Auth Verify UX, Timezone / Cloud Schedule & Layout Bounds
# ---------------------------------------------------------------------------

def test_v110_account_card_verify_ux_and_accounts_page_cookie_upgrade(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    acc = AccountProfile(id="a1", name="Main", friends=["alice"], username="", inbox_verified=False)
    win, _store, secrets, _mem, _tmp = make_window(state=AppState(accounts=[acc]), smoke_test=True)
    ap = win.accounts_interface
    ap.refresh_from_state()
    qapp.processEvents()

    card = ap._cards[0]
    assert card.verifyInboxBtn.text() == "Verify Session"
    assert card.verifySpinner.isHidden() is True

    # Language switch updates Verify Session label cleanly
    set_language("id")
    qapp.processEvents()
    assert card.verifyInboxBtn.text() == "Verifikasi Sesi"
    set_language("en")
    qapp.processEvents()
    assert card.verifyInboxBtn.text() == "Verify Session"

    # set_verifying(True/False) toggles spinner, status label, button, and badge
    card.set_verifying(True, "Opening headless browser...")
    assert card.verifySpinner.isHidden() is False
    assert card.verifyStatusLabel.text() == "Opening headless browser..."
    assert card.verifyInboxBtn.isEnabled() is False
    assert card.verifyInboxBtn.text() == tr("accounts.verifying")
    assert card.inboxBadge.text() == tr("accounts.verifying")
    assert card.inboxBadge.level() == "info"

    card.set_verifying(False)
    assert card.verifySpinner.isHidden() is True
    assert card.verifyInboxBtn.text() == "Verify Session"

    # Paste bare cookie -> _check_pasted_cookie hydrates regional cookie and persists it
    monkeypatch.setattr(CookieDialog, "exec", lambda self: (
        self.textEdit.setPlainText("sessionid=bare_paste_1"),
        self.validate(),
        True,
    )[-1])
    monkeypatch.setattr(
        "gui.pages.accounts_page._check_pasted_cookie",
        lambda payload: {
            "ok": True,
            "username": "pasted_regional",
            "cookie_text": "sessionid=bare_paste_1; tt-target-idc=alisg; store-idc=alisg",
            "cloud_ready": True,
        },
    )
    ap._open_paste_dialog("a1")
    _pump_until(qapp, lambda: not win.workers.is_running("check_cookie_a1"), timeout=4.0)
    qapp.processEvents()

    stored_after_paste = secrets.get_account_cookie("a1")
    assert "tt-target-idc=alisg" in stored_after_paste
    assert card.inboxBadge.text() == tr("accounts.inbox_unchecked")
    assert tr("accounts.cookies_cloud_ready", count=3) in card.authDetailLabel.text()

    # Reset to bare cookie & verify: first captcha_detected=True, then local-only, then upgraded cloud-ready
    live_acc = win.state.accounts[0]
    secrets.set_account_cookie("a1", "sessionid=bare_only_2")
    live_acc.inbox_verified = True
    card.load_profile(live_acc, has_cookie=True, cookie_payload="sessionid=bare_only_2")
    assert card.inboxBadge.text() == tr("accounts.local_only_badge")
    assert card.inboxBadge.level() == "warning"
    assert tr("accounts.cookies_local_only", count=1) in card.authDetailLabel.text()

    # Captcha detected keeps unverified / turns off spinner
    live_acc.inbox_verified = False
    card.load_profile(live_acc, has_cookie=True, cookie_payload="sessionid=bare_only_2")
    monkeypatch.setattr(
        "gui.pages.accounts_page.verify_inbox_headless",
        lambda payload, status_cb=None: (
            status_cb("Verifying /messages inbox...") if status_cb else None,
            {
                "ok": False,
                "inbox_verified": False,
                "captcha_detected": True,
                "username": "pasted_regional",
                "error": "CaptchaVerificationRequired",
            },
        )[-1],
    )
    ap._start_verify_inbox("a1")
    _pump_until(qapp, lambda: not win.workers.is_running("verify_inbox_a1"), timeout=4.0)
    qapp.processEvents()
    assert card.verifySpinner.isHidden() is True
    assert live_acc.inbox_verified is False

    # Successful verify upgrades cookie_text and flips badge to Verified
    monkeypatch.setattr(
        "gui.pages.accounts_page.verify_inbox_headless",
        lambda payload, status_cb=None: (
            status_cb("Collecting full session cookies...") if status_cb else None,
            {
                "ok": True,
                "inbox_verified": True,
                "captcha_detected": False,
                "cloud_ready": True,
                "cookie_count": 4,
                "upgraded_cookie_text": "sessionid=bare_only_2; tt-target-idc=useast2a; store-idc=useast2a; msToken=ms1",
                "username": "verified_user",
            },
        )[-1],
    )
    ap._start_verify_inbox("a1")
    _pump_until(qapp, lambda: not win.workers.is_running("verify_inbox_a1"), timeout=4.0)
    qapp.processEvents()

    assert live_acc.inbox_verified is True
    assert live_acc.username == "verified_user"
    assert "tt-target-idc=useast2a" in secrets.get_account_cookie("a1")
    assert card.inboxBadge.text() == tr("accounts.cloud_ready_badge")
    assert card.inboxBadge.level() == "success"
    assert tr("accounts.cookies_cloud_ready", count=4) in card.authDetailLabel.text()


def test_v110_content_page_timezone_visual_cloud_schedule_and_bare_deploy_confirm(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    acc = AccountProfile(id="a1", name="Main", friends=["alice"])
    state = AppState(
        accounts=[acc],
        mode="both",
        timezone="Asia/Jakarta",
        local_times=["08:00"],
        cloud_times=["08:00"],
        cloud_cron="0 1 * * *",
        github_private=True,
        github_repo_name="my-streak",
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=bare_deploy_sid")
    secrets.set_github_token("ghp_v110_token_12345")

    aut = win.automation_interface
    sp = win.settings_interface
    aut.refresh_from_state()
    qapp.processEvents()

    # Unified Advanced drawer collapsed by default & delay note visible
    assert aut.advancedContainer.isHidden() is True
    assert aut.cloudDelayNote.text() == tr("schedule.cloud_delay_note")
    assert aut.cloudTimesList.count() == 1
    assert "08:00 WIB" in aut.cloudTimesList.item(0).text()

    # Visual cloudTimePicker add + chip remove
    aut.cloudTimePicker.setTime(QTime(20, 30))
    aut.addCloudTimeBtn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:00", "20:30"]
    assert win.state.cloud_cron == "0 1 * * *\n30 13 * * *"
    assert aut.cloudTimesList.count() == 2

    aut.cloudTimesChips.chip_at(0).removeBtn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["20:30"]
    assert win.state.cloud_cron == "30 13 * * *"

    # Quick-fill off-peak presets
    aut.preset2Btn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:15", "20:15"]
    assert win.state.cloud_cron == "15 1,13 * * *"
    assert "08:15, 20:15 WIB" in aut.cronSummaryLabel.text()

    aut.preset3Btn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:15", "14:15", "20:15"]
    assert win.state.cloud_cron == "15 1,7,13 * * *"

    aut.preset1Btn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:15"]
    assert win.state.cloud_cron == "15 1 * * *"

    # Single-source Timezone switch in Settings updates Automation pill, chips, and non-dirty cron
    idx_utc = sp.timezoneCombo.findData("UTC")
    assert idx_utc >= 0
    sp.timezoneCombo.setCurrentIndex(idx_utc)
    qapp.processEvents()
    assert win.state.timezone == "UTC"
    assert "UTC" in aut.tzValueLabel.text()
    assert "08:15 UTC" in aut.cloudTimesList.item(0).text()
    assert aut.customCronEdit.text() == "15 8 * * *"

    # Unified Advanced drawer expands customCronEdit and edits mark custom cron dirty
    aut.advancedToggleBtn.click()
    qapp.processEvents()
    assert aut.advancedContainer.isHidden() is False
    aut.customCronEdit.setText("15 2 * * *; 45 14 * * *")
    qapp.processEvents()
    assert aut._custom_cron_dirty is True
    assert win.state.cloud_cron == "15 2 * * *\n45 14 * * *"

    # Bare-session deploy confirmation via single ConsolidatedDeployDialog: Cancel aborts before GitHub.whoami
    whoami_calls: list[str] = []
    deployed_crons: list[str] = []
    monkeypatch.setattr(
        "gui.services.cloud_deploy.GitHub.whoami",
        lambda self: (whoami_calls.append("whoami"), {"login": "octocat", "scopes_header_present": True, "scopes": ["repo", "workflow"]})[-1],
    )
    monkeypatch.setattr(
        "gui.services.cloud_deploy.GitHub.create_private_repo_with_bot",
        lambda self, owner, repo_name, source_dir=None, cron_expr=None, progress_cb=None: (
            deployed_crons.append(str(cron_expr)),
            {"full_name": f"{owner}/{repo_name}", "html_url": f"https://github.com/{owner}/{repo_name}"},
        )[-1],
    )
    monkeypatch.setattr(
        "gui.services.cloud_deploy.GitHub.sync_secrets",
        lambda self, full_name, desired, managed_names=None: {"written": list(desired.keys()), "deleted": []},
    )

    monkeypatch.setattr("gui.pages.automation_page.ConsolidatedDeployDialog.exec", lambda self: 0)
    aut.deployBtn.click()
    qapp.processEvents()
    assert whoami_calls == []

    # Continue proceeds and passes multi-line cron_expr
    monkeypatch.setattr("gui.pages.automation_page.ConsolidatedDeployDialog.exec", lambda self: 1)
    aut.deployBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("deploy_github"), timeout=4.0)
    qapp.processEvents()
    assert whoami_calls == ["whoami"]
    assert deployed_crons == ["15 2 * * *\n45 14 * * *"]


def test_v110_settings_timezone_runpage_timestamps_i18n_and_layout_bounds(
    qapp: QApplication,
    make_window,
):
    from gui.services.i18n import TRANSLATIONS

    acc = AccountProfile(id="a1", name="Main", friends=["alice"])
    state = AppState(
        multi_account=True,
        accounts=[acc],
        mode="both",
        timezone="Asia/Jakarta",
        github_login="octocat",
        github_repo_name="my-streak",
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_ok; tt-target-idc=useast2a")
    secrets.set_github_token("ghp_tok_12345")

    sp = win.settings_interface
    cp = win.content_interface
    aut = win.automation_interface
    rp = win.run_interface

    assert sp.timezoneCard.minimumSizeHint().width() <= 585

    rp._populate_runs_table([
        {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "event": "schedule",
            "created_at": "2026-11-18T01:05:00Z",
        }
    ])
    qapp.processEvents()
    assert "Passed" in rp.runsTable.item(0, 0).text()
    assert rp.runsTable.item(0, 1).text() == "Scheduled"
    assert rp.runsTable.item(0, 2).text() == "2026-11-18 08:05 WIB"

    # Changing timezone in SettingsPage syncs RunPage timestamps and AutomationPage pill
    idx_ny = sp.timezoneCombo.findData("America/New_York")
    assert idx_ny >= 0
    sp.timezoneCombo.setCurrentIndex(idx_ny)
    qapp.processEvents()
    assert win.state.timezone == "America/New_York"

    rp.refresh_from_state()
    aut.sync_timezone_display()
    qapp.processEvents()
    assert rp.runsTable.item(0, 2).text() == "2026-11-17 20:05 ET"
    assert "ET" in aut.tzValueLabel.text()
    assert not hasattr(aut, "timezoneCombo")

    # Layout geometry check at 1080x760 with expanded sidebar across ALL 6 pages
    win.show()
    win.resize(1080, 760)
    win.navigationInterface.expand(useAni=False)
    qapp.processEvents()
    for route, page in (
        ("home", win.home_interface),
        ("accounts", win.accounts_interface),
        ("content", cp),
        ("automation", aut),
        ("run", rp),
        ("settings", sp),
    ):
        win.navigate_to(route)
        qapp.processEvents()
        assert page.horizontalScrollBar().maximum() == 0, f"{route} overflowed at 1080x760"
    assert sp.timezoneCard.minimumSizeHint().width() <= 585

    # Programmatic i18n parity check (EN == ID keys)
    assert set(TRANSLATIONS["en"].keys()) == set(TRANSLATIONS["id"].keys())


# ---------------------------------------------------------------------------
# 12. WinUI 3 Badges, Friend Chips, Time Chips, Decluttered Cloud UI & Privacy
# ---------------------------------------------------------------------------

def test_winui3_badges_friend_chips_time_chips_and_security_dialog(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    from qfluentwidgets import SimpleCardWidget
    from gui.services.theme import PILL_RADIUS
    from gui.widgets.security_dialog import SecurityPrivacyDialog

    assert PILL_RADIUS == 4

    acc = AccountProfile(
        id="a1",
        name="Main",
        friends=["alice"],
        username="demo_creator",
        inbox_verified=True,
        auth_method="guided (chrome)",
    )
    state = AppState(
        accounts=[acc],
        mode="both",
        timezone="Asia/Jakarta",
        local_times=["08:00", "20:00"],
        cloud_times=["08:15", "20:15"],
        cloud_cron="15 1,13 * * *",
        github_login="octocat",
        github_repo_name="tiktok-streak",
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_chip; tt-target-idc=useast2a")
    secrets.set_github_token("ghp_chip_test_token_12345")

    ap = win.accounts_interface
    ap.refresh_from_state()
    cp = win.content_interface
    cp.refresh_from_state()
    hp = win.home_interface
    hp.refresh_state()
    sp = win.settings_interface
    sp.refresh_from_state()
    qapp.processEvents()

    # 1) WinUI 3 StatusPill de-duplication on AccountCard
    card = ap._cards[0]
    assert card.usernameBadge.level() == "neutral"
    assert card.usernameBadge.showDot() is False
    assert card.usernameBadge.text() == "@demo_creator"
    assert card.friendsCountBadge.level() == "neutral"
    assert card.friendsCountBadge.showDot() is False
    assert card.inboxBadge.level() == "success"
    assert card.inboxBadge.showDot() is True
    assert f"border-radius: {PILL_RADIUS}px;" in card.inboxBadge.styleSheet()

    # 2) Friend chips input: single handle, TikTok URL, multi-paste, and chip X removal
    assert card.friendsEdit.isHidden() is True
    assert card.friendsChipBox.keys() == ["alice"]
    assert card.friendsChipBox.labels() == ["@alice"]

    card.friendInputEdit.setText("https://www.tiktok.com/@bob_user, @carol")
    card.addFriendBtn.click()
    qapp.processEvents()
    assert card.friendInputEdit.text() == ""
    assert card.profile.friends == ["alice", "bob_user", "carol"]
    assert card.friendsChipBox.keys() == ["alice", "bob_user", "carol"]
    assert card.friendsChipBox.count() == 3

    # Remove @bob_user by clicking its chip's X button
    card.friendsChipBox.chip_at(1).removeBtn.click()
    qapp.processEvents()
    assert card.profile.friends == ["alice", "carol"]
    assert card.friendsChipBox.keys() == ["alice", "carol"]

    # 3) AutomationPage removable time chips & unified Advanced disclosure drawer
    aut = win.automation_interface
    aut.refresh_from_state()
    qapp.processEvents()
    assert aut.timesList.isHidden() is True
    assert aut.cloudTimesList.isHidden() is True
    assert aut.localTimesChips.keys() == ["08:00", "20:00"]
    assert aut.cloudTimesChips.keys() == ["08:15", "20:15"]
    assert aut.cloudTimesChips.labels() == ["08:15 WIB", "20:15 WIB"]

    # Remove second cloud time chip via its X button
    aut.cloudTimesChips.chip_at(1).removeBtn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:15"]
    assert aut.cloudTimesChips.keys() == ["08:15"]

    # Attempting to remove the last remaining cloud time chip keeps it intact
    aut.cloudTimesChips.chip_at(0).removeBtn.click()
    qapp.processEvents()
    assert win.state.cloud_times == ["08:15"]
    assert aut.cloudTimesChips.count() == 1

    # Remove second local time chip via its X button
    aut.localTimesChips.chip_at(1).removeBtn.click()
    qapp.processEvents()
    assert win.state.local_times == ["08:00"]
    assert aut.localTimesChips.keys() == ["08:00"]

    # Unified Advanced (PAT, secrets-only, raw cron) drawer collapsed by default and toggles open
    assert aut.advancedContainer.isHidden() is True
    aut.advancedToggleBtn.click()
    qapp.processEvents()
    assert aut.advancedContainer.isHidden() is False

    # Static container cards use non-hoverable SimpleCardWidget / HeaderCardWidget
    assert not hasattr(ap, "browserDisclosureCard")
    assert not hasattr(ap, "browserCard")
    assert isinstance(hp.privacyCard, SimpleCardWidget)

    # Single-line Video Share header title, header videoBadge, Check Link wording, and collapsed CLI drawer
    assert cp.videoCard.headerLabel.wordWrap() is False
    assert cp.videoBadge.parentWidget() is cp.videoCard.headerView
    assert cp.resolveShortBtn.text() == "Check Link"
    win.show()
    qapp.processEvents()
    set_language("id")
    qapp.processEvents()
    assert cp.videoCard.headerLabel.wordWrap() is False
    assert cp.resolveShortBtn.text() == "Cek Link"
    set_language("en")
    qapp.processEvents()

    assert sp.cliExportCard.isExpand is False
    sp.cliExportCard.setExpand(True)
    qapp.processEvents()
    assert sp.cliExportCard.isExpand is True
    assert sp.workspaceDirEdit.minimumWidth() >= 200

    # 4) SecurityPrivacyDialog instantiates & opens cleanly from Home and Settings (Accounts duplicate removed)
    opened: list[str] = []
    monkeypatch.setattr(
        SecurityPrivacyDialog,
        "exec",
        lambda self: (opened.append(self.titleLabel.text()), 1)[-1],
    )
    hp.privacyBtn.click()
    sp.openSecurityBtn.click()
    qapp.processEvents()
    assert len(opened) == 2
    assert all(title == tr("security.dialog_title") for title in opened)


def test_run_bot_redacts_secrets_in_scheduled_run_log(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    cfg_dir = tmp_path / "redact_cfg"
    ws_dir = tmp_path / "redact_ws"
    ws_dir.mkdir(parents=True, exist_ok=True)

    script = f"""
import logging, pathlib, sys, types
from unittest.mock import patch

mem = {{}}
sys.modules['keyring'] = types.SimpleNamespace(
    get_password=lambda s, k: mem.get((s, k)),
    set_password=lambda s, k, v: mem.__setitem__((s, k), v),
    delete_password=lambda s, k: mem.pop((s, k), None),
)

import run_gui
from gui.services.storage import AccountProfile, AppState, ConfigStore, SecretStore

cfg_dir = pathlib.Path({str(cfg_dir)!r})
ws_dir = pathlib.Path({str(ws_dir)!r})
store = ConfigStore(cfg_dir)
secrets = SecretStore(cfg_dir)
acc = AccountProfile(id='acc_1', name='Main', friends=['alice'])
store.save(AppState(accounts=[acc], video_enabled=False, photo_enabled=False, workspace_dir=str(ws_dir)))
secrets.set_account_cookie('acc_1', 'sessionid=ultra_secret_disk_cookie_999')
secrets.set_github_token('ghp_ultraSecretDiskToken999')

def fake_run(**_kwargs):
    logging.getLogger('tiktok-streak').info(
        'Leaked cookie sessionid=ultra_secret_disk_cookie_999 and ghp_ultraSecretDiskToken999'
    )
    return {{'ok': True, 'results': [], 'elapsed': 1, 'critical_failure': False}}

with patch('main.run', new=fake_run):
    rc = run_gui.main(['--run-bot', '--config-dir', str(cfg_dir)])

assert rc == 0
log_txt = (cfg_dir / 'scheduled_run.log').read_text(encoding='utf-8')
assert 'ultra_secret_disk_cookie_999' not in log_txt
assert 'ghp_ultraSecretDiskToken999' not in log_txt
assert '<REDACTED' in log_txt
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"







# ---------------------------------------------------------------------------
# 13. Cloud Stop/Pause, Bot Repo Discovery, Headful Hint & Auto-Updater UI
# ---------------------------------------------------------------------------

def test_cloud_control_bot_discovery_headful_hint_and_updater_ui(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    from qfluentwidgets import MessageBox
    from gui.version import APP_VERSION
    from gui.widgets.update_dialog import UpdateDialog

    acc = AccountProfile(id="a1", name="Main", friends=["alice"], username="tester", inbox_verified=True)
    state = AppState(
        mode="cloud",
        accounts=[acc],
        github_login="octocat",
        github_repo_name="tiktok-streak",
        github_private=True,
        headless_local=True,
        video_enabled=False,
        photo_enabled=False,
        last_deployed_version="1.2.0",
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_ok; tt-target-idc=useast5")
    secrets.set_github_token("ghp_tok123")
    qapp.processEvents()

    hp = win.home_interface
    cp = win.content_interface
    aut = win.automation_interface
    rp = win.run_interface
    sp = win.settings_interface

    # 1) HomePage shows Cloud Bot update banner when last_deployed_version < APP_VERSION
    hp.refresh_state()
    qapp.processEvents()
    assert hp.cloudSyncBannerCard.isHidden() is False
    assert "v1.2.0" in hp.cloudSyncBannerDesc.text()
    assert APP_VERSION in hp.cloudSyncBannerDesc.text()

    # 2) RunPage decoupled headful hint stays visible & translates cleanly
    assert rp.showBrowserHintLabel.isHidden() is False
    rp.showBrowserSwitch.setChecked(True)
    qapp.processEvents()
    assert win.state.headless_local is False
    assert aut.showBrowserSwitch.isChecked() is True
    set_language("id")
    qapp.processEvents()
    assert "dikunci" in rp.showBrowserHintLabel.text().lower() or "mengunci" in rp.showBrowserHintLabel.text().lower()
    set_language("en")
    qapp.processEvents()

    # 3) RunPage Cloud Monitor: Stop Run enabled only on active run; Pause/Resume syncs workflow badge & ContentPage
    fake_runs = [
        {"id": 901, "status": "in_progress", "conclusion": None, "event": "workflow_dispatch", "created_at": "2026-11-20T01:00:00Z", "html_url": "https://github.com/octocat/tiktok-streak/actions/runs/901"},
        {"id": 902, "status": "completed", "conclusion": "success", "event": "schedule", "created_at": "2026-11-19T01:00:00Z", "html_url": "https://github.com/octocat/tiktok-streak/actions/runs/902"},
    ]
    cancelled_calls: list[tuple[str, int]] = []
    paused_calls: list[str] = []
    resumed_calls: list[str] = []

    monkeypatch.setattr("gui.pages.run_page.GitHub.runs", lambda self, fn, wf, limit=8: fake_runs)
    monkeypatch.setattr("gui.pages.run_page.GitHub.get_workflow", lambda self, fn, wf: {"id": 1, "state": "active"})
    monkeypatch.setattr(
        "gui.pages.run_page.GitHub.cancel_run",
        lambda self, fn, rid: cancelled_calls.append((fn, int(rid))),
    )
    monkeypatch.setattr(
        "gui.pages.run_page.GitHub.pause_workflow",
        lambda self, fn, wf, cancel_running=True: (paused_calls.append(fn), {"paused": True, "cancelled_run_ids": [901]})[-1],
    )
    monkeypatch.setattr(
        "gui.pages.run_page.GitHub.enable_workflow",
        lambda self, fn, wf: resumed_calls.append(fn),
    )

    rp.refresh_cloud_runs()
    _pump_until(qapp, lambda: not win.workers.is_running("fetch_cloud_runs"), timeout=4.0)
    qapp.processEvents()
    assert rp.runsTable.rowCount() == 2
    assert rp.cloudWorkflowBadge.isHidden() is False
    assert rp.cloudWorkflowBadge.text() == tr("cloud.repo_state_active")
    assert rp.cancelCloudRunBtn.isEnabled() is True

    rp.runsTable.selectRow(1)
    qapp.processEvents()
    assert rp.cancelCloudRunBtn.isEnabled() is False

    rp.runsTable.selectRow(0)
    qapp.processEvents()
    rp.cancelCloudRunBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("cancel_cloud_run"), timeout=4.0)
    assert cancelled_calls == [("octocat/tiktok-streak", 901)]

    rp.toggleCloudScheduleBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("toggle_cloud_schedule"), timeout=4.0)
    qapp.processEvents()
    assert paused_calls == ["octocat/tiktok-streak"]
    assert rp.cloudWorkflowBadge.text() == tr("cloud.repo_state_paused")
    assert rp.toggleCloudScheduleBtn.text() == tr("cloud.resume_short")

    rp.toggleCloudScheduleBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("toggle_cloud_schedule"), timeout=4.0)
    qapp.processEvents()
    assert resumed_calls == ["octocat/tiktok-streak"]
    assert rp.cloudWorkflowBadge.text() == tr("cloud.repo_state_active")

    # 4) AutomationPage Detected Bot Repos & Inline Pre-Deploy Readiness + ConsolidatedDeployDialog
    fake_discovery = {
        "all_repos": [
            {"full_name": "octocat/tiktok-streak", "name": "tiktok-streak", "owner": "octocat", "private": True, "archived": False, "fork": False, "default_branch": "main", "html_url": ""},
            {"full_name": "octocat/tiktok-streak-2", "name": "tiktok-streak-2", "owner": "octocat", "private": False, "archived": False, "fork": True, "default_branch": "main", "html_url": ""},
            {"full_name": "octocat/website", "name": "website", "owner": "octocat", "private": False, "archived": False, "fork": False, "default_branch": "main", "html_url": ""},
        ],
        "bot_repos": [
            {"full_name": "octocat/tiktok-streak", "name": "tiktok-streak", "owner": "octocat", "private": True, "fork": False, "html_url": "", "workflow_id": 1, "workflow_state": "active", "active": True},
            {"full_name": "octocat/tiktok-streak-2", "name": "tiktok-streak-2", "owner": "octocat", "private": False, "fork": True, "html_url": "", "workflow_id": 2, "workflow_state": "active", "active": True},
        ],
        "active_bot_repos": [
            {"full_name": "octocat/tiktok-streak", "active": True},
            {"full_name": "octocat/tiktok-streak-2", "active": True},
        ],
        "has_duplicates": True,
        "scanned_count": 3,
    }
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.discover_bot_repos", lambda self, **kw: fake_discovery)
    aut.scan_bot_repos()
    _pump_until(qapp, lambda: not win.workers.is_running("discover_bot_repos"), timeout=4.0)
    qapp.processEvents()

    assert aut.botReposPanel.isHidden() is False
    assert aut.preflightIssuesBox.isHidden() is False
    assert aut.preflightRowDups.fixBtn.isHidden() is False
    assert len(aut._bot_repo_row_widgets) == 2
    assert isinstance(aut.scanBotReposBtn, TransparentToolButton)
    assert isinstance(aut._bot_repo_row_widgets[0].openBtn, TransparentToolButton)
    assert isinstance(rp.refreshRunsBtn, TransparentToolButton)
    assert "Updates" in aut.repoValidCaption.text()

    aut.repoNameEdit.setText("website")
    qapp.processEvents()
    assert aut.repoValidBadge.isHidden() is False
    assert aut.preflightRowRepo.fixBtn.isHidden() is False

    # Inline Allow Existing fix clears non-bot repo warning without popup
    aut.preflightRowRepo.fixBtn.click()
    qapp.processEvents()
    assert aut._allow_existing_non_bot is True
    assert aut._last_preflight["checks"][1]["status"] == "pass"

    aut.repoNameEdit.setText("brand-new-bot")
    qapp.processEvents()
    assert aut.repoValidBadge.isHidden() is True
    assert "New repository" in aut.repoValidCaption.text()

    # Deploy to brand-new-bot while 2 active bots exist triggers single ConsolidatedDeployDialog; accept => pauses old repos
    monkeypatch.setattr("gui.pages.automation_page.ConsolidatedDeployDialog.exec", lambda self: 1)
    paused_during_deploy: list[str] = []
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.whoami", lambda self: {"login": "octocat", "scopes_header_present": True, "scopes": ["repo", "workflow"]})
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.pause_workflow", lambda self, fn, wf, cancel_running=True: paused_during_deploy.append(fn) or {"paused": True})
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.create_private_repo_with_bot", lambda self, **kw: {"full_name": "octocat/brand-new-bot", "html_url": "https://github.com/octocat/brand-new-bot"})
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.sync_secrets", lambda self, fn, d, managed_names=None: {"written": list(d.keys()), "deleted": []})

    aut.deployBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("deploy_github"), timeout=4.0)
    qapp.processEvents()
    assert set(paused_during_deploy) == {"octocat/tiktok-streak", "octocat/tiktok-streak-2"}
    assert win.state.last_deployed_version == APP_VERSION
    hp.refresh_state()
    qapp.processEvents()
    assert hp.cloudSyncBannerCard.isHidden() is True

    # Test 1-click inline Pause Old button on preflightRowDups
    fresh_dup = {
        "all_repos": list(fake_discovery["all_repos"]),
        "bot_repos": [
            {"full_name": "octocat/tiktok-streak", "name": "tiktok-streak", "owner": "octocat", "private": True, "fork": False, "html_url": "", "workflow_id": 1, "workflow_state": "active", "active": True},
            {"full_name": "octocat/tiktok-streak-2", "name": "tiktok-streak-2", "owner": "octocat", "private": False, "fork": True, "html_url": "", "workflow_id": 2, "workflow_state": "active", "active": True},
        ],
        "active_bot_repos": [
            {"full_name": "octocat/tiktok-streak", "active": True},
            {"full_name": "octocat/tiktok-streak-2", "active": True},
        ],
        "has_duplicates": True,
        "scanned_count": 3,
    }
    monkeypatch.setattr("gui.services.cloud_deploy.GitHub.discover_bot_repos", lambda self, **kw: fresh_dup)
    aut.scan_bot_repos()
    _pump_until(qapp, lambda: not win.workers.is_running("discover_bot_repos"), timeout=4.0)
    qapp.processEvents()
    QTest.mouseClick(aut._bot_repo_row_widgets[0], Qt.LeftButton)
    qapp.processEvents()
    assert aut.preflightIssuesBox.isHidden() is False

    paused_others: list[str] = []
    monkeypatch.setattr(
        "gui.services.cloud_deploy.GitHub.pause_workflow",
        lambda self, fn, wf, cancel_running=True: paused_others.append(fn) or {"paused": True},
    )
    aut.preflightRowDups.fixBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("pause_other_bot_repos"), timeout=4.0)
    qapp.processEvents()
    assert paused_others == ["octocat/tiktok-streak-2"]
    assert aut.preflightPassBox.isHidden() is False
    assert len([r for r in aut._discovered_bot_repos if r.get("active")]) == 1
    assert len(aut._bot_repo_row_widgets) == 2
    assert aut._bot_repo_row_widgets[0].toggleBtn.width() == aut._bot_repo_row_widgets[1].toggleBtn.width()

    # Honest video verification badge states (disabled -> neutral while unverified -> green live -> red unavailable -> warning offline)
    assert cp.videoBadge._level == "neutral"
    assert cp.videoBadge.text() == tr("common.disabled")
    cp.videoSwitch.setChecked(True)
    qapp.processEvents()
    assert cp.videoBadge._level == "neutral"
    assert cp.videoBadge.text() == tr("content.video_unverified")
    cp.videoUrlEdit.setText("https://www.tiktok.com/@proofwereokay/video/7669608467589647629?is_from_webapp=1")
    qapp.processEvents()
    assert cp.videoBadge._level == "neutral"
    assert cp.videoBadge.text() == tr("content.video_unverified")

    cp._on_short_resolved(
        {
            "valid": True,
            "ok": True,
            "aweme_id": "7669608467589647629",
            "normalized_url": "https://www.tiktok.com/@proofwereokay/video/7669608467589647629",
            "verified": True,
            "availability": "available",
            "author": "proofwereokay",
            "error": None,
        }
    )
    qapp.processEvents()
    assert cp.videoUrlEdit.text() == "https://www.tiktok.com/@proofwereokay/video/7669608467589647629"
    assert cp.videoBadge.text() == tr("content.video_verified_author", author="proofwereokay")
    assert cp.videoBadge._level == "success"

    cp._on_short_resolved(
        {
            "valid": False,
            "ok": False,
            "aweme_id": "7684838545659317524",
            "normalized_url": "https://www.tiktok.com/@yutta/video/7684838545659317524",
            "verified": True,
            "availability": "unavailable",
            "author": None,
            "error": "This TikTok video is currently unavailable on TikTok (author_invalid).",
        }
    )
    qapp.processEvents()
    assert cp.videoBadge._level == "error"
    assert cp.videoBadge.text() == tr("content.video_unavailable")

    cp._on_short_resolved(
        {
            "valid": True,
            "ok": True,
            "aweme_id": "7669608467589647629",
            "normalized_url": "https://www.tiktok.com/@proofwereokay/video/7669608467589647629",
            "verified": False,
            "availability": "unverified",
            "author": None,
            "error": "Could not verify online: timed out",
        }
    )
    qapp.processEvents()
    assert cp.videoBadge._level == "warning"
    assert cp.videoBadge.text() == tr("content.video_verify_offline")

    # Editing URL resets verification state back to neutral unverified
    cp.videoUrlEdit.setText("https://www.tiktok.com/@another/video/7123456789012345678")
    qapp.processEvents()
    assert cp.videoBadge._level == "neutral"
    assert cp.videoBadge.text() == tr("content.video_unverified")

    # Ensure redesigned HomePage banner, ContentPage, AutomationPage & RunPage respect viewport bounds in both EN and ID
    assert not hasattr(hp, "updateBannerCard")
    win.resize(1080, 760)
    win.navigationInterface.expand(useAni=False)
    win.show()
    for lang in ("en", "id"):
        set_language(lang)
        qapp.processEvents()
        win.navigate_to("automation")
        qapp.processEvents()
        assert aut._bot_repo_row_widgets[0].toggleBtn.width() == aut._bot_repo_row_widgets[1].toggleBtn.width()
        for route, page in (
            ("home", hp),
            ("accounts", win.accounts_interface),
            ("content", cp),
            ("automation", aut),
            ("run", rp),
            ("settings", sp),
        ):
            win.navigate_to(route)
            qapp.processEvents()
            assert page.horizontalScrollBar().maximum() == 0, f"{page.objectName()} overflowed in {lang}"
    set_language("en")
    qapp.processEvents()

    # 5) SettingsPage About & Updates + Sidebar Update Item (above Settings) + Background Download + Skip Version
    assert APP_VERSION in sp.versionCard.titleLabel.text()
    assert sp.autoUpdateSwitch.isChecked() is True
    sp.autoUpdateSwitch.setChecked(False)
    qapp.processEvents()
    assert win.state.auto_check_updates is False

    upd_nav = win._nav_items["update"]
    stg_nav = win._nav_items["settings"]
    assert upd_nav.isHidden() is True
    assert win._update_nav_badge is not None and win._update_nav_badge.isHidden() is True

    fake_upd = {
        "ok": True,
        "current_version": APP_VERSION,
        "latest_version": "9.9.9",
        "tag_name": "v9.9.9",
        "has_update": True,
        "download_url": "https://github.com/alexanderfrit/tiktok-streak/releases/download/v9.9.9/TikTokStreakSetup.exe",
        "asset_name": "TikTokStreakSetup.exe",
        "asset_size": 1048576,
        "asset_sha256": "a" * 64,
        "html_url": "https://github.com/alexanderfrit/tiktok-streak/releases/tag/v9.9.9",
        "release_notes": "Critical recipient safety release",
    }
    win._on_update_check_finished(fake_upd, manual=False)
    qapp.processEvents()
    assert upd_nav.isHidden() is False
    assert "9.9.9" in upd_nav.text()
    assert win._update_nav_badge.isHidden() is False
    bottom_lay = win.navigationInterface.panel.bottomLayout
    assert 0 <= bottom_lay.indexOf(upd_nav) < bottom_lay.indexOf(stg_nav)
    assert "9.9.9" in sp.versionCard.contentLabel.text()

    rich_upd = dict(fake_upd)
    rich_upd["release_notes"] = (
        "### What's New\n\n"
        "#### Recipient Safety\n"
        "- **Fixed `header`:** Reads `<p data-e2e=\"chat-uniqueid\">@friend_one</p>` cleanly."
    )
    dlg = UpdateDialog(
        parent=win,
        update_info=rich_upd,
        worker_controller=win.workers,
        start_download_cb=lambda: None,
        cancel_download_cb=win.cancel_app_update_download,
        download_state="downloading",
        downloaded_bytes=524288,
        total_bytes=1048576,
    )
    assert "9.9.9" in dlg.titleLabel.text()
    assert "1.0 MB" in dlg.statusLabel.text()
    plain = dlg.notesEdit.toPlainText()
    assert "What's New" in plain and "Recipient Safety" in plain
    assert '<p data-e2e="chat-uniqueid">@friend_one</p>' in plain
    assert "###" not in plain and "**" not in plain
    assert "Background" in dlg.yesButton.text()
    assert "Cancel" in dlg.cancelButton.text()
    assert dlg.skipBtn.isHidden() is True
    dlg.close()

    # Background download keeps running when user uses MainWindow tabs, and sidebar tracks progress
    started_dl: list[str] = []
    monkeypatch.setattr(win.workers, "start_task", lambda *a, **kw: started_dl.append(str(kw.get("task_key"))))
    win.start_app_update_download()
    assert started_dl == ["app_update_download"]
    assert win._update_download_state == "downloading"
    assert win._update_cancel_event is not None and not win._update_cancel_event.is_set()
    win._on_update_download_progress({"downloaded": 524288, "total": 1048576})
    qapp.processEvents()
    assert win._update_nav_badge.text() == "50%"
    win.navigate_to("accounts")
    qapp.processEvents()
    assert win._update_download_state == "downloading"
    assert not win._update_cancel_event.is_set()
    win.cancel_app_update_download()
    qapp.processEvents()
    assert win._update_download_state == "idle"
    assert "9.9.9" in upd_nav.text()

    # Startup auto-check cadence (60s guard) & single auto-modal prompt per launch
    orig_smoke = win.smoke_test
    try:
        win.smoke_test = False
        now = 1_800_000_000.0
        win.state.auto_check_updates = True
        win.state.last_update_check_ts = 0
        assert win._should_auto_check_updates(now_ts=now) is True
        win.state.last_update_check_ts = int(now - 30)
        assert win._should_auto_check_updates(now_ts=now) is False
        win.state.last_update_check_ts = int(now - 61)
        assert win._should_auto_check_updates(now_ts=now) is True
        win.state.last_update_check_ts = int(now + 120)
        assert win._should_auto_check_updates(now_ts=now) is True
        win.state.auto_check_updates = False
        assert win._should_auto_check_updates(now_ts=now) is False

        exec_calls: list[int] = []
        monkeypatch.setattr(UpdateDialog, "exec", lambda self: exec_calls.append(1) or 0)
        win._startup_update_prompted = False
        win.state.skipped_update_version = ""
        win._on_update_check_finished(fake_upd, manual=False)
        assert len(exec_calls) == 1
        assert upd_nav.isHidden() is False
        # Repeat non-manual check within the same launch must not re-pop modal
        win._on_update_check_finished(fake_upd, manual=False)
        assert len(exec_calls) == 1
        # Manual check always opens dialog
        win._on_update_check_finished(fake_upd, manual=True)
        assert len(exec_calls) == 2
    finally:
        win.smoke_test = orig_smoke
        win.state.skipped_update_version = ""
        win.state.auto_check_updates = False

    win._skip_update_version("9.9.9")
    qapp.processEvents()
    assert win.state.skipped_update_version == "9.9.9"
    assert upd_nav.isHidden() is True
    assert win._update_nav_badge.isHidden() is True


# ---------------------------------------------------------------------------
# 14. Fluent Redesign IA, DM Preview, Modals, Pivot, Alignment & Bounds
# ---------------------------------------------------------------------------

def test_fluent_redesign_preview_preflight_modals_pivot_and_bounds(
    qapp: QApplication,
    make_window,
    monkeypatch: pytest.MonkeyPatch,
):
    from gui.widgets.fluent_redesign import ConsolidatedDeployDialog, DMPreviewPanel, ResetConfirmDialog
    from gui.widgets.security_dialog import SecurityPrivacyDialog

    acc = AccountProfile(
        id="a1",
        name="Main",
        friends=["alice", "bob"],
        username="main_user",
        inbox_verified=True,
    )
    state = AppState(
        accounts=[acc],
        mode="both",
        timezone="Asia/Jakarta",
        github_login="octocat",
        github_repo_name="tiktok-streak",
        message="Halo 🔥",
        video_enabled=False,
        share_times=2,
        photo_enabled=False,
        last_deployed_version="1.0.0",
    )
    win, _store, secrets, _mem, tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_redesign; tt-target-idc=useast2a")
    secrets.set_github_token("ghp_redesign_token_12345")

    win.resize(1080, 760)
    win.navigationInterface.expand(useAni=False)
    win.show()
    qapp.processEvents()

    hp = win.home_interface
    ap = win.accounts_interface
    cp = win.content_interface
    aut = win.automation_interface
    rp = win.run_interface
    sp = win.settings_interface

    # 1) HomePage: 1:1 checklist routing, cloud drift -> automation, Telegram -> settings:general, quick_run_requested
    nav_seen: list[str] = []
    hp.navigate_requested.connect(nav_seen.append)
    hp.step1.goBtn.click()
    hp.step2.goBtn.click()
    hp.step3.goBtn.click()
    hp.step4.goBtn.click()
    hp.cloudSyncGoBtn.click()
    hp.telegramSettingsBtn.click()
    qapp.processEvents()
    assert nav_seen == [
        "accounts",
        "content",
        "automation",
        "run",
        "automation",
        "settings:general",
    ]
    assert win.stackedWidget.currentWidget() is sp
    assert sp.tabStack.currentWidget() is sp.generalTab

    quick_runs: list[str] = []
    hp.quick_run_requested.connect(quick_runs.append)
    monkeypatch.setattr(rp, "start_smoke_test", lambda: None)
    monkeypatch.setattr(rp, "start_full_run", lambda: None)
    win.navigate_to("home")
    qapp.processEvents()
    hp.quickTestBtn.click()
    hp.runAllBtn.click()
    qapp.processEvents()
    assert quick_runs == ["smoke", "full"]

    # 1b) HomePage Local-First Privacy bar must not collapse horizontally (EN & ID)
    assert hp.privacyTitle.width() >= 150
    assert hp.privacyDesc.width() >= 150
    assert hp.privacyCard.height() <= 115

    # 2) AccountsPage: BrowserImportDialog replaces disconnected bottom disclosure;
    #    both BrowserImportDialog and CookieDialog offer 1-click Guided Browser Login escape hatch
    from gui.widgets.browser_import_dialog import BrowserImportDialog

    win.navigate_to("accounts")
    qapp.processEvents()
    assert not hasattr(ap, "browserDisclosureCard")
    assert not hasattr(ap, "browserCard")

    guided_calls: list[str] = []
    monkeypatch.setattr(ap, "_start_guided_login", lambda aid: guided_calls.append(aid))

    def _fake_browser_import_escape(self):
        self.switch_to_browser_login = True
        return 0

    monkeypatch.setattr(BrowserImportDialog, "exec", _fake_browser_import_escape)
    ap._cards[0].importBrowserBtn.click()
    qapp.processEvents()
    assert guided_calls == ["a1"]

    dlg_cookie = CookieDialog(win)
    dlg_cookie.textEdit.setPlainText("sessionid=bare_only_token")
    qapp.processEvents()
    assert dlg_cookie.bareWarningBanner.isHidden() is False
    assert dlg_cookie.switch_to_browser_login is False
    dlg_cookie.useBrowserLoginBtn.click()
    qapp.processEvents()
    assert dlg_cookie.switch_to_browser_login is True
    dlg_cookie.close()

    def _fake_exec_escape(self):
        self.switch_to_browser_login = True
        return 0

    monkeypatch.setattr(CookieDialog, "exec", _fake_exec_escape)
    ap._open_paste_dialog("a1")
    qapp.processEvents()
    assert guided_calls == ["a1", "a1"]

    # 3) ContentPage: 1:1 TikTok Web DMPreviewPanel, real cover rendering, cramped stacking & Pet points
    win.navigate_to("content")
    qapp.processEvents()
    assert cp.dmPreviewPanel.previewFriendChip.text() == "@alice"
    assert cp.dmPreviewPanel.chatHeaderHandle.text() == "@alice"
    assert "+1" in cp.petGrowthBadge.text()
    assert "+1" in cp.dmPreviewPanel.petTotalBadge.text()

    setTheme(Theme.DARK, save=False)
    qapp.processEvents()
    assert "#00a2c9" in cp.dmPreviewPanel.msgBubble.styleSheet().lower()
    setTheme(Theme.LIGHT, save=False)
    qapp.processEvents()
    assert "rgba(22, 24, 35, 0.06)" in cp.dmPreviewPanel.msgBubble.styleSheet().lower()
    setTheme(Theme.DARK, save=False)
    qapp.processEvents()

    cp.videoSwitch.setChecked(True)
    cp.shareTimesSpin.setValue(2)
    qapp.processEvents()
    assert "+3" in cp.petGrowthBadge.text()
    assert "+3" in cp.dmPreviewPanel.petTotalBadge.text()
    assert cp.dmPreviewPanel.videoCard._share_times == 2

    from PySide6.QtGui import QImage
    from PySide6.QtCore import QBuffer, QIODevice
    img = QImage(32, 48, QImage.Format_RGB32)
    img.fill(0xFE2C55)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    cp.dmPreviewPanel.set_video_media(cover_bytes=bytes(buf.data()), author_nickname="DuneMovie")
    assert not cp.dmPreviewPanel.videoCard._cover.isNull()

    cp.photoSwitch.setChecked(True)
    qapp.processEvents()
    assert "+7" in cp.petGrowthBadge.text()
    assert "+7" in cp.dmPreviewPanel.petTotalBadge.text()

    cp.shareTimesSpin.setValue(1)
    qapp.processEvents()
    assert "+5" in cp.petGrowthBadge.text()
    assert "+5" in cp.dmPreviewPanel.petTotalBadge.text()

    # Cramped resize switches preview cleanly without horizontal overflow
    standalone_dm = DMPreviewPanel()
    standalone_dm.resize(210, 350)
    standalone_dm.show()
    qapp.processEvents()
    assert standalone_dm._density_tier == "cramped"
    assert standalone_dm.chatComposer.isHidden() is True
    assert standalone_dm.msgAvatar.isHidden() is True
    standalone_dm.close()

    # Narrow window stacks DMPreviewPanel below the editor cleanly
    win.resize(740, 680)
    _pump_until(qapp, lambda: cp._is_stacked and cp.horizontalScrollBar().maximum() == 0, timeout=1.5)
    assert cp._is_stacked is True
    assert cp.horizontalScrollBar().maximum() == 0
    win.resize(1080, 760)
    _pump_until(qapp, lambda: not cp._is_stacked, timeout=1.5)
    assert cp._is_stacked is False

    cp.nextAutomationBtn.click()
    qapp.processEvents()
    assert win.stackedWidget.currentWidget() is aut

    # 4) AutomationPage: single-source timezone pill, unified Show Browser switch, no GitHub Sign Out, ConsolidatedDeployDialog
    assert not hasattr(aut, "timezoneCombo")
    assert not hasattr(aut, "signOutGithubBtn")
    aut.showBrowserSwitch.setChecked(True)
    qapp.processEvents()
    assert win.state.headless_local is False
    assert rp.showBrowserSwitch.isChecked() is True
    aut.showBrowserSwitch.setChecked(False)
    qapp.processEvents()
    assert win.state.headless_local is True
    assert rp.showBrowserSwitch.isChecked() is False

    aut.changeTzBtn.click()
    qapp.processEvents()
    assert win.stackedWidget.currentWidget() is sp
    assert sp.tabStack.currentWidget() is sp.generalTab

    cdlg = ConsolidatedDeployDialog(
        win,
        [{"code": "duplicates", "text": "2 active bots"}],
        has_duplicates=True,
    )
    assert cdlg.choice == "pause"
    assert cdlg.keepBtn is not None
    cdlg._on_keep_clicked()
    assert cdlg.choice == "keep"
    cdlg.close()

    # 5) SettingsPage: Pivot tabs, no 'Single Source' copy, strict L->R Input->Action ordering, dialogs
    from qfluentwidgets import StrongBodyLabel

    assert not hasattr(sp, "tzSingleSourcePill")
    win.navigate_to("run")
    qapp.processEvents()
    assert isinstance(rp.repoTargetLabel, StrongBodyLabel)
    assert rp.cloudInfoBox.isHidden() is False
    assert rp.cloudScheduleLabel.text().strip() != ""

    win.navigate_to("settings:general")
    qapp.processEvents()
    assert sp.tabStack.currentWidget() is sp.generalTab
    assert sp.tgTokenEdit.x() < sp.botFatherLink.x()
    assert sp.tgChatEdit.x() < sp.tgDetectBtn.x() < sp.tgTestBtn.x()

    win.navigate_to("settings:accounts")
    qapp.processEvents()
    assert sp.tabStack.currentWidget() is sp.accountsTab
    assert sp.ghSignoutBtn.isEnabled() is True

    sec_dlg = SecurityPrivacyDialog(win, config_dir=tmp)
    assert len(sec_dlg.pointCards) == 4
    assert sec_dlg.techDetailsBox.isHidden() is True
    sec_dlg.techToggleBtn.click()
    qapp.processEvents()
    assert sec_dlg.techDetailsBox.isHidden() is False
    tech_txt = sec_dlg.techDetailsLabel.text()
    assert "DPAPI" in tech_txt
    assert "SealedBox" in tech_txt
    assert "?mode=ro" in tech_txt
    sec_dlg.close()

    reset_dlg = ResetConfirmDialog(win)
    scope_texts = " ".join(lbl.text() for lbl in reset_dlg.scopeLabels)
    assert "GitHub" in scope_texts
    assert reset_dlg.scopeLabels[-1].text().startswith("✓")
    reset_dlg.close()

    # 6) Exhaustive zero horizontal overflow across all 6 pages x EN/ID x 3 Automation modes x 2 Settings Pivot tabs
    for lang in ("en", "id"):
        set_language(lang)
        qapp.processEvents()
        for route, page in (
            ("home", hp),
            ("accounts", ap),
            ("content", cp),
            ("run", rp),
        ):
            win.navigate_to(route)
            qapp.processEvents()
            assert page.horizontalScrollBar().maximum() == 0, f"{route} overflowed in {lang}"

        win.navigate_to("automation")
        for mode_key in ("local", "cloud", "both"):
            aut.modeSegment.setCurrentItem(mode_key)
            aut._on_mode_changed(mode_key)
            qapp.processEvents()
            assert aut.horizontalScrollBar().maximum() == 0, (
                f"automation ({mode_key}) overflowed in {lang}"
            )

        for tab_key in ("general", "accounts"):
            win.navigate_to(f"settings:{tab_key}")
            qapp.processEvents()
            assert sp.horizontalScrollBar().maximum() == 0, (
                f"settings:{tab_key} overflowed in {lang}"
            )

    set_language("en")
    qapp.processEvents()
