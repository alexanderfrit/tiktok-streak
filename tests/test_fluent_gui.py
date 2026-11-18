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
from PySide6.QtWidgets import QApplication, QDialog
from qfluentwidgets import (
    InfoLevel,
    ListWidget,
    NavigationDisplayMode,
    PrimaryPushButton,
    Theme,
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
    monkeypatch.setattr("gui.pages.content_page.WindowsScheduler.list_tasks", lambda self: [])

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
        win.run_interface,
        win.settings_interface,
    ]
    names = [p.objectName() for p in pages]
    assert names == [
        "home_interface",
        "accounts_interface",
        "content_interface",
        "run_interface",
        "settings_interface",
    ]
    assert len(set(names)) == 5

    # Navigate across all 5 sub-interfaces
    for route, expected_widget in (
        ("accounts", win.accounts_interface),
        ("content", win.content_interface),
        ("run", win.run_interface),
        ("settings", win.settings_interface),
        ("home", win.home_interface),
    ):
        win.navigate_to(route)
        qapp.processEvents()
        assert win.stackedWidget.currentWidget() is expected_widget

    # EN -> ID -> EN retranslation via SettingsPage language combo
    idx_id = win.settings_interface.langCombo.findData("id")
    assert idx_id >= 0
    win.settings_interface.langCombo.setCurrentIndex(idx_id)
    qapp.processEvents()

    assert i18n.get_language() == "id"
    assert win.windowTitle() == "Pengaturan TikTok Streak"
    assert win.home_interface.welcomeTitle.text() == "Ringkasan"
    assert win.accounts_interface.titleLabel.text() == "Akun TikTok"
    assert win.accounts_interface.modeSegment.items["single"].text() == "1 Akun"
    assert win.content_interface.titleLabel.text() == "Pesan, Media & Teman"
    assert win.run_interface.titleLabel.text() == "Jalankan & Log"
    assert win.run_interface.logViewer.titleLabel.text() == "Log & Diagnostik"
    assert win.settings_interface.titleLabel.text() == "Pengaturan & Workspace"
    assert win.settings_interface.themeCombo.itemText(0) == "Sistem"

    idx_en = win.settings_interface.langCombo.findData("en")
    assert idx_en >= 0
    win.settings_interface.langCombo.setCurrentIndex(idx_en)
    qapp.processEvents()

    assert i18n.get_language() == "en"
    assert win.windowTitle() == "TikTok Streak Setup"
    assert win.accounts_interface.titleLabel.text() == "TikTok Accounts"
    assert win.content_interface.titleLabel.text() == "Message, Media & Friends"
    assert win.run_interface.titleLabel.text() == "Run & Logs"
    assert win.settings_interface.titleLabel.text() == "Settings & Workspace"
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
    assert "[v20]" in ap.browserCombo.currentText()
    assert ap.browserBadge.text() == tr("accounts.v20_badge")
    assert ap.browserNote.text() == tr("err.v20")

    # Attempting to import a v20 source warns immediately without reading DB
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
    assert ap._cards[0].inboxBadge.text() == tr("accounts.inbox_verified")


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

    # Disable video & photo via UI controls
    cp.videoSwitch.setChecked(False)
    cp.resetPhotoBtn.click()
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

    # Use Default Photo clears custom photo & omits PHOTO_DATA_URL
    cp.defaultPhotoBtn.click()
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
        cloud_cron="0 1,13 * * *",
        video_enabled=False,
        photo_enabled=False,
    )
    win, _store, secrets, _mem, _tmp = make_window(state=state, smoke_test=True)
    secrets.set_account_cookie("a1", "sessionid=sid_deploy")
    secrets.set_github_token("ghp_deploy_token_1234567890")

    cp = win.content_interface
    cp.refresh_from_state()
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

    monkeypatch.setattr("gui.pages.content_page.GitHub.whoami", fake_whoami)
    monkeypatch.setattr("gui.pages.content_page.GitHub.create_private_repo_with_bot", fake_create_private)
    monkeypatch.setattr("gui.pages.content_page.GitHub.sync_secrets", fake_sync_secrets)

    cp.deployBtn.click()
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
    cp.timePicker.setTime(QTime(20, 30))
    cp.addTimeBtn.click()
    qapp.processEvents()
    assert win.state.local_times == ["08:00", "20:30"]

    sync_calls: list[list[str]] = []
    monkeypatch.setattr(
        "gui.pages.content_page.WindowsScheduler.sync_daily_tasks",
        lambda self, times: (sync_calls.append(list(times)), {"ok": True, "created": [{"name": "T1"}, {"name": "T2"}], "removed": []})[-1],
    )
    cp.applyWinScheduleBtn.click()
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

    # 1) Quick test (1-friend visible run) + LogViewer redaction
    smoke_calls: list[dict] = []

    def fake_smoke(self, st, ss, friend, account_id=None, headless=False, stop_event=None, log_cb=None, progress_cb=None):
        smoke_calls.append({"friend": friend, "account_id": account_id, "headless": headless})
        if log_cb:
            log_cb(f"Using sessionid={secret_sid} raw={secret_sid} tok={secret_gh}")
        if progress_cb:
            progress_cb({"message": f"Messaged @{friend}"})
        return {"ok": True, "elapsed": 2, "results": [{"name": "Main", "sent": 1}]}

    monkeypatch.setattr("gui.pages.run_page.LocalRunner.run_smoke_test", fake_smoke)
    rp.quickTestBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("local_run"), timeout=4.0)
    qapp.processEvents()

    assert smoke_calls == [{"friend": "alice", "account_id": "a1", "headless": False}]
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
    assert rp.runsTable.item(0, 1).text() == "failure"

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

    # Reset all settings & secrets (mock MessageBox.exec -> 1)
    monkeypatch.setattr("gui.pages.settings_page.MessageBox.exec", lambda self: 1)
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

    # 4) RunPage showBrowserSwitch persists to state.headless_local and syncs ContentPage
    win.run_interface.refresh_from_state()
    win.content_interface.refresh_from_state()
    qapp.processEvents()
    assert win.run_interface.showBrowserSwitch.isChecked() is False
    win.run_interface.showBrowserSwitch.setChecked(True)
    qapp.processEvents()
    assert win.state.headless_local is False
    assert win.content_interface.headlessSwitch.isChecked() is False
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
    assert "tugas Windows" in win.content_interface.windowsTasksBadge.text()
    set_language("en")
    qapp.processEvents()
    assert "Single-account mode" in win.accounts_interface.multiHiddenHint.text()
    assert "Windows task(s)" in win.content_interface.windowsTasksBadge.text()

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

    # 1) HomePage Step 1 badge: warning when unverified & no username; success when cookie + username
    hp = win.home_interface
    hp.refresh_state()
    qapp.processEvents()
    assert isinstance(hp.step1.badge, StatusPill)
    assert hp.step1.badge.level() == "warning"

    live_acc1 = win.state.accounts[0]
    live_acc1.username = "alice_tt"
    hp.refresh_state()
    qapp.processEvents()
    assert hp.step1.badge.level() == "success"

    # 2) ContentPage Hybrid ('both') mode: no horizontal overflow & compact SegmentedWidget
    cp = win.content_interface
    win.navigate_to("content")
    cp.modeSegment.setCurrentItem("both")
    cp._on_mode_changed("both")
    _pump_until(
        qapp,
        lambda: cp.shareTimesSpin.lineEdit().width() >= 30 and cp.dailyCapSpin.lineEdit().width() >= 30,
        timeout=3.0,
    )
    assert cp.modeSegment.height() <= 44
    assert cp.minimumSizeHint().width() <= 840
    assert cp.view.minimumSizeHint().width() <= 840
    assert cp.shareTimesSpin.lineEdit().width() >= 30
    assert cp.dailyCapSpin.lineEdit().width() >= 30

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
    secrets.set_account_cookie("a1", "sessionid=sid_repo")
    secrets.set_github_token("ghp_repo_picker_token_12345")

    cp = win.content_interface
    rp = win.run_interface
    cp.refresh_from_state()
    rp.refresh_from_state()
    qapp.processEvents()

    # 1) Fetch repos populates existingRepoCombo and selecting owner/repo updates state + badge + private switch
    monkeypatch.setattr(
        "gui.pages.content_page.GitHub.list_user_repos",
        lambda self, limit=100: [
            {
                "full_name": "myorg/streak-bot",
                "name": "streak-bot",
                "owner": "myorg",
                "private": True,
                "default_branch": "main",
                "html_url": "https://github.com/myorg/streak-bot",
            },
            {
                "full_name": "octocat/public-streak",
                "name": "public-streak",
                "owner": "octocat",
                "private": False,
                "default_branch": "main",
                "html_url": "https://github.com/octocat/public-streak",
            },
        ],
    )
    cp.fetchReposBtn.click()
    _pump_until(qapp, lambda: not win.workers.is_running("fetch_user_repos"), timeout=4.0)
    qapp.processEvents()
    assert cp.existingRepoCombo.count() == 3
    assert "myorg/streak-bot" in cp.existingRepoCombo.itemText(1)

    cp.existingRepoCombo.setCurrentIndex(1)
    qapp.processEvents()
    assert cp.repoNameEdit.text() == "myorg/streak-bot"
    assert cp.privateRepoSwitch.isChecked() is True
    assert win.state.github_repo_name == "myorg/streak-bot"
    assert isinstance(cp.repoValidBadge, StatusPill)
    assert cp.repoValidBadge.level() == "success"
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

    monkeypatch.setattr("gui.pages.content_page.GitHub.whoami", fake_whoami)
    monkeypatch.setattr("gui.pages.content_page.GitHub.create_private_repo_with_bot", fake_create_private)
    monkeypatch.setattr("gui.pages.content_page.GitHub.sync_secrets", fake_sync_secrets)

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

    # HomePage: "Setup Checklist" heading, StatusPill badges, and single primary CTA
    hp = win.home_interface
    assert hp.checklistHeading.text() == "Setup Checklist"
    for badge in (hp.overallBadge, hp.step1.badge, hp.step2.badge, hp.step3.badge, hp.step4.badge):
        assert isinstance(badge, StatusPill)

    def _primary_steps() -> list[int]:
        return [
            idx
            for idx, row in enumerate((hp.step1, hp.step2, hp.step3, hp.step4), start=1)
            if isinstance(row.goBtn, PrimaryPushButton) and not row.goBtn.isHidden()
        ]

    # Step 1 incomplete -> only step 1 is primary
    hp.refresh_state()
    qapp.processEvents()
    assert _primary_steps() == [1]
    assert not isinstance(hp.quickTestBtn, PrimaryPushButton)

    # Complete Step 1 -> only step 2 is primary
    secrets.set_account_cookie("a1", "sessionid=sid_step1")
    hp.refresh_state()
    qapp.processEvents()
    assert _primary_steps() == [2]

    # Complete Step 2 -> only step 3 is primary
    win.state.accounts[0].friends = ["alice"]
    hp.refresh_state()
    qapp.processEvents()
    assert _primary_steps() == [3]

    # Complete Step 3 -> only step 4 is primary
    secrets.set_github_token("ghp_step3_token_12345")
    hp.refresh_state()
    qapp.processEvents()
    assert _primary_steps() == [4]

    # Complete Step 4 -> no step row primary; quickTestBtn becomes primary
    win.state.last_test_ok = True
    hp.refresh_state()
    qapp.processEvents()
    assert _primary_steps() == []
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

    # AccountsPage & AccountCard
    ap = win.accounts_interface
    ap.refresh_from_state()
    qapp.processEvents()
    assert isinstance(ap.browserBadge, StatusPill)
    assert ap.browserBadge.level() == "neutral"
    assert ap.browserBadge.text() == tr("accounts.scan_not_run")
    assert ap.addAccountBtn.isHidden() is False
    assert ap.addAccountBtn.width() < ap.view.width() // 2

    card = ap._cards[0]
    assert card.nameEdit.minimumWidth() >= 200
    for badge in (card.usernameBadge, card.inboxBadge, card.friendsCountBadge):
        assert isinstance(badge, StatusPill)

    # ContentPage
    cp = win.content_interface
    cp.refresh_from_state()
    qapp.processEvents()
    assert cp.deployBtn.text() == "Save && Deploy to GitHub"
    assert cp.syncOnlyCheck.text() == "Sync secrets && schedule only"
    assert cp.defaultPhotoBtn.text() == "Use Default Photo"
    assert isinstance(cp.timesList, ListWidget)
    assert cp.timesList.count() == 1
    assert cp.patContainer.isHidden() is True

    # Single cloud primary CTA in "both" / "cloud" mode (Windows sync stays secondary)
    assert isinstance(cp.deployBtn, PrimaryPushButton)
    assert cp._applyWinPrimaryBtn.isHidden() is True
    assert cp._applyWinSecondaryBtn.isHidden() is False
    assert cp.applyWinScheduleBtn is cp._applyWinSecondaryBtn

    # repoValidBadge hidden when valid, shown when invalid
    assert cp.repoValidBadge.isHidden() is True
    assert cp.repoValidCaption.isHidden() is False
    cp.repoNameEdit.setText("bad repo name!")
    qapp.processEvents()
    assert cp.repoValidBadge.isHidden() is False
    assert cp.repoValidCaption.isHidden() is True
    cp.repoNameEdit.setText("tiktok-streak")
    qapp.processEvents()
    assert cp.repoValidBadge.isHidden() is True

    # RunPage & LogViewer
    rp = win.run_interface
    rp.refresh_from_state()
    qapp.processEvents()
    assert rp.runsTable.rowCount() == 0
    assert rp.runEmptyLabel.isHidden() is False
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
    assert rp.runEmptyLabel.isHidden() is True
    assert rp.runsTable.isHidden() is False

    created_font = rp.runsTable.item(0, 3).font()
    assert created_font.fixedPitch() is True
    assert created_font.families()[0] == "Cascadia Mono"

    editor_font = rp.logViewer.editor.font()
    assert editor_font.fixedPitch() is True
    assert editor_font.families()[0] == "Cascadia Mono"



