"""Comprehensive unit test suite for core engine (src/*, main.py) and gui/services/*."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import types
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    def __init__(self, payload: bytes | str | dict, status: int = 200, url: str = "", headers: dict | None = None):
        if isinstance(payload, dict):
            self._data = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            self._data = payload.encode("utf-8")
        else:
            self._data = payload
        self.status = status
        self._url = url
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._data if n < 0 else self._data[:n]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_http_error(url: str, code: int, body: str | dict = "") -> urllib.error.HTTPError:
    raw = json.dumps(body).encode("utf-8") if isinstance(body, dict) else str(body).encode("utf-8")
    return urllib.error.HTTPError(url, code, f"HTTP {code}", hdrs={}, fp=io.BytesIO(raw))


# ===========================================================================
# 0. Cold import safety (no Qt / Pillow / PyNaCl / Selenium / win32crypt)
# ===========================================================================

def test_gui_services_import_without_optional_gui_deps():
    repo_root = str(Path(__file__).resolve().parents[1])
    code = (
        "import sys\n"
        "blocked = {'PySide6', 'qfluentwidgets', 'PIL', 'nacl', 'Crypto', 'win32crypt', 'keyring', 'selenium'}\n"
        "class Blocker:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname.split('.')[0] in blocked:\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "import gui.services\n"
        "assert gui.services.SECRET_NAMES\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"


# ===========================================================================
# 1. src/config.py
# ===========================================================================

class TestSrcConfig:
    def test_multi_account_share_times_zero_and_explicit_disable(self, tmp_path, monkeypatch):
        from src.config import DEFAULT_PHOTO_PATH, DEFAULT_VIDEO_URL, load_accounts

        cfg_file = tmp_path / "accounts.json"
        payload = [
            {
                "name": "DisabledAll",
                "session_id": "sid_1",
                "friends": ["@Alice", "alice", "Bob", "", None],
                "share_times": 0,
                "video_url": "",
                "photo_path": "none",
                "message": "Custom msg",
            },
            {
                "name": "Sentinels",
                "session_id": "sid_2",
                "friends": "@carol, dave, @Carol",
                "share_times": "none",
                "video_url": "OFF",
                "photo_path": "",
            },
            {
                "name": "OmittedFallsBack",
                "session_id": "sid_3",
                "friends": ["eve"],
            },
        ]
        cfg_file.write_text(json.dumps(payload), encoding="utf-8")

        monkeypatch.setenv("STREAK_VIDEO_URL", "https://www.tiktok.com/@env/video/9999999999999999999")
        monkeypatch.setenv("PHOTO_PATH", "assets/env_photo.png")
        monkeypatch.setenv("SHARE_TIMES", "4")
        monkeypatch.setenv("MESSAGE", "Env msg")
        monkeypatch.delenv("DISABLE_VIDEO", raising=False)
        monkeypatch.delenv("DISABLE_PHOTO", raising=False)

        accounts = load_accounts(config_file=str(cfg_file))
        assert len(accounts) == 3

        # 1) share_times=0 stays 0, explicit ""/"none" disables without falling back
        assert accounts[0].name == "DisabledAll"
        assert accounts[0].friends == ["Alice", "Bob"]
        assert accounts[0].share_times == 0
        assert accounts[0].video_url == ""
        assert accounts[0].photo_path == ""
        assert accounts[0].message == "Custom msg"

        # 2) Disable sentinels ("none", "OFF", "") also disable
        assert accounts[1].friends == ["carol", "dave"]
        assert accounts[1].share_times == 0
        assert accounts[1].video_url == ""
        assert accounts[1].photo_path == ""
        assert accounts[1].message == "Env msg"

        # 3) Omitted keys fall back to env
        assert accounts[2].share_times == 4
        assert accounts[2].video_url.endswith("9999999999999999999")
        assert accounts[2].photo_path == "assets/env_photo.png"

        # 4) Omitted keys fall back to defaults when env unset
        monkeypatch.delenv("STREAK_VIDEO_URL")
        monkeypatch.delenv("PHOTO_PATH")
        monkeypatch.delenv("SHARE_TIMES")
        monkeypatch.delenv("MESSAGE")
        accounts_def = load_accounts(config_file=str(cfg_file))
        assert accounts_def[2].share_times == 2
        assert accounts_def[2].video_url == DEFAULT_VIDEO_URL
        assert accounts_def[2].photo_path == DEFAULT_PHOTO_PATH
        assert accounts_def[2].message == "🔥 Daily Streak"

    def test_disable_env_vars_and_single_account_sentinels(self, tmp_path, monkeypatch):
        from src.config import load_accounts, load_friends_csv

        monkeypatch.chdir(tmp_path)
        for k in list(os.environ):
            if k in {
                "TIKTOK_ACCOUNTS_JSON", "TIKTOK_SESSION_ID", "FRIENDS_LIST", "MESSAGE",
                "STREAK_VIDEO_URL", "SHARE_TIMES", "PHOTO_PATH", "DISABLE_VIDEO", "DISABLE_PHOTO",
            }:
                monkeypatch.delenv(k, raising=False)

        monkeypatch.setenv("TIKTOK_SESSION_ID", "main_sid")
        monkeypatch.setenv("FRIENDS_LIST", "@one, two, @ONE, three")
        monkeypatch.setenv("DISABLE_VIDEO", "true")
        monkeypatch.setenv("DISABLE_PHOTO", "YES")
        monkeypatch.setenv("SHARE_TIMES", "0")

        accs = load_accounts(config_file=str(tmp_path / "missing.json"))
        assert len(accs) == 1
        assert accs[0].name == "Main"
        assert accs[0].friends == ["one", "two", "three"]
        assert accs[0].video_url == ""
        assert accs[0].photo_path == ""
        assert accs[0].share_times == 0

        # Sentinels in env vars directly
        monkeypatch.setenv("DISABLE_VIDEO", "false")
        monkeypatch.setenv("DISABLE_PHOTO", "false")
        monkeypatch.setenv("STREAK_VIDEO_URL", "disabled")
        monkeypatch.setenv("PHOTO_PATH", "off")
        accs2 = load_accounts(config_file=str(tmp_path / "missing.json"))
        assert accs2[0].video_url == ""
        assert accs2[0].photo_path == ""

        # friends.csv fallback with BOM & dedup
        csv_path = tmp_path / "friends.csv"
        csv_path.write_text("\ufeffUsername\n@Alpha\nbeta\n@alpha\n\n", encoding="utf-8")
        assert load_friends_csv(str(csv_path)) == ["Alpha", "beta"]

        monkeypatch.delenv("FRIENDS_LIST")
        accs3 = load_accounts(config_file=str(tmp_path / "missing.json"))
        assert accs3[0].friends == ["Alpha", "beta"]

    def test_invalid_accounts_json_falls_back_to_env(self, tmp_path, monkeypatch):
        from src.config import load_accounts

        bad_file = tmp_path / "accounts.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setenv("TIKTOK_ACCOUNTS_JSON", json.dumps([{"name": "EnvAcc", "session_id": "s1", "friends": ["f1"]}]))
        accs = load_accounts(config_file=str(bad_file))
        assert len(accs) == 1
        assert accs[0].name == "EnvAcc"


# ===========================================================================
# 2. src/photo_prep.py
# ===========================================================================

class TestSrcPhotoPrep:
    def test_resolve_disable_flags_and_sentinels(self, tmp_path, monkeypatch):
        from src import photo_prep

        monkeypatch.chdir(tmp_path)
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nDATA").decode()
        data_url = f"data:image/png;base64,{png}"

        assert photo_prep.resolve(data_url, "assets/mine.png", disable_flag="true") == ""
        assert photo_prep.resolve(data_url, "", disable_flag="1") == ""
        assert photo_prep.resolve(data_url, "", disable_flag="Yes") == ""
        assert photo_prep.resolve("none") == ""
        assert photo_prep.resolve("OFF") == ""
        assert photo_prep.resolve("", "disabled") == ""

    def test_resolve_fallbacks_and_invalid_base64(self, tmp_path, monkeypatch):
        from src import photo_prep

        monkeypatch.chdir(tmp_path)
        assert photo_prep.resolve("") == photo_prep.DEFAULT
        assert photo_prep.resolve("", "assets/custom.png") == "assets/custom.png"

        with pytest.raises(ValueError, match="Invalid PHOTO_DATA_URL"):
            photo_prep.resolve("not-a-data-url")

        with pytest.raises(ValueError, match="expected a base64 image data URL"):
            photo_prep.resolve("data:image/png;base64,   ")

        with pytest.raises(ValueError, match="Invalid base64"):
            photo_prep.resolve("data:image/png;base64,@@@invalid@@@")

    def test_resolve_writes_file_and_main_exports_github_env(self, tmp_path, monkeypatch):
        from src import photo_prep

        monkeypatch.chdir(tmp_path)
        raw_bytes = b"WEBP_PAYLOAD"
        b64 = base64.b64encode(raw_bytes).decode()
        spaced_b64 = f"{b64[:4]}\n {b64[4:]}"
        genv = tmp_path / "github_env.txt"

        monkeypatch.setenv("PHOTO_DATA_URL", f"data:image/webp;base64,{spaced_b64}")
        monkeypatch.setenv("PHOTO_PATH", "")
        monkeypatch.setenv("DISABLE_PHOTO", "false")
        monkeypatch.setenv("GITHUB_ENV", str(genv))

        photo_prep.main()
        out_file = tmp_path / "assets" / "custom_photo.webp"
        assert out_file.read_bytes() == raw_bytes
        assert "PHOTO_PATH=assets/custom_photo.webp\n" in genv.read_text(encoding="utf-8")


# ===========================================================================
# 3. src/browser.py
# ===========================================================================

class TestSrcBrowser:
    def test_parse_cookie_payload_strips_crlf_across_formats(self):
        from src.browser import parse_cookie_payload

        assert parse_cookie_payload('{"sessionid": "abc\r\n", "sid_tt\n": "xyz\r"}') == {
            "sessionid": "abc",
            "sid_tt": "xyz",
        }
        assert parse_cookie_payload('[{"name": "sessionid\r", "value": "val\n"}]') == {
            "sessionid": "val",
        }
        header = "Cookie: sessionid=tok1\r\n; sid_tt=\"tok2\n\"\r\nttwid=tok3"
        assert parse_cookie_payload(header) == {
            "sessionid": "tok1",
            "sid_tt": "tok2",
            "ttwid": "tok3",
        }
        raw_tok = "abc123\r\ndef456\n"
        assert parse_cookie_payload(raw_tok) == {
            "sessionid": "abc123def456",
            "sessionid_ss": "abc123def456",
            "sid_tt": "abc123def456",
        }
        assert parse_cookie_payload("   \r\n  ") == {}

    def test_authenticate_session_clears_stashed_templates_and_handles_redirects(self, monkeypatch):
        from src.browser import authenticate_session
        from src.exceptions import InboxUnavailableError, SessionExpiredError

        mock_browser = MagicMock()
        mock_browser.current_url = "https://www.tiktok.com/messages?lang=en"
        injected = {}
        mock_browser.add_cookie.side_effect = lambda c: injected.__setitem__(c["name"], c["value"])

        with patch("time.sleep", return_value=None):
            monkeypatch.setenv("TIKTOK_COOKIES", "sessionid=GLOBAL; ttwid=global_ttwid")
            authenticate_session(mock_browser, "ACC1_SID", "Acc1")
            assert injected["sessionid"] == "ACC1_SID"
            assert injected["ttwid"] == "global_ttwid"

            injected.clear()
            authenticate_session(mock_browser, "ACC2_SID", "Acc2")
            assert injected["sessionid"] == "ACC2_SID"

        assert mock_browser.delete_all_cookies.call_count == 2
        scripts = [call.args[0] for call in mock_browser.execute_script.call_args_list]
        assert len(scripts) == 2
        for script in scripts:
            assert "sessionStorage.removeItem('__tmplB64')" in script
            assert "sessionStorage.removeItem('__httpSendB64')" in script
            assert "sessionStorage.removeItem('__httpSendUrl')" in script
            assert "window.__sent = []" in script
            assert "window.__httpSend = null" in script
            assert "window.__mediaSend = null" in script

        # Empty session raises SessionExpiredError
        with pytest.raises(SessionExpiredError):
            authenticate_session(mock_browser, "", "Empty")

        # Login redirect raises SessionExpiredError
        mock_browser.current_url = "https://www.tiktok.com/login"
        with patch("time.sleep", return_value=None), patch("src.browser.dump_debug_diagnostics") as mock_dump:
            with pytest.raises(SessionExpiredError):
                authenticate_session(mock_browser, "sid", "ExpiredAcc")
            mock_dump.assert_called_once()

        # Business Suite redirect raises InboxUnavailableError
        mock_browser.current_url = "https://www.tiktok.com/business-suite/messages"
        with patch("time.sleep", return_value=None), patch("src.browser.dump_debug_diagnostics") as mock_dump:
            with pytest.raises(InboxUnavailableError):
                authenticate_session(mock_browser, "sid", "BizAcc")
            mock_dump.assert_called_once()

    def test_authenticate_session_cdp_cleanup_selective_httponly_host_prefix_and_hydration(self, monkeypatch):
        from src.browser import authenticate_session
        from src.exceptions import SessionExpiredError

        monkeypatch.delenv("TIKTOK_COOKIES", raising=False)
        mock_browser = MagicMock()
        mock_browser.current_url = "https://www.tiktok.com/messages?lang=en"
        added_cookies: list[dict] = []
        mock_browser.add_cookie.side_effect = lambda c: added_cookies.append(dict(c))

        payload = (
            "sessionid=sid_val; msToken=ms_val; store-idc=alisg; "
            "__Host-csrf=host_tok; sid_guard=guard_val"
        )
        with patch("time.sleep", return_value=None):
            authenticate_session(mock_browser, payload, "HydratedAcc")

        # CDP cookie + storage cleanup and localStorage/sessionStorage clear
        mock_browser.execute_cdp_cmd.assert_any_call("Network.clearBrowserCookies", {})
        mock_browser.execute_cdp_cmd.assert_any_call(
            "Storage.clearDataForOrigin",
            {"origin": "https://www.tiktok.com", "storageTypes": "all"},
        )
        script = mock_browser.execute_script.call_args.args[0]
        assert "localStorage.clear();" in script
        assert "sessionStorage.clear();" in script

        by_name = {c["name"]: c for c in added_cookies}
        # Selective httpOnly: auth cookies True, JS-readable regional/token cookies False
        assert by_name["sessionid"]["httpOnly"] is True
        assert by_name["sid_guard"]["httpOnly"] is True
        assert by_name["msToken"]["httpOnly"] is False
        assert by_name["store-idc"]["httpOnly"] is False
        # __Host- cookies omit domain attribute; standard cookies include .tiktok.com
        assert "domain" not in by_name["__Host-csrf"]
        assert by_name["sessionid"]["domain"] == ".tiktok.com"

        # Best-effort runtime hydration for bare sessionid without regional cookies
        added_cookies.clear()
        with patch("time.sleep", return_value=None), \
             patch(
                 "gui.services.tiktok_auth.check_session_api",
                 return_value={
                     "ok": True,
                     "cookie_text": "sessionid=hydrated_should_not_override; tt-target-idc=useast2a; store-idc=useast2a",
                 },
             ) as mock_api:
            authenticate_session(mock_browser, "sessionid=bare_sid; ttwid=tw_orig", "BareAcc")
        mock_api.assert_called_once()
        hydrated_by_name = {c["name"]: c for c in added_cookies}
        assert hydrated_by_name["sessionid"]["value"] == "bare_sid"
        assert hydrated_by_name["tt-target-idc"]["value"] == "useast2a"
        assert hydrated_by_name["store-idc"]["value"] == "useast2a"

        # Hydration failure is swallowed gracefully and /login without regional cookies gives actionable error
        mock_browser.current_url = "https://www.tiktok.com/login"
        with patch("time.sleep", return_value=None), \
             patch("src.browser.dump_debug_diagnostics"), \
             patch("gui.services.tiktok_auth.check_session_api", side_effect=RuntimeError("network down")):
            with pytest.raises(SessionExpiredError, match="tt-target-idc / store-idc"):
                authenticate_session(mock_browser, "sessionid=bare_sid", "BareExpired")

        # /login WITH regional cookies raises standard SessionExpiredError without missing-regional tip
        with patch("time.sleep", return_value=None), \
             patch("src.browser.dump_debug_diagnostics"):
            with pytest.raises(SessionExpiredError) as exc_info:
                authenticate_session(mock_browser, "sessionid=sid_ok; tt-target-idc=useast2a", "RegionalExpired")
            assert "tt-target-idc / store-idc" not in str(exc_info.value)


# ===========================================================================
# 4. src/actions.py & main.py
# ===========================================================================

class TestActionsAndMain:
    def test_rate_limit_propagates_from_send_streak_message(self):
        from src.actions import send_streak_message
        from src.exceptions import RateLimitError

        mock_browser = MagicMock()
        toast = MagicMock()
        toast.is_displayed.return_value = True
        toast.text = "You are sending messages too fast"
        mock_browser.find_elements.return_value = [toast]

        with patch("src.actions.open_inbox_chat"), \
             patch("src.share.get_active_chat_handle", return_value="friend1"), \
             patch("src.actions.find_element_by_candidates", return_value=MagicMock()), \
             patch("src.actions.dump_debug_diagnostics") as mock_dump, \
             patch("time.sleep", return_value=None):
            with pytest.raises(RateLimitError):
                send_streak_message(mock_browser, "friend1", "hello")
            mock_dump.assert_called_once()

    def test_send_streak_message_respects_stop_event(self):
        from src.actions import send_streak_message

        stop = threading.Event()
        stop.set()
        res = send_streak_message(MagicMock(), "friend1", "hello", stop_event=stop)
        assert res == {"friend": "friend1", "sent": False, "error": "Cancelled"}

    def test_main_run_halts_on_rate_limit_without_sys_exit(self):
        import main
        from src.config import AccountConfig
        from src.exceptions import RateLimitError

        acc1 = AccountConfig(name="A1", session_id="s1", friends=["f1", "f2"], video_url="", share_times=0, photo_path="")
        acc2 = AccountConfig(name="A2", session_id="s2", friends=["f3"], video_url="", share_times=0, photo_path="")
        mock_browser = MagicMock()
        events = []

        with patch("main.init_browser", return_value=(mock_browser, MagicMock())), \
             patch("main.install_ws_hook"), \
             patch("main.install_media_guard"), \
             patch("main.authenticate_session") as mock_auth, \
             patch("main.send_streak_message", side_effect=RateLimitError("Too fast")) as mock_send, \
             patch("main.notify_telegram") as mock_tg, \
             patch("main.notify_streak_summary") as mock_sum, \
             patch("time.sleep", return_value=None):
            result = main.run(accounts_override=[acc1, acc2], progress_cb=events.append)

        assert result["ok"] is False
        assert result["critical_failure"] is True
        assert len(result["results"]) == 1  # A2 was never started after rate limit
        assert mock_auth.call_count == 1
        assert mock_send.call_count == 1
        assert any("Rate limit" in f for f in result["results"][0]["failures"])
        assert any(e.get("stage") == "rate_limit" for e in events)
        mock_tg.assert_called_once()
        mock_sum.assert_called_once()
        mock_browser.quit.assert_called_once()

    def test_main_run_filters_stop_event_and_cli_exit(self):
        import main
        from src.config import AccountConfig

        acc1 = AccountConfig(name="Primary", session_id="s1", friends=["alice", "bob"], video_url="", share_times=0, photo_path="")
        acc2 = AccountConfig(name="Backup", session_id="s2", friends=["carol"], video_url="", share_times=0, photo_path="")

        # Pre-set stop_event aborts before launching browser
        stop = threading.Event()
        stop.set()
        with patch("main.init_browser") as mock_init:
            res_stopped = main.run(accounts_override=[acc1], stop_event=stop)
        mock_init.assert_not_called()
        assert res_stopped["ok"] is False
        assert res_stopped["critical_failure"] is False

        # Filter mismatch returns critical_failure without sys.exit
        with patch("main.init_browser") as mock_init:
            res_missing = main.run(accounts_override=[acc1], only_account="Nope")
        mock_init.assert_not_called()
        assert res_missing["ok"] is False
        assert res_missing["critical_failure"] is True

        # only_account + only_friend filter accurately and return ok=True
        mock_browser = MagicMock()
        sent_calls = []

        def fake_send(**kwargs):
            sent_calls.append((kwargs["friend"], kwargs["message_text"]))
            return {"friend": kwargs["friend"], "sent": True, "error": None}

        with patch("main.init_browser", return_value=(mock_browser, MagicMock())), \
             patch("main.install_ws_hook"), \
             patch("main.install_media_guard"), \
             patch("main.authenticate_session") as mock_auth, \
             patch("main.send_streak_message", side_effect=fake_send), \
             patch("main.notify_streak_summary"), \
             patch("time.sleep", return_value=None):
            res_ok = main.run(
                accounts_override=[acc1, acc2],
                headless=False,
                only_account="primary",
                only_friend="@BOB",
            )

        assert res_ok["ok"] is True
        assert res_ok["critical_failure"] is False
        assert mock_auth.call_count == 1
        assert mock_auth.call_args.args[2] == "Primary"
        assert sent_calls == [("bob", "🔥 Daily Streak")]
        mock_browser.quit.assert_called_once()

        # Empty accounts does not sys.exit in run(), but cli_main exits 1
        with patch("main.load_accounts", return_value=[]), patch("main.notify_telegram"):
            res_empty = main.run()
            assert res_empty["ok"] is False and res_empty["critical_failure"] is True

        with patch("main.run", return_value={"ok": False}) as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                main.cli_main(["--headful", "--only-account", "Primary", "--only-friend", "bob"])
            assert exc_info.value.code == 1
            mock_run.assert_called_once_with(headless=False, only_account="Primary", only_friend="bob")

    def test_main_run_multi_account_browser_isolation_and_rate_limit(self):
        import main
        from src.config import AccountConfig
        from src.exceptions import RateLimitError, SessionExpiredError

        acc1 = AccountConfig(name="A1", session_id="s1", friends=["f1"], video_url="", share_times=0, photo_path="")
        acc2 = AccountConfig(name="A2", session_id="s2", friends=["f2"], video_url="", share_times=0, photo_path="")

        # 1) Normal multi-account run creates and quits a fresh browser per account
        b1, b2 = MagicMock(), MagicMock()
        browsers = iter([b1, b2])
        call_log: list[str] = []

        def fake_init(headless=None):
            b = next(browsers)
            call_log.append(f"init:{'b1' if b is b1 else 'b2'}")
            return b, MagicMock()

        b1.quit.side_effect = lambda: call_log.append("quit:b1")
        b2.quit.side_effect = lambda: call_log.append("quit:b2")

        with patch("main.init_browser", side_effect=fake_init), \
             patch("main.install_ws_hook") as mock_ws, \
             patch("main.install_media_guard") as mock_mg, \
             patch("main.authenticate_session") as mock_auth, \
             patch("main.send_streak_message", return_value={"friend": "f1", "sent": True, "error": None}), \
             patch("main.notify_streak_summary"), \
             patch("time.sleep", return_value=None):
            res = main.run(accounts_override=[acc1, acc2], headless=True)

        assert res["ok"] is True
        assert call_log == ["init:b1", "quit:b1", "init:b2", "quit:b2"]
        assert mock_ws.call_count == 2
        assert mock_mg.call_count == 2
        assert mock_auth.call_args_list[0].args[0] is b1
        assert mock_auth.call_args_list[1].args[0] is b2
        b1.quit.assert_called_once()
        b2.quit.assert_called_once()

        # 2) Auth failure on A1 still quits b1 cleanly before starting b2 for A2
        b1_err, b2_ok = MagicMock(), MagicMock()
        with patch("main.init_browser", side_effect=[(b1_err, MagicMock()), (b2_ok, MagicMock())]), \
             patch("main.install_ws_hook"), \
             patch("main.install_media_guard"), \
             patch("main.authenticate_session", side_effect=[SessionExpiredError("expired"), None]), \
             patch("main.send_streak_message", return_value={"friend": "f2", "sent": True, "error": None}), \
             patch("main.notify_telegram"), \
             patch("main.notify_streak_summary"), \
             patch("time.sleep", return_value=None):
            res_auth_err = main.run(accounts_override=[acc1, acc2], headless=True)

        assert res_auth_err["ok"] is False
        assert res_auth_err["critical_failure"] is True
        assert len(res_auth_err["results"]) == 2
        b1_err.quit.assert_called_once()
        b2_ok.quit.assert_called_once()

        # 3) RateLimitError on A1 quits b1 exactly once and halts before A2
        b1_rl = MagicMock()
        with patch("main.init_browser", return_value=(b1_rl, MagicMock())) as mock_init_rl, \
             patch("main.install_ws_hook"), \
             patch("main.install_media_guard"), \
             patch("main.authenticate_session"), \
             patch("main.send_streak_message", side_effect=RateLimitError("Too fast")), \
             patch("main.notify_telegram"), \
             patch("main.notify_streak_summary"), \
             patch("time.sleep", return_value=None):
            res_rl = main.run(accounts_override=[acc1, acc2], headless=True)

        assert res_rl["ok"] is False
        assert res_rl["critical_failure"] is True
        assert len(res_rl["results"]) == 1
        assert mock_init_rl.call_count == 1
        b1_rl.quit.assert_called_once()


# ===========================================================================
# 5. gui/services/i18n.py
# ===========================================================================

class TestI18nService:
    def test_en_id_lookup_subscribe_and_set_language(self):
        from gui.services.i18n import I18nManager

        mgr = I18nManager(default_lang="en")
        seen = []

        def on_lang(lang: str) -> None:
            seen.append(lang)

        mgr.subscribe(on_lang)
        assert mgr.get_language() == "en"
        assert mgr.tr("nav.accounts") == "Accounts"

        # Same language does not re-fire subscribers
        mgr.set_language("en-US")
        assert seen == []

        # Switch to Indonesian
        assert mgr.set_language("id-ID") == "id"
        assert seen == ["id"]
        assert mgr.tr("nav.accounts") == "Akun"
        assert mgr.tr("dashboard.accounts_count", count=3) == "3 akun terkonfigurasi"
        assert mgr.tr("nonexistent.key") == "nonexistent.key"

        # Unsubscribe stops notifications
        mgr.unsubscribe(on_lang)
        mgr.set_language("en")
        assert seen == ["id"]

    def test_friendly_error_mappings_and_secret_redaction(self):
        from gui.services.browsers import BrowserLocked, V20EncryptedError
        from gui.services.github_api import GitHubError
        from gui.services.i18n import I18nManager
        from src.exceptions import InboxUnavailableError, RateLimitError, SessionExpiredError

        mgr = I18nManager("en")

        assert "App-Bound Encryption" in mgr.friendly_error(V20EncryptedError("v20"))
        assert "v20" in mgr.friendly_error("v20 encrypted", lang="id").lower()

        assert "currently open" in mgr.friendly_error(BrowserLocked("locked")).lower()
        assert "mengunci" in mgr.friendly_error("WinError 32 database is locked", lang="id").lower()

        assert "Business Suite" in mgr.friendly_error(InboxUnavailableError("biz"))
        assert "Business Suite" in mgr.friendly_error("redirected to business-suite", lang="id")

        assert "session cookie" in mgr.friendly_error(SessionExpiredError("expired")).lower()
        assert "24 hours" in mgr.friendly_error(RateLimitError("too fast")).lower()

        assert "webhook" in mgr.friendly_error("Telegram 409 conflict (active webhook)").lower()
        assert "webhook" in mgr.friendly_error("Telegram 409 webhook", lang="id").lower()

        assert "422" in mgr.friendly_error(GitHubError("unprocessable", status_code=422))
        assert "409" in mgr.friendly_error(GitHubError("conflict", status_code=409))
        assert "409" in mgr.friendly_error("PATCH /ref -> 409", lang="id")

        # Secret redaction in fallback error text
        raw = (
            "Error with ghp_secret1234567890 and 123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 "
            "Authorization: Bearer topsecret Cookie: sessionid=mysecretval"
        )
        cleaned = mgr.friendly_error(raw)
        assert "ghp_secret1234567890" not in cleaned
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in cleaned
        assert "topsecret" not in cleaned
        assert "mysecretval" not in cleaned


# ===========================================================================
# 6. gui/services/storage.py
# ===========================================================================

class TestStorageService:
    def test_config_store_atomic_load_save_reset(self, tmp_path):
        from gui.services.storage import AccountProfile, AppState, ConfigStore

        store = ConfigStore(tmp_path / "cfg")
        assert store.exists() is False
        default_state = store.load()
        assert default_state.language == "en"
        assert default_state.share_times == 2

        state = AppState(
            language="id",
            theme="dark",
            accent_mode="tiktok",
            mode="both",
            multi_account=True,
            accounts=[
                AccountProfile(
                    id="acc1",
                    name="Alpha",
                    friends=["alice", "bob"],
                    share_times_override=0,
                    video_url_override="",
                    username="alpha_u",
                    inbox_verified=True,
                )
            ],
            share_times=0,
            video_enabled=False,
            photo_enabled=False,
            daily_cap=5,
            local_times=["08:00", "20:00"],
        )
        saved_path = store.save(state)
        assert saved_path.is_file()
        assert store.exists() is True
        assert not list((tmp_path / "cfg").glob("*.tmp"))

        reloaded = store.load()
        assert reloaded.language == "id"
        assert reloaded.accent_mode == "tiktok"
        assert reloaded.share_times == 0
        assert reloaded.video_enabled is False
        assert reloaded.accounts[0].share_times_override == 0
        assert reloaded.accounts[0].video_url_override == ""
        assert reloaded.accounts[0].inbox_verified is True

        # Corrupt JSON returns defaults safely
        saved_path.write_text("{corrupt", encoding="utf-8")
        assert store.load().language == "en"

        store.reset()
        assert store.exists() is False

    def test_appstate_timezone_and_cloud_times_migration(self, tmp_path):
        from gui.services.storage import AppState, ConfigStore

        # Legacy payload without timezone/cloud_times migrates from cloud_cron
        legacy = {
            "cloud_cron": "15 1,13 * * *",
            "local_times": ["08:15", "20:15"],
        }
        migrated = AppState.from_dict(legacy)
        assert migrated.cloud_cron == "15 1,13 * * *"
        # Unknown/invalid timezone falls back to Asia/Jakarta
        migrated_explicit_tz = AppState.from_dict({
            "timezone": "Asia/Jakarta",
            "cloud_cron": "15 1,13 * * *",
        })
        assert migrated_explicit_tz.timezone == "Asia/Jakarta"
        assert migrated_explicit_tz.cloud_times == ["08:15", "20:15"]

        # Non-WIB timezone migration (America/New_York UTC-5: 01:15 UTC -> 20:15, 13:15 UTC -> 08:15)
        migrated_ny = AppState.from_dict({
            "timezone": "America/New_York",
            "cloud_cron": "15 1,13 * * *",
        })
        assert migrated_ny.timezone == "America/New_York"
        assert migrated_ny.cloud_times == ["08:15", "20:15"]

        # Explicit cloud_times deduplicated & validated, invalid entries dropped
        explicit = AppState.from_dict({
            "timezone": "Asia/Tokyo",
            "cloud_times": ["8:5", "08:05", "bad", "21:30"],
            "cloud_cron": "5 23 * * *\n30 12 * * *",
        })
        assert explicit.timezone == "Asia/Tokyo"
        assert explicit.cloud_times == ["08:05", "21:30"]

        # Roundtrip via ConfigStore
        store = ConfigStore(tmp_path / "tz_cfg")
        store.save(explicit)
        loaded = store.load()
        assert loaded.timezone == "Asia/Tokyo"
        assert loaded.cloud_times == ["08:05", "21:30"]
        assert loaded.cloud_cron == "5 23 * * *\n30 12 * * *"

    def test_secret_store_keyring_dpapi_fallback_and_clear_all(self, tmp_path, monkeypatch):
        from gui.services.storage import SecretStore

        # 1) Keyring backend
        mem_keyring: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda svc, k: mem_keyring.get((svc, k)),
            set_password=lambda svc, k, v: mem_keyring.__setitem__((svc, k), v),
            delete_password=lambda svc, k: mem_keyring.pop((svc, k), None),
        )
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

        ss = SecretStore(tmp_path / "sec1")
        assert ss.set_github_token("ghp_1234567890") == "keyring"
        assert ss.set_telegram_token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345") == "keyring"
        assert ss.set_account_cookie("acc1", "sessionid=abc") == "keyring"
        assert ss.get_github_token() == "ghp_1234567890"
        assert ss.get_account_cookie("acc1") == "sessionid=abc"

        ss.clear_all(account_ids=["acc1"])
        assert mem_keyring == {}
        assert not ss._index_path.exists()

        # 2) Keyring fails -> DPAPI fallback (mocked win32crypt so test works on any OS)
        def fail_set(*_a):
            raise RuntimeError("keyring size limit exceeded")

        broken_keyring = types.SimpleNamespace(
            get_password=lambda *_a: None,
            set_password=fail_set,
            delete_password=lambda *_a: None,
        )
        monkeypatch.setitem(sys.modules, "keyring", broken_keyring)

        fake_win32crypt = types.SimpleNamespace(
            CryptProtectData=lambda data, *_a: b"DPAPI_HDR:" + bytes(b ^ 0x5A for b in data),
            CryptUnprotectData=lambda blob, *_a: ("desc", bytes(b ^ 0x5A for b in blob[len(b"DPAPI_HDR:"):])),
        )
        monkeypatch.setitem(sys.modules, "win32crypt", fake_win32crypt)

        ss_dpapi = SecretStore(tmp_path / "sec2")
        assert ss_dpapi.set_account_cookie("acc2", "sessionid=supersecret_cookie") == "dpapi"
        raw_dpapi = ss_dpapi._dpapi_path.read_bytes()
        assert b"supersecret_cookie" not in raw_dpapi  # never plaintext
        assert ss_dpapi.get_account_cookie("acc2") == "sessionid=supersecret_cookie"

        ss_dpapi.clear_all()
        assert not ss_dpapi._dpapi_path.exists()

        # 3) Neither keyring nor DPAPI available -> RuntimeError (never plaintext)
        monkeypatch.setattr(SecretStore, "_import_win32crypt", staticmethod(lambda: None))
        with pytest.raises(RuntimeError, match="Secure storage unavailable"):
            ss_dpapi.set_github_token("ghp_fail")

    def test_export_import_workspace_roundtrip_and_redact(self, tmp_path, monkeypatch):
        from gui.services.storage import (
            AccountProfile,
            AppState,
            SecretStore,
            export_local_workspace,
            import_local_workspace,
            redact_secrets,
        )

        mem_keyring: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda svc, k: mem_keyring.get((svc, k)),
            set_password=lambda svc, k, v: mem_keyring.__setitem__((svc, k), v),
            delete_password=lambda svc, k: mem_keyring.pop((svc, k), None),
        )
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
        ss = SecretStore(tmp_path / "sec")
        ss.set_telegram_token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        ss.set_account_cookie("a1", "sessionid=sid_one")
        ss.set_account_cookie("a2", "sessionid=sid_two")

        # Single-account with video & photo disabled
        ws_single = tmp_path / "ws_single"
        ws_single.mkdir()
        (ws_single / "accounts.json").write_text("[]", encoding="utf-8")
        single_state = AppState(
            multi_account=False,
            accounts=[AccountProfile(id="a1", name="Main", friends=["alice", "bob"])],
            message="Hi!",
            video_enabled=False,
            photo_enabled=False,
            daily_cap=3,
            headless_local=False,
            telegram_enabled=True,
            telegram_chat_id="777",
        )
        exp_single = export_local_workspace(single_state, ss, ws_single)
        assert exp_single["mode"] == "single"
        assert not (ws_single / "accounts.json").exists()  # stale file removed

        imp_single = import_local_workspace(ws_single)
        s_back = imp_single["state"]
        assert s_back.multi_account is False
        assert s_back.video_enabled is False
        assert s_back.photo_enabled is False
        assert s_back.daily_cap == 3
        assert s_back.headless_local is False
        assert s_back.telegram_enabled is True
        assert s_back.telegram_chat_id == "777"
        assert s_back.accounts[0].friends == ["alice", "bob"]
        assert list(imp_single["secrets"]["accounts"].values()) == ["sessionid=sid_one"]

        # Multi-account round-trip
        ws_multi = tmp_path / "ws_multi"
        multi_state = AppState(
            multi_account=True,
            accounts=[
                AccountProfile(id="a1", name="Acc1", friends=["alice"], share_times_override=0, video_url_override=""),
                AccountProfile(id="a2", name="Acc2", friends=["bob", "alice"], message_override="Yo"),
            ],
            message="DefaultMsg",
            video_enabled=True,
            video_url="https://www.tiktok.com/@u/video/1234567890123456789",
            share_times=2,
            photo_enabled=True,
        )
        exp_multi = export_local_workspace(multi_state, ss, ws_multi)
        assert exp_multi["mode"] == "multi"
        assert (ws_multi / "accounts.json").is_file()

        imp_multi = import_local_workspace(ws_multi)
        m_back = imp_multi["state"]
        assert m_back.multi_account is True
        assert len(m_back.accounts) == 2
        assert m_back.accounts[0].share_times_override == 0
        assert m_back.accounts[0].video_url_override == ""
        assert m_back.accounts[1].message_override == "Yo"
        assert sorted(imp_multi["secrets"]["accounts"].values()) == ["sessionid=sid_one", "sessionid=sid_two"]

        # Secret redaction
        sample = (
            'TIKTOK_SESSION_ID=raw_cookie_123\n'
            '{"session_id": "abc_secret", "token": "xyz"}\n'
            'https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345/getUpdates\n'
            'token=github_pat_1234567890abcdef custom=my_extra_secret_999'
        )
        red = redact_secrets(sample, extra_secrets=["my_extra_secret_999"])
        for leak in ("raw_cookie_123", "abc_secret", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "github_pat_1234567890abcdef", "my_extra_secret_999"):
            assert leak not in red


# ===========================================================================
# 7. gui/services/validators.py
# ===========================================================================

class TestValidatorsService:
    def test_normalize_friends_and_csv_roundtrip(self, tmp_path):
        from gui.services.validators import export_friends_csv, import_friends_csv, normalize_friends

        raw = (
            "@Alice, bob\n"
            "https://www.tiktok.com/@Carol?lang=en; https://m.tiktok.com/@dave/\n"
            "@alice\n"
            "John Doe\n"
            "https://vt.tiktok.com/ZS12345/\n"
            "https://www.tiktok.com/@Eve/video/7684838545659317524\n"
            "bad!name\n"
            "@user_one @user_two"
        )
        handles, warnings = normalize_friends(raw)
        assert handles == ["Alice", "bob", "Carol", "dave", "Eve", "user_one", "user_two"]
        assert any("duplicate" in w.lower() for w in warnings)
        assert any("John Doe" in w for w in warnings)
        assert any("short link" in w.lower() for w in warnings)
        assert any("video/photo URL" in w for w in warnings)
        assert any("bad!name" in w for w in warnings)

        csv_file = tmp_path / "friends.csv"
        export_friends_csv(csv_file, ["@First", "second", "FIRST"])
        assert import_friends_csv(csv_file) == ["First", "second"]

    def test_validate_video_url_and_short_link_redirect(self):
        from gui.services.validators import validate_video_url

        assert validate_video_url("")["valid"] is False
        ok_full = validate_video_url("https://www.tiktok.com/@u/video/7684838545659317524")
        assert ok_full["valid"] is True and ok_full["aweme_id"] == "7684838545659317524"

        short_no_res = validate_video_url("https://vt.tiktok.com/ZSabc/", resolve_short=False)
        assert short_no_res["valid"] is False and short_no_res["is_short_link"] is True

        resp_redirect = _FakeHTTPResponse(
            "",
            url="https://www.tiktok.com/@creator/video/7123456789012345678?is_from_webapp=1",
        )
        with patch("urllib.request.urlopen", return_value=resp_redirect):
            res_short = validate_video_url("vt.tiktok.com/ZSabc/", resolve_short=True)
        assert res_short["valid"] is True
        assert res_short["aweme_id"] == "7123456789012345678"
        assert res_short["normalized_url"] == "https://www.tiktok.com/@creator/video/7123456789012345678"

    def test_legacy_default_video_migration_and_live_availability_probe(self, tmp_path):
        from src.config import DEFAULT_VIDEO_URL as CORE_DEFAULT_VIDEO
        from gui.services.storage import ConfigStore, DEFAULT_VIDEO_URL as GUI_DEFAULT_VIDEO
        from gui.services.validators import validate_video_url

        expected_default = "https://www.tiktok.com/@proofwereokay/video/7669608467589647629"
        assert CORE_DEFAULT_VIDEO == expected_default
        assert GUI_DEFAULT_VIDEO == expected_default

        # Old dead shipped default migrates cleanly; custom URLs remain untouched
        cfg_dir = tmp_path / "cfg_migrate"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"video_url": "https://www.tiktok.com/@yutta/video/7684838545659317524?is_from_webapp=1"}),
            encoding="utf-8",
        )
        assert ConfigStore(cfg_dir).load().video_url == expected_default

        custom_url = "https://www.tiktok.com/@customcreator/video/7111222333444555666"
        (cfg_dir / "config.json").write_text(json.dumps({"video_url": custom_url}), encoding="utf-8")
        assert ConfigStore(cfg_dir).load().video_url == custom_url

        def _ssr_page(video_detail: dict) -> _FakeHTTPResponse:
            payload = {"__DEFAULT_SCOPE__": {"webapp.video-detail": video_detail}}
            html_s = (
                '<!doctype html><html><body>'
                f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script>'
                '</body></html>'
            )
            return _FakeHTTPResponse(html_s)

        # 1) Available public shareable video -> canonical URL + author
        live_detail = {
            "statusCode": 0,
            "statusMsg": "",
            "itemInfo": {
                "itemStruct": {
                    "id": "7669608467589647629",
                    "author": {"uniqueId": "proofwereokay"},
                    "privateItem": False,
                    "shareEnabled": True,
                }
            },
        }
        with patch("urllib.request.urlopen", return_value=_ssr_page(live_detail)):
            ok_res = validate_video_url(
                "https://www.tiktok.com/@proofwereokay/video/7669608467589647629?is_from_webapp=1&sender_device=pc",
                verify_availability=True,
            )
        assert ok_res["valid"] is True and ok_res["verified"] is True
        assert ok_res["availability"] == "available"
        assert ok_res["author"] == "proofwereokay"
        assert ok_res["normalized_url"] == expected_default

        # 2) Unavailable deleted video (statusCode != 0, e.g. 10204)
        dead_detail = {"statusCode": 10204, "statusMsg": "author_invalid,status_audit_not_pass"}
        with patch("urllib.request.urlopen", return_value=_ssr_page(dead_detail)):
            dead_res = validate_video_url(
                "https://www.tiktok.com/@yutta/video/7684838545659317524",
                verify_availability=True,
            )
        assert dead_res["valid"] is False and dead_res["verified"] is True
        assert dead_res["availability"] == "unavailable"

        # 3) Private & share-disabled items fail live verification
        priv_detail = {
            "statusCode": 0,
            "itemInfo": {"itemStruct": {"id": "123", "author": {"uniqueId": "u"}, "privateItem": True}},
        }
        with patch("urllib.request.urlopen", return_value=_ssr_page(priv_detail)):
            priv_res = validate_video_url("https://www.tiktok.com/@u/video/123456", verify_availability=True)
        assert priv_res["valid"] is False and priv_res["availability"] == "private"

        no_share_detail = {
            "statusCode": 0,
            "itemInfo": {"itemStruct": {"id": "123", "author": {"uniqueId": "u"}, "shareEnabled": False}},
        }
        with patch("urllib.request.urlopen", return_value=_ssr_page(no_share_detail)):
            ns_res = validate_video_url("https://www.tiktok.com/@u/video/123456", verify_availability=True)
        assert ns_res["valid"] is False and ns_res["availability"] == "share_disabled"

        # 4) Offline / network failure returns valid syntax + unverified (never false green)
        with patch("urllib.request.urlopen", side_effect=OSError("timed out")):
            off_res = validate_video_url(expected_default, verify_availability=True)
        assert off_res["valid"] is True and off_res["verified"] is False
        assert off_res["availability"] == "unverified"

    def test_prepare_photo_secret_pillow(self, tmp_path):
        PIL_Image = pytest.importorskip("PIL.Image")
        from gui.services.validators import prepare_photo_secret

        # Create a 1200x900 RGBA image with transparency
        img_path = tmp_path / "big_alpha.png"
        img = PIL_Image.new("RGBA", (1200, 900), (255, 0, 0, 128))
        img.save(img_path, format="PNG")

        data_url = prepare_photo_secret(img_path, max_bytes=45000)
        assert data_url.startswith("data:image/jpeg;base64,")
        assert len(data_url.encode("ascii")) <= 45000

        jpeg_bytes = base64.b64decode(data_url.split(",", 1)[1])
        with PIL_Image.open(io.BytesIO(jpeg_bytes)) as out_img:
            assert out_img.format == "JPEG"
            assert max(out_img.size) <= 720

        with pytest.raises(FileNotFoundError):
            prepare_photo_secret(tmp_path / "missing.png")

    def test_repo_name_telegram_token_and_cron_math(self):
        from gui.services.validators import (
            cron_to_wib_summary,
            times_to_utc_cron,
            validate_repo_name,
            validate_telegram_token,
        )

        assert validate_repo_name("my-tiktok_streak.1")["valid"] is True
        assert validate_repo_name("")["valid"] is False
        assert validate_repo_name("..")["valid"] is False
        assert validate_repo_name("repo.git")["valid"] is False
        assert validate_repo_name("bad/name")["valid"] is False

        ok_tok = validate_telegram_token("1234567:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        assert ok_tok["valid"] is True and ok_tok["bot_id"] == "1234567"
        assert validate_telegram_token("123:short")["valid"] is False

        assert times_to_utc_cron(["08:00"]) == "0 1 * * *"
        assert times_to_utc_cron(["08:00", "20:00"]) == "0 1,13 * * *"
        assert times_to_utc_cron("02:30") == "30 19 * * *"
        with pytest.raises(ValueError, match="same minute"):
            times_to_utc_cron(["08:00", "20:15"])
        with pytest.raises(ValueError):
            times_to_utc_cron([])

        assert cron_to_wib_summary("0 1,13 * * *", lang="en") == "Daily at 08:00, 20:00 WIB"
        assert cron_to_wib_summary("30 19 * * *", lang="id") == "Setiap hari pukul 02:30 WIB"

    def test_timezone_presets_multi_cron_and_timestamp_formatting(self):
        from datetime import datetime, timedelta, timezone
        from gui.services.validators import (
            TIMEZONE_CHOICES,
            crons_to_local_times,
            detect_local_timezone,
            format_schedule_summary,
            format_utc_timestamp,
            get_timezone_info,
            times_to_utc_crons,
        )

        assert len(TIMEZONE_CHOICES) >= 20
        assert get_timezone_info("asia/tokyo")["short"] == "JST"
        assert get_timezone_info("unknown/zone")["id"] == "Asia/Jakarta"

        # detect_local_timezone matches matching offset and falls back on unusual offset
        with patch("gui.services.validators.datetime") as mock_dt:
            mock_dt.now.return_value.astimezone.return_value.utcoffset.return_value = timedelta(hours=9)
            assert detect_local_timezone() == "Asia/Jayapura"
            mock_dt.now.return_value.astimezone.return_value.utcoffset.return_value = timedelta(hours=-5)
            assert detect_local_timezone() == "America/New_York"
            mock_dt.now.return_value.astimezone.return_value.utcoffset.return_value = timedelta(hours=3, minutes=45)
            assert detect_local_timezone() == "Asia/Jakarta"

        # times_to_utc_crons: same minute grouped, mixed minutes produce multiple sorted crons
        assert times_to_utc_crons(["08:15", "20:15"], "Asia/Jakarta") == ["15 1,13 * * *"]
        assert times_to_utc_crons(["08:00", "20:30"], "Asia/Jakarta") == [
            "0 1 * * *",
            "30 13 * * *",
        ]
        # Non-WIB offsets: IST (+05:30) and ET (-05:00) with midnight wraparound
        assert times_to_utc_crons(["06:00", "22:00"], "Asia/Kolkata") == ["30 0,16 * * *"]
        assert times_to_utc_crons(["21:15", "08:15"], "America/New_York") == ["15 2,13 * * *"]
        assert times_to_utc_crons("00:00", "UTC") == ["0 0 * * *"]
        with pytest.raises(ValueError):
            times_to_utc_crons([], "UTC")

        # crons_to_local_times roundtrip
        assert crons_to_local_times(["0 1 * * *", "30 13 * * *"], "Asia/Jakarta") == ["08:00", "20:30"]
        assert crons_to_local_times("30 0,16 * * *", "Asia/Kolkata") == ["06:00", "22:00"]
        assert crons_to_local_times("*/15 * * * *", "UTC") == []

        # format_schedule_summary
        assert format_schedule_summary(["15 1,13 * * *"], "Asia/Jakarta", "en") == "Daily at 08:15, 20:15 WIB (01:15, 13:15 UTC)"
        assert format_schedule_summary(["0 1 * * *", "30 13 * * *"], "Asia/Jakarta", "id") == "Setiap hari pukul 08:00, 20:30 WIB (01:00, 13:30 UTC)"
        assert format_schedule_summary(["15 8 * * *"], "UTC", "en") == "Daily at 08:15 UTC"
        assert format_schedule_summary("*/30 * * * *", "Asia/Jakarta", "en") == "Cron UTC: */30 * * * *"
        assert format_schedule_summary("", "Asia/Jakarta", "id") == "Jadwal tidak valid"

        # format_utc_timestamp
        assert format_utc_timestamp("") == ""
        assert format_utc_timestamp("not-a-date") == "not-a-date"
        assert format_utc_timestamp("2026-11-18T01:05:00Z", "Asia/Jakarta") == "2026-11-18 08:05 WIB"
        assert format_utc_timestamp("2026-11-18T01:05:00+00:00", "Asia/Tokyo") == "2026-11-18 10:05 JST"
        assert format_utc_timestamp("2026-11-18T01:05:00Z", "America/New_York") == "2026-11-17 20:05 ET"


# ===========================================================================
# 8. gui/services/browsers.py
# ===========================================================================

class TestBrowsersService:
    def test_chromium_key_utf8_bom_and_decrypt_checks(self, tmp_path, monkeypatch):
        AES = pytest.importorskip("Crypto.Cipher.AES")
        from gui.services import browsers

        key = os.urandom(32)
        raw_dpapi_blob = b"DPAPI_WRAPPED_KEY"
        fake_win32crypt = types.SimpleNamespace(
            CryptUnprotectData=lambda b, *_a: ("", key if b == raw_dpapi_blob else b"dpapi_plain"),
        )
        monkeypatch.setitem(sys.modules, "win32crypt", fake_win32crypt)

        # Write Local State WITH UTF-8 BOM
        enc_b64 = base64.b64encode(b"DPAPI" + raw_dpapi_blob).decode("ascii")
        local_state = tmp_path / "Local State"
        local_state.write_bytes(b"\xef\xbb\xbf" + json.dumps({"os_crypt": {"encrypted_key": enc_b64}}).encode("utf-8"))

        loaded_key = browsers._chromium_key(str(tmp_path))
        assert loaded_key == key

        # < 31 length check for v10/v11
        with pytest.raises(ValueError, match="< 31"):
            browsers._decrypt_chromium(b"v10" + b"x" * 20, loaded_key)

        # Real AES-GCM v10 decryption
        nonce = b"0123456789ab"
        cipher = AES.new(loaded_key, AES.MODE_GCM, nonce=nonce)
        ct, tag = cipher.encrypt_and_digest(b"real_session_123")
        assert browsers._decrypt_chromium(b"v10" + nonce + ct + tag, loaded_key) == "real_session_123"

        # v20 raises V20EncryptedError
        with pytest.raises(browsers.V20EncryptedError):
            browsers._decrypt_chromium(b"v20" + b"0" * 40, loaded_key)

    def test_v20_detection_domain_filter_and_expiry_ordering(self, tmp_path, monkeypatch):
        AES = pytest.importorskip("Crypto.Cipher.AES")
        from gui.services import browsers

        monkeypatch.setattr(sys, "platform", "win32")
        local_app = tmp_path / "Local"
        roam_app = tmp_path / "Roam"
        monkeypatch.setenv("LOCALAPPDATA", str(local_app))
        monkeypatch.setenv("APPDATA", str(roam_app))

        key = b"K" * 32
        dpapi_calls = []

        def fake_unprotect(blob, *_a):
            dpapi_calls.append(blob)
            return ("", key)

        fake_win32crypt = types.SimpleNamespace(CryptUnprotectData=fake_unprotect)
        monkeypatch.setitem(sys.modules, "win32crypt", fake_win32crypt)

        def make_v10(val: str) -> bytes:
            nonce = b"123456789012"
            ct, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(val.encode("utf-8"))
            return b"v10" + nonce + ct + tag

        # Chrome profile with v20 cookies
        chrome_dir = local_app / "Google" / "Chrome" / "User Data"
        (chrome_dir / "Default" / "Network").mkdir(parents=True)
        (chrome_dir / "Local State").write_text(
            json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPIkey").decode()}}),
            encoding="utf-8-sig",
        )
        chrome_db = chrome_dir / "Default" / "Network" / "Cookies"
        with contextlib.closing(sqlite3.connect(chrome_db)) as con:
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER)")
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", (".tiktok.com", "sessionid", "", b"v20" + b"x" * 32, 99999999999999999))
            con.commit()

        # Brave profile with v10 cookies, multiple expiries, and spoofed domain
        brave_dir = local_app / "BraveSoftware" / "Brave-Browser" / "User Data"
        (brave_dir / "Default" / "Network").mkdir(parents=True)
        (brave_dir / "Local State").write_text(
            json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPIkey").decode()}}),
            encoding="utf-8-sig",
        )
        brave_db = brave_dir / "Default" / "Network" / "Cookies"
        now_webkit = int((1_800_000_000 + browsers._WEBKIT_EPOCH_OFFSET) * 1_000_000)
        with contextlib.closing(sqlite3.connect(brave_db)) as con:
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER)")
            # Spoofed non-TikTok domain must be excluded
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", ("not-tiktok.com", "sessionid", "", make_v10("spoofed"), now_webkit * 2))
            # Expired cookie
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", (".tiktok.com", "sessionid", "", make_v10("expired_sid"), 100))
            # Session cookie (expires_utc = 0)
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", ("tiktok.com", "sessionid", "", make_v10("session_sid"), 0))
            # Empty blob should be skipped before DPAPI
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", (".tiktok.com", "empty_blob", "", b"", now_webkit * 2))
            # Freshest unexpired cookie (should win!)
            con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?, ?)", ("www.tiktok.com", "sessionid", "", make_v10("fresh_sid"), now_webkit * 2))
            con.commit()

        sources = browsers.list_sources(enrich=True)
        assert len(sources) == 2
        # Brave (non-v20) sorts before Chrome (v20_blocked=True)
        assert sources[0]["browser_label"] == "Brave"
        assert sources[0]["v20_blocked"] is False
        assert sources[0]["has_tiktok"] == 4  # excludes not-tiktok.com
        assert sources[1]["browser_label"] == "Google Chrome"
        assert sources[1]["v20_blocked"] is True

        brave_res = browsers.read_source(sources[0])
        assert brave_res["sessionid"] == "fresh_sid"
        assert all(blob != b"" for blob in dpapi_calls)

        with pytest.raises(browsers.V20EncryptedError):
            browsers.read_source(sources[1])

        # Locked browser during list_sources(enrich=True) reports locked=True without crashing
        with patch.object(browsers, "_inspect_chromium_presence", side_effect=browsers.BrowserLocked("locked")):
            locked_sources = browsers.list_sources(enrich=True)
        assert all(s["locked"] is True for s in locked_sources)

    def test_wal_shm_sidecar_copy_and_browser_locked_on_execute(self, tmp_path):
        import shutil as _shutil
        from gui.services import browsers

        db_path = tmp_path / "Cookies"
        con = sqlite3.connect(db_path)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE t (v TEXT)")
            con.execute("INSERT INTO t VALUES ('initial')")
            con.commit()

            # Write a second committed transaction kept in -wal (disable auto-checkpoint)
            con.execute("PRAGMA wal_autocheckpoint=0")
            con.execute("INSERT INTO t VALUES ('from_wal')")
            con.commit()
            assert (tmp_path / "Cookies-wal").exists()
            assert (tmp_path / "Cookies-shm").exists()

            copied_srcs = []
            orig_copy2 = _shutil.copy2

            def spy_copy2(src, dst, *a, **kw):
                copied_srcs.append(os.path.basename(str(src)))
                return orig_copy2(src, dst, *a, **kw)

            # Force _connect to act locked so _query falls back to temp copy + -wal/-shm sidecars
            with patch.object(browsers, "_connect", side_effect=browsers.BrowserLocked("locked")), \
                 patch("gui.services.browsers.shutil.copy2", side_effect=spy_copy2):
                rows = browsers._query(str(db_path), "SELECT v FROM t ORDER BY rowid")
            assert [r[0] for r in rows] == ["initial", "from_wal"]
            assert {"Cookies", "Cookies-wal", "Cookies-shm"}.issubset(set(copied_srcs))
        finally:
            con.close()

        # OperationalError on execute (both primary & copied DB) raises BrowserLocked
        class BoomCon:
            def execute(self, *_a):
                raise sqlite3.OperationalError("unable to open database file")

            def close(self):
                pass

        with patch.object(browsers, "_connect", return_value=BoomCon()), \
             patch("sqlite3.connect", return_value=BoomCon()):
            with pytest.raises(browsers.BrowserLocked):
                browsers._query(str(db_path), "SELECT 1")

    def test_firefox_profiles_ini_and_disambiguation(self, tmp_path, monkeypatch):
        from gui.services import browsers

        monkeypatch.setattr(sys, "platform", "win32")
        roam = tmp_path / "Roam"
        local = tmp_path / "Local"
        monkeypatch.setenv("APPDATA", str(roam))
        monkeypatch.setenv("LOCALAPPDATA", str(local))

        ff_roam = roam / "Mozilla" / "Firefox"
        prof1 = ff_roam / "Profiles" / "default"
        prof1.mkdir(parents=True)
        (ff_roam / "profiles.ini").write_text("[Profile0]\nPath=Profiles/default\nIsRelative=1\n", encoding="utf-8")
        with contextlib.closing(sqlite3.connect(prof1 / "cookies.sqlite")) as con:
            con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, expiry INTEGER)")
            con.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'sessionid', 'ff_old', 10)")
            con.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'sessionid', 'ff_fresh', 9999999999)")
            con.commit()

        # Second Firefox root in Local with same profile folder name to verify ID disambiguation
        ff_local = local / "Mozilla" / "Firefox"
        prof2 = ff_local / "Profiles" / "default"
        prof2.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(prof2 / "cookies.sqlite")) as con:
            con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, expiry INTEGER)")
            con.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'sessionid', 'ff_local', 9999999999)")
            con.commit()

        sources = browsers.list_sources(enrich=True)
        assert len(sources) == 2
        assert len({s["id"] for s in sources}) == 2  # disambiguated
        res = browsers.read_source(sources[0])
        assert res["sessionid"] in ("ff_fresh", "ff_local")

    def test_chrome_local_state_friendly_names_skip_internal_and_firefox_ini_names(self, tmp_path, monkeypatch):
        from gui.services import browsers

        monkeypatch.setattr(sys, "platform", "win32")
        local = tmp_path / "Local"
        roam = tmp_path / "Roam"
        monkeypatch.setenv("LOCALAPPDATA", str(local))
        monkeypatch.setenv("APPDATA", str(roam))

        chrome_dir = local / "Google" / "Chrome" / "User Data"
        for folder in ("Default", "Profile 1", "Profile 2", "Profile 3", "Guest Profile", "System Profile"):
            (chrome_dir / folder / "Network").mkdir(parents=True, exist_ok=True)
            db = chrome_dir / folder / "Network" / "Cookies"
            with contextlib.closing(sqlite3.connect(db)) as con:
                con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER)")
                if folder == "Default":
                    con.execute("INSERT INTO cookies VALUES ('.tiktok.com', 'sessionid', '', ?, 9999999999)", (b"v20" + b"a" * 30,))
                con.commit()

        (chrome_dir / "Local State").write_bytes(
            b"\xef\xbb\xbf"
            + json.dumps({
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Personal"},
                        "Profile 1": {"gaia_name": "work@example.com"},
                        "Profile 2": {"shortcut_name": "Creator"},
                        "Profile 3": {},
                        "Guest Profile": {"name": "Guest"},
                    }
                }
            }).encode("utf-8")
        )

        # Firefox root with friendly Name= in profiles.ini
        ff_roam = roam / "Mozilla" / "Firefox"
        ff_prof = ff_roam / "Profiles" / "abcd1234.default-release"
        ff_prof.mkdir(parents=True)
        (ff_roam / "profiles.ini").write_text(
            "[Profile0]\nName=WorkProfile\nIsRelative=1\nPath=Profiles/abcd1234.default-release\n",
            encoding="utf-8",
        )
        with contextlib.closing(sqlite3.connect(ff_prof / "cookies.sqlite")) as con:
            con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, expiry INTEGER)")
            con.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'sessionid', 'ff_sid', 9999999999)")
            con.commit()

        sources = browsers.list_sources(enrich=True)
        chrome_sources = {s["profile"]: s for s in sources if s["kind"] == "chromium"}
        assert set(chrome_sources.keys()) == {"Default", "Profile 1", "Profile 2", "Profile 3"}
        assert "Guest Profile" not in chrome_sources
        assert "System Profile" not in chrome_sources
        assert chrome_sources["Default"]["display_name"] == "Personal"
        assert chrome_sources["Default"]["label"] == "Google Chrome — Personal (Default)"
        assert chrome_sources["Profile 1"]["display_name"] == "work@example.com"
        assert chrome_sources["Profile 1"]["label"] == "Google Chrome — work@example.com (Profile 1)"
        assert chrome_sources["Profile 2"]["display_name"] == "Creator"
        assert chrome_sources["Profile 2"]["label"] == "Google Chrome — Creator (Profile 2)"
        assert chrome_sources["Profile 3"]["display_name"] == "Profile 3"
        assert chrome_sources["Profile 3"]["label"] == "Google Chrome — Profile 3"

        ff_sources = [s for s in sources if s["kind"] == "firefox"]
        assert len(ff_sources) == 1
        assert ff_sources[0]["display_name"] == "WorkProfile"
        assert ff_sources[0]["label"] == "Firefox — WorkProfile"


# ===========================================================================
# 9. gui/services/tiktok_auth.py
# ===========================================================================

class TestTikTokAuthService:
    def test_parse_cookie_payload_and_check_session_api(self):
        from gui.services.tiktok_auth import check_session_api, parse_cookie_payload

        tsv = "sessionid\tsid_tsv\t.tiktok.com\t/\t2030\nuid_tt\t12345\t.tiktok.com\t/\t2030"
        assert parse_cookie_payload(tsv) == {"sessionid": "sid_tsv", "uid_tt": "12345"}

        hdr = "Cookie: sessionid=s1\r\n; Path=/; HttpOnly; sid_tt=s2"
        assert parse_cookie_payload(hdr) == {"sessionid": "s1", "sid_tt": "s2"}

        assert check_session_api("")["ok"] is False

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({"data": {"username": "alice", "user_id_str": "42"}})):
            ok_res = check_session_api("sid")
            assert ok_res["ok"] is True and ok_res["username"] == "alice" and ok_res["uid"] == "42"

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({"message": "session expired"})):
            exp_res = check_session_api("sid")
            assert exp_res["ok"] is False and exp_res["waf_blocked"] is False

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse("<html>WAF captcha</html>")):
            waf_res = check_session_api("sid")
            assert waf_res["ok"] is False and waf_res["waf_blocked"] is True

    def test_guided_browser_login_success_business_suite_and_cancel(self, tmp_path):
        from gui.services.tiktok_auth import guided_browser_login

        # 1) Success reaching /messages via native browser + CDP Storage.getCookies
        proc_ok = MagicMock()
        proc_ok.poll.return_value = None
        fake_clock = iter([0.0, 0.1, 0.2, 2.0, 2.1])
        tabs_ok = [{"type": "page", "url": "https://www.tiktok.com/messages?lang=en", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"}]
        cdp_cookies_ok = [
            {"name": "sessionid", "value": "live_sid", "domain": ".tiktok.com", "expires": 9999999999},
            {"name": "ttwid", "value": "tw_1", "domain": ".tiktok.com", "expires": 9999999999},
        ]

        with patch("gui.services.tiktok_auth._launch_native_browser", return_value=(proc_ok, 9222, "chrome")), \
             patch("gui.services.tiktok_auth._http_json_get", return_value=tabs_ok), \
             patch("gui.services.tiktok_auth._cdp_get_cookies", return_value=cdp_cookies_ok) as mock_cdp, \
             patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "alice"}), \
             patch("gui.services.tiktok_auth.time.time", side_effect=lambda: next(fake_clock, 3.0)), \
             patch("gui.services.tiktok_auth.time.sleep", return_value=None):
            res_ok = guided_browser_login(tmp_path / "prof_ok", timeout=60)
        assert res_ok["ok"] is True
        assert res_ok["inbox_verified"] is True
        assert res_ok["username"] == "alice"
        assert res_ok["sessionid"] == "live_sid"
        assert res_ok["browser"] == "chrome"
        mock_cdp.assert_called_once_with(9222, page_ws_url="ws://127.0.0.1:9222/devtools/page/1")
        proc_ok.terminate.assert_called_once()

        # 2) manual_grab_event on /login: retries when empty, succeeds once cookie exists
        proc_grab = MagicMock()
        proc_grab.poll.return_value = None
        grab_ev = threading.Event()
        grab_ev.set()
        statuses: list[str] = []
        tabs_login = [{"type": "page", "url": "https://www.tiktok.com/login", "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/1"}]
        cdp_seq = [
            [],
            [{"name": "sessionid", "value": "grab_sid", "domain": ".tiktok.com", "expires": 9999999999}],
        ]

        def fake_sleep(_sec):
            if not grab_ev.is_set():
                grab_ev.set()

        with patch("gui.services.tiktok_auth._launch_native_browser", return_value=(proc_grab, 9223, "edge")), \
             patch("gui.services.tiktok_auth._http_json_get", return_value=tabs_login), \
             patch("gui.services.tiktok_auth._cdp_get_cookies", side_effect=cdp_seq), \
             patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "bob"}), \
             patch("gui.services.tiktok_auth.time.sleep", side_effect=fake_sleep):
            res_grab = guided_browser_login(
                tmp_path / "prof_grab",
                timeout=60,
                status_cb=statuses.append,
                manual_grab_event=grab_ev,
            )
        assert res_grab["ok"] is True
        assert res_grab["sessionid"] == "grab_sid"
        assert res_grab["username"] == "bob"
        assert res_grab["inbox_verified"] is False
        assert res_grab["browser"] == "edge"
        assert any("No TikTok session cookie found yet" in s for s in statuses)
        proc_grab.terminate.assert_called_once()

        # 3) Redirected to business-suite
        proc_biz = MagicMock()
        proc_biz.poll.return_value = None
        tabs_biz = [{"type": "page", "url": "https://www.tiktok.com/business-suite/messages"}]

        with patch("gui.services.tiktok_auth._launch_native_browser", return_value=(proc_biz, 9224, "edge")), \
             patch("gui.services.tiktok_auth._http_json_get", return_value=tabs_biz), \
             patch("gui.services.tiktok_auth.time.sleep", return_value=None):
            res_biz = guided_browser_login(tmp_path / "prof_biz", timeout=60)
        assert res_biz["ok"] is False
        assert res_biz["business_suite"] is True
        assert res_biz["inbox_verified"] is False
        proc_biz.terminate.assert_called_once()

        # 4) Cancelled via cancel_event
        proc_cancel = MagicMock()
        proc_cancel.poll.return_value = None
        cancel = threading.Event()
        cancel.set()
        with patch("gui.services.tiktok_auth._launch_native_browser", return_value=(proc_cancel, 9225, "chrome")):
            res_cancel = guided_browser_login(tmp_path / "prof_cancel", cancel_event=cancel, timeout=60)
        assert res_cancel["ok"] is False and res_cancel["cancelled"] is True
        proc_cancel.terminate.assert_called_once()

    def test_cdp_get_cookies_storage_and_network_fallback(self):
        from gui.services.tiktok_auth import _cdp_get_cookies

        # 1) Storage.getCookies via /json/version filters to TikTok domains
        def fake_http_storage(url, timeout=2.0):
            assert url == "http://127.0.0.1:9333/json/version"
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/abc"}

        with patch("gui.services.tiktok_auth._http_json_get", side_effect=fake_http_storage), \
             patch("gui.services.tiktok_auth._ws_cdp_call", return_value={
                 "cookies": [
                     {"name": "sessionid", "value": "s1", "domain": ".tiktok.com"},
                     {"name": "other", "value": "x", "domain": "example.com"},
                 ]
             }) as mock_ws:
            out = _cdp_get_cookies(9333)
        assert out == [{"name": "sessionid", "value": "s1", "domain": ".tiktok.com"}]
        mock_ws.assert_called_once_with("ws://127.0.0.1:9333/devtools/browser/abc", "Storage.getCookies", timeout=10.0)

        # 2) Falls back to /json/list + Network.getAllCookies when Storage.getCookies fails
        def fake_http_fallback(url, timeout=2.0):
            if url.endswith("/json/version"):
                raise RuntimeError("no version endpoint")
            return [{"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/1"}]

        with patch("gui.services.tiktok_auth._http_json_get", side_effect=fake_http_fallback), \
             patch("gui.services.tiktok_auth._ws_cdp_call", return_value={
                 "cookies": [{"name": "sid_tt", "value": "s2", "domain": "www.tiktok.com"}]
             }) as mock_ws2:
            out2 = _cdp_get_cookies(9333)
        assert out2 == [{"name": "sid_tt", "value": "s2", "domain": "www.tiktok.com"}]
        mock_ws2.assert_called_once_with("ws://127.0.0.1:9333/devtools/page/1", "Network.getAllCookies", timeout=10.0)

    def test_verify_inbox_headless(self):
        from gui.services.tiktok_auth import verify_inbox_headless
        from src.exceptions import InboxUnavailableError, SessionExpiredError

        assert verify_inbox_headless("")["session_expired"] is True

        mock_browser = MagicMock()
        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "alice"}), \
             patch("src.browser.init_browser", return_value=(mock_browser, MagicMock())), \
             patch("src.browser.authenticate_session"):
            res = verify_inbox_headless("sessionid=abc")
        assert res["ok"] is True and res["inbox_verified"] is True and res["username"] == "alice"
        mock_browser.quit.assert_called_once()

        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "biz"}), \
             patch("src.browser.init_browser", return_value=(mock_browser, MagicMock())), \
             patch("src.browser.authenticate_session", side_effect=InboxUnavailableError("biz")):
            res_biz = verify_inbox_headless("sessionid=abc")
        assert res_biz["ok"] is False and res_biz["business_suite"] is True

        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": False, "username": ""}), \
             patch("src.browser.init_browser", return_value=(mock_browser, MagicMock())), \
             patch("src.browser.authenticate_session", side_effect=SessionExpiredError("expired")):
            res_exp = verify_inbox_headless("sessionid=abc")
        assert res_exp["ok"] is False and res_exp["session_expired"] is True

    def test_cloud_cookie_readiness_and_set_cookie_hydration(self):
        from gui.services.tiktok_auth import check_cloud_cookie_readiness, check_session_api

        # Bare 3-key session vs full regional cookies
        bare = check_cloud_cookie_readiness("sessionid=s1; sessionid_ss=s1; sid_tt=s1")
        assert bare["cloud_ready"] is False
        assert bare["is_bare_session"] is True
        assert bare["has_regional"] is False
        assert bare["count"] == 3
        assert bare["missing_regional"] == ["tt-target-idc", "store-idc"]

        regional = check_cloud_cookie_readiness("sessionid=s1; tt-target-idc=useast2a; ttwid=tw1")
        assert regional["cloud_ready"] is True
        assert regional["is_bare_session"] is False
        assert regional["has_regional"] is True
        assert regional["missing_regional"] == ["store-idc"]

        many_without_regional = check_cloud_cookie_readiness(
            "sessionid=s1; ttwid=t; odin_tt=o; uid_tt=u; msToken=m"
        )
        assert many_without_regional["cloud_ready"] is False
        assert many_without_regional["is_bare_session"] is False

        # check_session_api hydrates Set-Cookie headers (and skips deleted / Max-Age=0)
        class _HeadersWithGetAll:
            def get_all(self, name):
                if name.lower() == "set-cookie":
                    return [
                        "tt-target-idc=alisg; Path=/; Domain=.tiktok.com",
                        "store-idc=alisg; Path=/; HttpOnly",
                        "sid_guard=guard123; Path=/; Max-Age=3600",
                        "old_cookie=deleted; Path=/",
                        "zero_age=skipme; Max-Age=0; Path=/",
                    ]
                return []

        resp = _FakeHTTPResponse(
            {"data": {"username": "hydrated_user", "user_id_str": "77"}},
            headers=_HeadersWithGetAll(),
        )
        with patch("urllib.request.urlopen", return_value=resp):
            res = check_session_api("sessionid=bare_sid")
        assert res["ok"] is True
        assert res["cloud_ready"] is True
        assert res["cookie_count"] >= 4
        assert "tt-target-idc=alisg" in res["cookie_text"]
        assert "store-idc=alisg" in res["cookie_text"]
        assert "sid_guard=guard123" in res["cookie_text"]
        assert "old_cookie" not in res["cookie_text"]
        assert "zero_age" not in res["cookie_text"]

    def test_verify_inbox_headless_captcha_status_cb_and_cookie_harvest(self):
        from gui.services.tiktok_auth import verify_inbox_headless

        # 1) Captcha detection returns captcha_detected=True
        statuses_cap: list[str] = []
        browser_cap = MagicMock()
        browser_cap.current_url = "https://www.tiktok.com/messages?lang=en"
        browser_cap.execute_script.return_value = {"inbox": False, "captcha": True}
        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "cap_user", "cookie_text": "sessionid=abc"}), \
             patch("src.browser.init_browser", return_value=(browser_cap, MagicMock())), \
             patch("src.browser.authenticate_session"):
            res_cap = verify_inbox_headless("sessionid=abc", status_cb=statuses_cap.append)
        assert res_cap["ok"] is False
        assert res_cap["captcha_detected"] is True
        assert res_cap["inbox_verified"] is False
        assert res_cap["username"] == "cap_user"
        assert any("Verifying /messages inbox" in s for s in statuses_cap)
        browser_cap.quit.assert_called_once()

        # 2) CDP Network.getAllCookies harvesting upgrades bare cookies -> cloud_ready=True
        statuses_ok: list[str] = []
        browser_cdp = MagicMock()
        browser_cdp.current_url = "https://www.tiktok.com/messages?lang=en"
        browser_cdp.execute_script.return_value = {"inbox": True, "captcha": False}
        browser_cdp.execute_cdp_cmd.return_value = {
            "cookies": [
                {"name": "sessionid", "value": "abc", "domain": ".tiktok.com"},
                {"name": "tt-target-idc", "value": "useast2a", "domain": ".tiktok.com"},
                {"name": "store-idc", "value": "useast2a", "domain": ".tiktok.com"},
                {"name": "msToken", "value": "ms_xyz", "domain": "www.tiktok.com"},
                {"name": "ignored", "value": "nope", "domain": "example.com"},
            ]
        }
        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "alice", "cookie_text": "sessionid=abc"}), \
             patch("src.browser.init_browser", return_value=(browser_cdp, MagicMock())), \
             patch("src.browser.authenticate_session"):
            res_cdp = verify_inbox_headless("sessionid=abc", status_cb=statuses_ok.append)
        assert res_cdp["ok"] is True
        assert res_cdp["inbox_verified"] is True
        assert res_cdp["cloud_ready"] is True
        assert res_cdp["cookie_count"] == 4
        assert "tt-target-idc=useast2a" in res_cdp["upgraded_cookie_text"]
        assert "store-idc=useast2a" in res_cdp["upgraded_cookie_text"]
        assert "ignored=" not in res_cdp["upgraded_cookie_text"]
        assert any("Collecting full session cookies" in s for s in statuses_ok)
        browser_cdp.quit.assert_called_once()

        # 3) Fallback to browser.get_cookies() when CDP Network.getAllCookies fails
        browser_fb = MagicMock()
        browser_fb.current_url = "https://www.tiktok.com/messages"
        browser_fb.execute_script.return_value = {"inbox": True, "captcha": False}
        browser_fb.execute_cdp_cmd.side_effect = RuntimeError("cdp disabled")
        browser_fb.get_cookies.return_value = [
            {"name": "sessionid", "value": "abc", "domain": ".tiktok.com"},
            {"name": "tt-target-idc", "value": "alisg", "domain": ".tiktok.com"},
        ]
        with patch("gui.services.tiktok_auth.check_session_api", return_value={"ok": True, "username": "bob", "cookie_text": "sessionid=abc"}), \
             patch("src.browser.init_browser", return_value=(browser_fb, MagicMock())), \
             patch("src.browser.authenticate_session"):
            res_fb = verify_inbox_headless("sessionid=abc")
        assert res_fb["ok"] is True
        assert res_fb["cloud_ready"] is True
        assert "tt-target-idc=alisg" in res_fb["upgraded_cookie_text"]
        browser_fb.quit.assert_called_once()


# ===========================================================================
# 10. gui/services/github_api.py
# ===========================================================================

class TestGitHubApiService:
    def test_whoami_scope_check(self):
        from gui.services.github_api import GitHub, has_required_scopes

        assert has_required_scopes(["repo", "workflow"]) is True
        assert has_required_scopes(["repo"]) is False

        gh = GitHub("tok")
        with patch.object(gh, "_req_with_headers", return_value=({"login": "alice"}, {"x-oauth-scopes": "repo, workflow"})):
            me_ok = gh.whoami()
            assert me_ok["has_required_scopes"] is True and me_ok["scopes"] == ["repo", "workflow"]

        with patch.object(gh, "_req_with_headers", return_value=({"login": "alice"}, {"x-oauth-scopes": "repo"})):
            me_bad = gh.whoami()
            assert me_bad["has_required_scopes"] is False

        with patch.object(gh, "_req_with_headers", return_value=({"login": "alice"}, {})):
            me_fg = gh.whoami()
            assert me_fg["scopes_header_present"] is False and me_fg["has_required_scopes"] is True

    def test_sync_secrets_caches_key_and_deletes_stale(self):
        nacl_public = pytest.importorskip("nacl.public")
        nacl_encoding = pytest.importorskip("nacl.encoding")
        from gui.services.github_api import GitHub

        priv = nacl_public.PrivateKey.generate()
        pub_b64 = priv.public_key.encode(nacl_encoding.Base64Encoder()).decode("ascii")

        gh = GitHub("tok")
        calls = []

        def fake_req(method, path, body=None, api=None):
            calls.append((method, path, body))
            if method == "GET" and path.endswith("/actions/secrets/public-key"):
                return {"key": pub_b64, "key_id": "key_1"}
            if method == "GET" and "/actions/secrets?" in path:
                return {"secrets": [{"name": "TIKTOK_SESSION_ID"}, {"name": "TIKTOK_ACCOUNTS_JSON"}, {"name": "UNMANAGED"}]}
            if method == "DELETE" and path.endswith("/STALE_404"):
                from gui.services.github_api import GitHubError
                raise GitHubError("not found", status_code=404)
            return {}

        with patch.object(gh, "_req", side_effect=fake_req):
            res = gh.sync_secrets(
                "alice/tiktok-streak",
                desired={"TIKTOK_SESSION_ID": "sid_1", "MESSAGE": "Hey", "EMPTY_IGNORED": ""},
                managed_names=["TIKTOK_SESSION_ID", "TIKTOK_ACCOUNTS_JSON", "MESSAGE"],
            )
            assert res == {"written": ["TIKTOK_SESSION_ID", "MESSAGE"], "deleted": ["TIKTOK_ACCOUNTS_JSON"]}
            assert gh.delete_secret("alice/tiktok-streak", "STALE_404") is False

        # Public key fetched ONLY ONCE across multiple secrets
        key_fetches = [c for c in calls if c[0] == "GET" and c[1].endswith("/public-key")]
        assert len(key_fetches) == 1
        puts = [c for c in calls if c[0] == "PUT"]
        assert len(puts) == 2
        deletes = [c for c in calls if c[0] == "DELETE"]
        assert len(deletes) == 2  # TIKTOK_ACCOUNTS_JSON + STALE_404
        # UNMANAGED secret was NOT deleted
        assert not any("UNMANAGED" in c[1] for c in deletes)

    def test_create_private_repo_with_bot_and_cron_rewrite(self):
        from gui.services.github_api import GitHub, GitHubError

        gh = GitHub("tok")
        reqs = []
        blobs_by_sha = {}

        def fake_req_raw(method, path, body=None, api=None, follow_cross_host_auth_strip=False):
            reqs.append((method, path, body))
            if method == "GET" and path == "/repos/alice/my-private-streak":
                raise GitHubError("not found", status_code=404)
            if method == "GET" and path == "/user":
                return json.dumps({"login": "alice"}).encode("utf-8"), {"x-oauth-scopes": "repo, workflow"}, 200
            if method == "POST" and path == "/user/repos":
                assert body["private"] is True
                payload = {
                    "full_name": "alice/my-private-streak",
                    "default_branch": "main",
                    "private": True,
                    "html_url": "https://github.com/alice/my-private-streak",
                }
                return json.dumps(payload).encode("utf-8"), {}, 201
            if method == "GET" and path.endswith("/git/ref/heads/main"):
                return json.dumps({"object": {"sha": "head_sha_1"}}).encode("utf-8"), {}, 200
            if method == "GET" and path.endswith("/git/commits/head_sha_1"):
                return json.dumps({"tree": {"sha": "base_tree_1"}}).encode("utf-8"), {}, 200
            if method == "POST" and path.endswith("/git/blobs"):
                sha = f"blob_{len(blobs_by_sha)}"
                blobs_by_sha[sha] = base64.b64decode(body["content"])
                return json.dumps({"sha": sha}).encode("utf-8"), {}, 201
            if method == "POST" and path.endswith("/git/trees"):
                return json.dumps({"sha": "new_tree_1"}).encode("utf-8"), {}, 201
            if method == "POST" and path.endswith("/git/commits"):
                return json.dumps({"sha": "new_commit_1"}).encode("utf-8"), {}, 201
            return b"{}", {}, 200

        with patch.object(gh, "_req_raw", side_effect=fake_req_raw):
            out = gh.create_private_repo_with_bot("alice", "my-private-streak", cron_expr="30 2,14 * * *")

        assert out["full_name"] == "alice/my-private-streak"
        assert out["private"] is True
        assert out["commit_sha"] == "new_commit_1"
        tree_call = next(c for c in reqs if c[0] == "POST" and c[1].endswith("/git/trees"))
        paths = {e["path"]: e["sha"] for e in tree_call[2]["tree"]}
        assert {"main.py", "requirements.txt", "test_runner.py", "assets/default_photo.png", ".github/workflows/streak.yml"}.issubset(paths.keys())
        assert any(p.startswith("src/") and p.endswith(".py") for p in paths)
        wf_text = blobs_by_sha[paths[".github/workflows/streak.yml"]].decode("utf-8")
        assert "- cron: '30 2,14 * * *'" in wf_text

    def test_fork_custom_name_and_timeout_error(self):
        from gui.services.github_api import GitHub, GitHubError

        gh = GitHub("tok")
        with patch.object(gh, "_req", return_value={"full_name": "alice/custom-fork"}) as mock_req, \
             patch.object(gh, "get_repo", return_value={"full_name": "alice/custom-fork", "default_branch": "main"}):
            repo = gh.fork("alexanderfrit/tiktok-streak", name="custom-fork", timeout=5)
        assert repo["full_name"] == "alice/custom-fork"
        assert mock_req.call_args.args[2] == {"name": "custom-fork"}

        # Timeout when get_repo keeps returning 404
        clock = iter([0.0, 1.0, 10.0])
        with patch.object(gh, "_req", return_value={"full_name": "alice/slow-fork"}), \
             patch.object(gh, "get_repo", side_effect=GitHubError("not yet", status_code=404)), \
             patch("gui.services.github_api.time.time", side_effect=lambda: next(clock, 10.0)), \
             patch("gui.services.github_api.time.sleep", return_value=None):
            with pytest.raises(GitHubError) as exc:
                gh.fork("alexanderfrit/tiktok-streak", name="slow-fork", timeout=5)
            assert exc.value.status_code == 408

    def test_update_workflow_cron_dispatch_track_and_artifact_zip(self, tmp_path):
        from gui.services.github_api import GitHub, GitHubError, _NoCrossHostAuthRedirect

        gh = GitHub("tok")
        orig_yaml = "on:\n  schedule:\n    - cron: '0 1 * * *' # daily\n"
        b64_yaml = base64.b64encode(orig_yaml.encode("utf-8")).decode("ascii")

        def fake_req(method, path, body=None, api=None):
            if method == "GET" and "/contents/" in path:
                return {"content": b64_yaml, "sha": "wf_sha"}
            if method == "GET" and path == "/repos/alice/my-repo":
                return {"default_branch": "main"}
            if method == "PUT" and "/contents/" in path:
                return {"commit": {"sha": "c1"}, "sent_body": body}
            return {}

        with patch.object(gh, "_req", side_effect=fake_req):
            put_res = gh.update_workflow_cron("alice/my-repo", "0 1,13 * * *")
        decoded = base64.b64decode(put_res["sent_body"]["content"]).decode("utf-8")
        assert "- cron: '0 1,13 * * *' # daily" in decoded

        # dispatch_and_track picks the newly created run
        runs_seq = [
            [{"id": 100}],
            [{"id": 100}],
            [{"id": 101, "event": "workflow_dispatch"}, {"id": 100}],
        ]
        with patch.object(gh, "default_branch", return_value="main"), \
             patch.object(gh, "runs", side_effect=runs_seq), \
             patch.object(gh, "dispatch"), \
             patch("gui.services.github_api.time.sleep", return_value=None):
            run = gh.dispatch_and_track("alice/my-repo", timeout=10)
        assert run["id"] == 101

        # download_diagnostics_artifact extracts only debug_*.png and debug_*.html
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as zf:
            zf.writestr("debug_err.png", b"PNG")
            zf.writestr("nested/debug_err.html", b"<html>err</html>")
            zf.writestr("notes.txt", b"ignore")
            zf.writestr("../../evil.png", b"ignore")

        def fake_req_raw(method, path, body=None, api=None, follow_cross_host_auth_strip=False):
            assert follow_cross_host_auth_strip is True
            return zbuf.getvalue(), {}, 200

        with patch.object(gh, "_req", return_value={"artifacts": [{"id": 55, "name": "debug-diagnostics", "expired": False}]}), \
             patch.object(gh, "_req_raw", side_effect=fake_req_raw):
            extracted = gh.download_diagnostics_artifact("alice/my-repo", 101, tmp_path / "diag")
        assert sorted(p.name for p in extracted) == ["debug_err.html", "debug_err.png"]

        # Cross-host redirect strips Authorization header
        handler = _NoCrossHostAuthRedirect()
        orig_req = urllib.request.Request("https://api.github.com/logs", headers={"Authorization": "Bearer secret", "Accept": "application/json"})
        redir = handler.redirect_request(orig_req, None, 302, "Found", {}, "https://pipelines.actions.githubusercontent.com/blob")
        assert redir.get_header("Authorization") is None

        # Invalid cron raises GitHubError
        with pytest.raises(GitHubError):
            gh.update_workflow_cron("alice/my-repo", "bad cron")

    def test_device_flow_poll_cancel_timeout_and_telegram_find_chat_id(self):
        from gui.services.github_api import GitHubError, device_flow_poll, telegram_find_chat_id

        # 1) Pending -> success
        responses = [
            _FakeHTTPResponse({"error": "authorization_pending"}),
            _FakeHTTPResponse({"access_token": "gho_success"}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            tok = device_flow_poll("dev_1", interval=1, expires_in=30, cancel_event=threading.Event())
        assert tok == "gho_success"

        # 2) Cancel event
        ev = threading.Event()
        ev.set()
        with pytest.raises(GitHubError, match="cancelled"):
            device_flow_poll("dev_1", interval=1, cancel_event=ev)

        # 3) Timeout
        clock = iter([0.0, 0.5, 10.0])
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({"error": "authorization_pending"})), \
             patch("gui.services.github_api.time.time", side_effect=lambda: next(clock, 10.0)), \
             patch("gui.services.github_api.time.sleep", return_value=None):
            with pytest.raises(GitHubError, match="timed out"):
                device_flow_poll("dev_1", interval=1, timeout=5)

        # 4) telegram_find_chat_id uses offset=-1 and detects 409 webhook conflict
        valid_tok = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        captured_urls = []

        def fake_tg_open(url, timeout=15):
            captured_urls.append(url)
            return _FakeHTTPResponse({"ok": True, "result": [{"message": {"chat": {"id": 987654}}}]})

        with patch("urllib.request.urlopen", side_effect=fake_tg_open):
            res = telegram_find_chat_id(valid_tok)
        assert res["ok"] is True and res["chat_id"] == "987654"
        assert "offset=-1" in captured_urls[0]

        err_409 = _make_http_error(
            "https://api.telegram.org/bot.../getUpdates",
            409,
            {"ok": False, "description": "Conflict: can't use getUpdates method while webhook is active"},
        )
        with patch("urllib.request.urlopen", side_effect=err_409):
            res_409 = telegram_find_chat_id(valid_tok)
        assert res_409["ok"] is False
        assert res_409["webhook_conflict"] is True
        assert res_409["status_code"] == 409

    def test_resolve_repo_parts_list_user_repos_repo_has_workflow_and_sync_only_private(self):
        from gui.services.github_api import GitHub, GitHubError, resolve_repo_parts

        # 1) resolve_repo_parts
        assert resolve_repo_parts("octocat/my-repo", "alice") == ("octocat", "my-repo")
        assert resolve_repo_parts("my-repo", "alice") == ("alice", "my-repo")
        assert resolve_repo_parts("https://github.com/octocat/my-repo.git") == ("octocat", "my-repo")
        assert resolve_repo_parts("https://github.com/octocat/my-repo/") == ("octocat", "my-repo")
        assert resolve_repo_parts("", "alice") == ("alice", "")
        with pytest.raises(GitHubError):
            resolve_repo_parts("a/b/c")

        # 2) list_user_repos (pagination + normalization + limit=0)
        gh = GitHub("tok")
        assert gh.list_user_repos(limit=0) == []

        page1 = [
            {
                "full_name": f"alice/repo-{i}",
                "name": f"repo-{i}",
                "owner": {"login": "alice"},
                "private": i % 2 == 0,
                "default_branch": "main",
                "html_url": f"https://github.com/alice/repo-{i}",
            }
            for i in range(100)
        ]
        page2 = [
            {
                "full_name": "org/shared-bot",
                "name": "shared-bot",
                "owner": {"login": "org"},
                "private": True,
                "default_branch": "master",
                "html_url": "https://github.com/org/shared-bot",
            }
        ]
        seen_paths: list[str] = []

        def fake_list_req(method, path, body=None, api=None):
            seen_paths.append(path)
            if "&page=2" in path:
                return page2
            return page1

        with patch.object(gh, "_req", side_effect=fake_list_req):
            listed = gh.list_user_repos(limit=105)
        assert len(listed) == 101
        assert len(seen_paths) == 2
        assert listed[-1] == {
            "full_name": "org/shared-bot",
            "name": "shared-bot",
            "owner": "org",
            "private": True,
            "archived": False,
            "fork": False,
            "default_branch": "master",
            "html_url": "https://github.com/org/shared-bot",
        }

        # 3) repo_has_workflow (True / 404->False / 500->raise)
        with patch.object(gh, "_req", return_value={"name": "streak.yml"}):
            assert gh.repo_has_workflow("alice/my-repo") is True
        with patch.object(gh, "_req", side_effect=GitHubError("missing", status_code=404)):
            assert gh.repo_has_workflow("alice/my-repo") is False
        with patch.object(gh, "_req", side_effect=GitHubError("boom", status_code=500)):
            with pytest.raises(GitHubError):
                gh.repo_has_workflow("alice/my-repo")

        # 4) create_private_repo_with_bot(..., push_files=False) on existing repo
        sync_reqs: list[tuple[str, str]] = []
        orig_yaml = "on:\n  schedule:\n    - cron: '0 1 * * *'\n"
        b64_yaml = base64.b64encode(orig_yaml.encode("utf-8")).decode("ascii")

        def fake_sync_only_req(method, path, body=None, api=None):
            sync_reqs.append((method, path))
            if method == "GET" and path == "/repos/org/existing-bot":
                return {
                    "full_name": "org/existing-bot",
                    "default_branch": "main",
                    "private": True,
                    "html_url": "https://github.com/org/existing-bot",
                }
            if method == "GET" and path == "/repos/org/existing-bot/contents/.github/workflows/streak.yml":
                return {"name": "streak.yml"}
            if method == "GET" and path.startswith("/repos/org/existing-bot/contents/.github/workflows/streak.yml?ref="):
                return {"content": b64_yaml, "sha": "wf_sha_1"}
            return {}

        with patch.object(gh, "_req", side_effect=fake_sync_only_req):
            res_sync = gh.create_private_repo_with_bot(
                owner="alice",
                repo_name="org/existing-bot",
                cron_expr="0 1,13 * * *",
                push_files=False,
            )
        assert res_sync["full_name"] == "org/existing-bot"
        assert res_sync["files_pushed"] == 0
        assert not any("/git/" in p for _m, p in sync_reqs)
        assert ("PUT", "/repos/org/existing-bot/actions/permissions") in sync_reqs
        assert ("PUT", "/repos/org/existing-bot/actions/workflows/streak.yml/enable") in sync_reqs
        assert ("PUT", "/repos/org/existing-bot/contents/.github/workflows/streak.yml") in sync_reqs

        # Sync-only raises 404 GitHubError when workflow is missing
        def fake_missing_wf(method, path, body=None, api=None):
            if method == "GET" and path == "/repos/alice/no-wf":
                return {"full_name": "alice/no-wf", "default_branch": "main", "private": True}
            if method == "GET" and "/contents/.github/workflows/" in path:
                raise GitHubError("not found", status_code=404)
            return {}

        with patch.object(gh, "_req", side_effect=fake_missing_wf):
            with pytest.raises(GitHubError) as exc_info:
                gh.create_private_repo_with_bot("alice", "no-wf", push_files=False)
            assert exc_info.value.status_code == 404

    def test_multi_cron_extract_and_rewrite_under_schedule(self):
        from gui.services.github_api import (
            GitHub,
            GitHubError,
            _extract_workflow_cron_text,
            _rewrite_workflow_cron_text,
        )

        yaml_1 = (
            "name: TikTok Streak\n"
            "on:\n"
            "  schedule:\n"
            "    - cron: '0 1 * * *' # morning\n"
            "  workflow_dispatch:\n"
            "jobs:\n"
            "  run:\n"
            "    runs-on: ubuntu-latest\n"
        )
        assert _extract_workflow_cron_text(yaml_1) == "0 1 * * *"

        # 1 -> 2 crons
        yaml_2 = _rewrite_workflow_cron_text(yaml_1, ["15 1 * * *", "30 13 * * *"])
        assert _extract_workflow_cron_text(yaml_2) == "15 1 * * *\n30 13 * * *"
        assert "  workflow_dispatch:\n" in yaml_2

        # 2 -> 1 cron removes extra line cleanly
        yaml_back = _rewrite_workflow_cron_text(yaml_2, "45 2 * * *")
        assert _extract_workflow_cron_text(yaml_back) == "45 2 * * *"
        assert yaml_back.count("- cron:") == 1

        # Ignores commented cron lines & preserves CRLF
        yaml_crlf = "on:\r\n  schedule:\r\n    # - cron: '9 9 * * *'\r\n    - cron: '0 1 * * *'\r\n    - cron: '30 13 * * *'\r\n  workflow_dispatch:\r\n"
        assert _extract_workflow_cron_text(yaml_crlf) == "0 1 * * *\n30 13 * * *"
        rewritten_crlf = _rewrite_workflow_cron_text(yaml_crlf, "15 2 * * *\n45 14 * * *")
        assert "\r\n" in rewritten_crlf
        assert _extract_workflow_cron_text(rewritten_crlf) == "15 2 * * *\n45 14 * * *"

        # Invalid cron raises GitHubError
        with pytest.raises(GitHubError):
            _rewrite_workflow_cron_text(yaml_1, "0 1 * *\n")

        # GitHub.update_workflow_cron accepts multi-line cron string
        gh = GitHub("tok")
        b64_yaml = base64.b64encode(yaml_1.encode("utf-8")).decode("ascii")

        def fake_req(method, path, body=None, api=None):
            if method == "GET" and "/contents/" in path:
                return {"content": b64_yaml, "sha": "wf_sha"}
            if method == "GET" and path == "/repos/alice/my-repo":
                return {"default_branch": "main"}
            if method == "PUT" and "/contents/" in path:
                return {"sent_body": body}
            return {}

        with patch.object(gh, "_req", side_effect=fake_req):
            put_res = gh.update_workflow_cron("alice/my-repo", "0 1 * * *\n30 13 * * *")
        decoded = base64.b64decode(put_res["sent_body"]["content"]).decode("utf-8")
        assert _extract_workflow_cron_text(decoded) == "0 1 * * *\n30 13 * * *"


# ===========================================================================
# 11. gui/services/local_runner.py & gui/services/scheduler.py
# ===========================================================================

class TestLocalRunnerAndScheduler:
    def test_local_runner_log_redaction_env_restore_and_smoke_test(self, tmp_path, monkeypatch):
        from gui.services.local_runner import LocalRunner
        from gui.services.storage import AccountProfile, AppState, SecretStore

        mem_keyring: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda svc, k: mem_keyring.get((svc, k)),
            set_password=lambda svc, k, v: mem_keyring.__setitem__((svc, k), v),
            delete_password=lambda svc, k: mem_keyring.pop((svc, k), None),
        )
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
        ss = SecretStore(tmp_path / "sec")
        ss.set_github_token("ghp_mysecretgh12345")
        ss.set_telegram_token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        ss.set_account_cookie("a1", "sessionid=tok_secret_value_1")

        state = AppState(
            multi_account=False,
            accounts=[AccountProfile(id="a1", name="Main", friends=["alice", "bob"])],
            daily_cap=4,
            headless_local=True,
            telegram_enabled=True,
            telegram_chat_id="555",
            video_enabled=False,
            photo_enabled=False,
        )

        monkeypatch.setenv("DAILY_CAP", "99")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        captured_logs = []
        captured_kwargs = {}

        def fake_main_run(
            accounts_override=None,
            headless=None,
            only_account=None,
            only_friend=None,
            stop_event=None,
            progress_cb=None,
        ):
            captured_kwargs.update({
                "accounts_override": accounts_override,
                "headless": headless,
                "only_account": only_account,
                "only_friend": only_friend,
                "stop_event": stop_event,
                "progress_cb": progress_cb,
            })
            assert os.environ["DAILY_CAP"] == "4"
            assert os.environ["HEADLESS"] == "false"
            assert os.environ["TELEGRAM_BOT_TOKEN"] == "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
            logging.getLogger("tiktok-streak").info(
                "Sending with tok_secret_value_1 and ghp_mysecretgh12345"
            )
            return {"ok": True, "results": [{"name": "Main", "sent": 1}], "elapsed": 1, "critical_failure": False}

        runner = LocalRunner()
        with patch("main.run", new=fake_main_run):
            res = runner.run_smoke_test(
                state,
                ss,
                friend="@bob",
                account_id="a1",
                headless=False,
                log_cb=captured_logs.append,
            )

        assert res["ok"] is True
        assert captured_kwargs["headless"] is False
        assert captured_kwargs["only_friend"] == "@bob"
        assert len(captured_kwargs["accounts_override"]) == 1
        assert captured_kwargs["accounts_override"][0].friends == ["bob"]
        assert captured_kwargs["accounts_override"][0].video_url == ""
        assert captured_kwargs["accounts_override"][0].photo_path == ""

        # Env restored & handler detached
        assert os.environ["DAILY_CAP"] == "99"
        assert "TELEGRAM_BOT_TOKEN" not in os.environ
        assert not any(type(h).__name__ == "_RedactingCallbackHandler" for h in logging.getLogger("tiktok-streak").handlers)

        # Logs were redacted
        joined = "\n".join(captured_logs)
        assert "Sending with" in joined
        assert "tok_secret_value_1" not in joined
        assert "ghp_mysecretgh12345" not in joined

        # Env & handler are also restored on exception / SystemExit
        def boom_run(accounts_override=None, headless=None, only_account=None, only_friend=None, stop_event=None, progress_cb=None):
            raise RuntimeError("boom with ghp_mysecretgh12345")

        with patch("main.run", new=boom_run):
            err_res = runner.run(state, ss, log_cb=captured_logs.append)
        assert err_res["ok"] is False and err_res["critical_failure"] is True
        assert "ghp_mysecretgh12345" not in (err_res["error"] or "")
        assert os.environ["DAILY_CAP"] == "99"
        assert "TELEGRAM_BOT_TOKEN" not in os.environ
        assert not any(type(h).__name__ == "_RedactingCallbackHandler" for h in logging.getLogger("tiktok-streak").handlers)

        def exit_run(accounts_override=None, headless=None, only_account=None, only_friend=None, stop_event=None, progress_cb=None):
            raise SystemExit(1)

        with patch("main.run", new=exit_run):
            exit_res = runner.run(state, ss)
        assert exit_res["ok"] is False and exit_res["critical_failure"] is True
        assert os.environ["DAILY_CAP"] == "99"

    def test_windows_scheduler_commands_list_sync_remove(self, tmp_path, monkeypatch):
        from gui.services.scheduler import SchedulerError, TASK_PREFIX, WindowsScheduler

        monkeypatch.setattr(sys, "platform", "win32")
        sched = WindowsScheduler()

        # Non-frozen build_command
        run_gui_fake = tmp_path / "run_gui.py"
        run_gui_fake.write_text("", encoding="utf-8")
        cmd = sched.build_command(run_gui_path=run_gui_fake)
        assert cmd.endswith(f'"{run_gui_fake.resolve()}" --run-bot')

        # Frozen build_command
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "TikTokStreakSetup.exe"))
        frozen_cmd = sched.build_command()
        assert frozen_cmd == f'"{(tmp_path / "TikTokStreakSetup.exe").resolve()}" --run-bot'
        monkeypatch.delattr(sys, "frozen", raising=False)

        # list_tasks, sync_daily_tasks, remove_all_tasks
        calls = []

        def fake_subproc(argv, **_kwargs):
            calls.append(argv)
            if "/Query" in argv:
                csv_out = f'"\\{TASK_PREFIX}0800","8:00:00 AM","Ready"\n"\\OtherTask","N/A","Ready"\n'
                return subprocess.CompletedProcess(argv, 0, stdout=csv_out, stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with patch("gui.services.scheduler.subprocess.run", side_effect=fake_subproc):
            listed = sched.list_tasks()
            assert listed == [{"name": f"{TASK_PREFIX}0800", "next_run": "8:00:00 AM", "status": "Ready"}]

            synced = sched.sync_daily_tasks(["20:00", "08:00"], command='"C:\\Bot\\app.exe" --run-bot')
            assert synced["ok"] is True
            assert synced["removed"] == [f"{TASK_PREFIX}0800"]
            assert [c["name"] for c in synced["created"]] == [f"{TASK_PREFIX}0800", f"{TASK_PREFIX}2000"]

            removed = sched.remove_all_tasks()
            assert removed == [f"{TASK_PREFIX}0800"]

        # Validation: > 261 chars or invalid HH:MM raises SchedulerError
        with pytest.raises(SchedulerError, match="261"):
            sched.sync_daily_tasks(["08:00"], command="x" * 262)
        with pytest.raises(SchedulerError, match="Invalid daily time"):
            sched.sync_daily_tasks(["25:99"], command='"app.exe" --run-bot')


# ===========================================================================
# 12. gui/services/theme.py & AppState.accent_mode
# ===========================================================================

class TestThemeService:
    def test_appstate_accent_mode_roundtrip_and_sanitization(self, tmp_path):
        from gui.services.storage import AppState, ConfigStore

        assert AppState().accent_mode == "system"
        assert AppState.from_dict({"accent_mode": "TIKTOK"}).accent_mode == "tiktok"
        assert AppState.from_dict({"accent_mode": "custom"}).accent_mode == "custom"
        assert AppState.from_dict({"accent_mode": "neon-green"}).accent_mode == "system"

        store = ConfigStore(tmp_path / "accent_cfg")
        for mode in ("system", "tiktok", "custom"):
            store.save(AppState(accent_mode=mode))
            assert store.load().accent_mode == mode

    def test_windows_personalization_winreg_abgr_and_fallbacks(self, monkeypatch):
        pytest.importorskip("PySide6")
        from gui.services.theme import (
            DEFAULT_TIKTOK_ACCENT,
            DEFAULT_WINDOWS_ACCENT,
            get_windows_personalization,
            resolve_accent_color,
        )

        # 1) Non-Windows returns safe defaults
        monkeypatch.setattr(sys, "platform", "linux")
        assert get_windows_personalization() == {
            "accent_color": DEFAULT_WINDOWS_ACCENT,
            "dark_mode": False,
            "transparency": True,
        }

        # 2) Mocked winreg on win32: AccentColor ABGR 0xffd47800 -> #0078d4
        monkeypatch.setattr(sys, "platform", "win32")

        class _FakeKey:
            def __init__(self, values: dict[str, int]):
                self._values = values

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _make_winreg(dwm_values: dict[str, int] | None, pers_values: dict[str, int] | None):
            def open_key(_root, subkey):
                if subkey.endswith(r"\DWM"):
                    if dwm_values is None:
                        raise OSError("missing DWM key")
                    return _FakeKey(dwm_values)
                if subkey.endswith(r"\Personalize"):
                    if pers_values is None:
                        raise OSError("missing Personalize key")
                    return _FakeKey(pers_values)
                raise OSError("unknown key")

            def query_value(key, name):
                if name not in key._values:
                    raise OSError(f"missing {name}")
                return key._values[name], 4

            return types.SimpleNamespace(
                HKEY_CURRENT_USER=1,
                OpenKey=open_key,
                QueryValueEx=query_value,
            )

        fake_reg = _make_winreg(
            {"AccentColor": 0xFFD47800},
            {"AppsUseLightTheme": 0, "EnableTransparency": 0},
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_reg)
        pers = get_windows_personalization()
        assert pers == {
            "accent_color": "#0078d4",
            "dark_mode": True,
            "transparency": False,
        }
        assert resolve_accent_color("system") == "#0078d4"
        assert resolve_accent_color("tiktok") == DEFAULT_TIKTOK_ACCENT
        assert resolve_accent_color("custom", "#AbC") == "#aabbcc"
        assert resolve_accent_color("custom", "bad-hex") == "#0078d4"

        # 3) Missing AccentColor falls back to ColorizationColor (0xAARRGGBB)
        fake_reg_colorization = _make_winreg(
            {"ColorizationColor": 0xFFFE2C55},
            {"AppsUseLightTheme": 1, "EnableTransparency": 1},
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_reg_colorization)
        pers2 = get_windows_personalization()
        assert pers2 == {
            "accent_color": "#fe2c55",
            "dark_mode": False,
            "transparency": True,
        }

        # 4) Missing registry keys + unavailable qframelesswindow fallback -> default #0078d4
        fake_reg_missing = _make_winreg(None, None)
        monkeypatch.setitem(sys.modules, "winreg", fake_reg_missing)
        broken_qfw = types.SimpleNamespace(
            getSystemAccentColor=MagicMock(side_effect=RuntimeError("no DWM"))
        )
        monkeypatch.setitem(sys.modules, "qframelesswindow.utils", broken_qfw)
        pers_fallback = get_windows_personalization()
        assert pers_fallback == {
            "accent_color": DEFAULT_WINDOWS_ACCENT,
            "dark_mode": False,
            "transparency": True,
        }

    def test_escape_mnemonic_tabular_font_and_create_app_icon(self):
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        assert app is not None

        from gui.services.theme import create_app_icon, escape_qt_mnemonic, tabular_mono_font

        assert escape_qt_mnemonic("Save & Deploy") == "Save && Deploy"
        assert escape_qt_mnemonic("A & B & C") == "A && B && C"
        assert escape_qt_mnemonic("") == ""

        font = tabular_mono_font(11)
        assert font.fixedPitch() is True
        assert font.pointSize() == 11
        assert font.families()[0] == "Cascadia Mono"

        for dark in (True, False):
            icon = create_app_icon("#0078d4", dark=dark)
            assert not icon.isNull()
            for sz in (16, 32, 48, 64, 128):
                pm = icon.pixmap(sz, sz)
                assert not pm.isNull()
                assert pm.width() == sz and pm.height() == sz

        # Invalid hex falls back cleanly without raising
        fallback_icon = create_app_icon("not-a-hex", dark=True)
        assert not fallback_icon.isNull()


class TestRecipientSafetyAndIsolation:
    def test_open_inbox_chat_never_matches_single_letter_nickname_and_skips_unread(self):
        from src.actions import open_inbox_chat

        mock_b = MagicMock()
        mock_b.current_url = "https://www.tiktok.com/messages?lang=en"
        active_header = {"val": None}
        clicked_indices: list[int] = []

        rows_data = [
            {"idx": 0, "nickname": "M", "handle": None, "unread": False, "rowKey": "m|0"},
            {"idx": 1, "nickname": "Secret Unread", "handle": None, "unread": True, "rowKey": "u|1"},
            {"idx": 2, "nickname": "Mev Morales", "handle": None, "unread": False, "rowKey": "mev|2"},
        ]

        def fake_exec(script, *args):
            if "READ_ACTIVE_CHAT" in script or "dm-new-chat-header" in script:
                return active_header["val"]
            if "dm-new-conversation-item" in script and "rowKey" in script:
                return rows_data
            if "window.__botAllowSidebarClick = true" in script and args:
                idx = int(args[0])
                clicked_indices.append(idx)
                if idx == 0:
                    active_header["val"] = "mlnnt"
                elif idx == 1:
                    active_header["val"] = "other_unread"
                elif idx == 2:
                    active_header["val"] = "mevaldi_"
                return True
            if "scrollTop" in script:
                return False
            return None

        mock_b.execute_script.side_effect = fake_exec
        with patch("src.actions.find_element_by_candidates", side_effect=TimeoutError("no search")), \
             patch("time.sleep", return_value=None):
            resolved = open_inbox_chat(mock_b, "mevaldi_")

        assert resolved == "mevaldi_"
        # Row 2 ("Mev Morales") has priority 2 (>= 3 char token) while "M" (1 char) is not boosted,
        # and unread row 1 is NEVER clicked!
        assert 1 not in clicked_indices
        assert clicked_indices[0] == 2

    def test_open_inbox_chat_tier1_exact_fiber_handle_even_if_unread(self):
        from src.actions import open_inbox_chat

        mock_b = MagicMock()
        mock_b.current_url = "https://www.tiktok.com/messages?lang=en"
        active_header = {"val": "mlnnt"}
        clicked: list[int] = []
        rows_data = [
            {"idx": 0, "nickname": "M", "handle": "mlnnt", "unread": False, "rowKey": "m|0"},
            {"idx": 1, "nickname": "Someone", "handle": "hisyamanartaa", "unread": True, "rowKey": "h|1"},
        ]

        def fake_exec(script, *args):
            if "dm-new-chat-header" in script:
                return active_header["val"]
            if "rowKey" in script:
                return rows_data
            if "window.__botAllowSidebarClick = true" in script and args:
                clicked.append(int(args[0]))
                active_header["val"] = "hisyamanartaa"
                return True
            return None

        mock_b.execute_script.side_effect = fake_exec
        with patch("time.sleep", return_value=None):
            resolved = open_inbox_chat(mock_b, "hisyamanartaa")
        assert resolved == "hisyamanartaa"
        assert clicked == [1]

    def test_send_streak_message_aborts_before_return_on_mid_send_chat_switch(self):
        from selenium.webdriver.common.keys import Keys
        from src.actions import send_streak_message

        mock_b = MagicMock()
        mock_input = MagicMock()
        # First 3 assertions during open/click/type pass as "mevaldi_",
        # final check right before Keys.RETURN sees user clicked onto "mlnnt"!
        header_seq = iter(["mevaldi_", "mevaldi_", "mevaldi_", "mlnnt"])

        with patch("src.actions.open_inbox_chat", return_value="mevaldi_"), \
             patch("src.share.get_active_chat_handle", side_effect=lambda _b: next(header_seq)), \
             patch("src.actions.find_element_by_candidates", return_value=mock_input), \
             patch("src.actions.dump_debug_diagnostics") as mock_dump, \
             patch("time.sleep", return_value=None):
            res = send_streak_message(mock_b, "mevaldi_", "hi")

        assert res["sent"] is False
        assert "mlnnt" in (res["error"] or "")
        assert all(call.args[0] != Keys.RETURN for call in mock_input.send_keys.call_args_list)
        mock_dump.assert_called_once()

    def test_main_run_skips_share_and_photo_when_text_fails(self):
        import main
        from src.config import AccountConfig

        acc = AccountConfig(
            name="Main",
            session_id="sid",
            friends=["mevaldi_", "danarygp"],
            video_url="https://www.tiktok.com/@u/video/1234567890123456789",
            share_times=2,
            photo_path="assets/default_photo.png",
        )
        mock_b = MagicMock()

        def fake_text(**kwargs):
            if kwargs["friend"] == "mevaldi_":
                return {"friend": "mevaldi_", "sent": False, "error": "Header mismatch"}
            return {"friend": kwargs["friend"], "sent": True, "error": None}

        with patch("main.init_browser", return_value=(mock_b, MagicMock())), \
             patch("main.install_ws_hook"), \
             patch("main.install_media_guard"), \
             patch("main.authenticate_session"), \
             patch("main.send_streak_message", side_effect=fake_text), \
             patch("main.send_share_card", return_value={"sent": True, "error": None}) as mock_share, \
             patch("main.send_photo_card", return_value={"sent": True, "error": None}) as mock_photo, \
             patch("main.notify_streak_summary"), \
             patch("random.shuffle", side_effect=lambda x: x), \
             patch("time.sleep", return_value=None):
            out = main.run(accounts_override=[acc])

        assert out["ok"] is True
        assert mock_share.call_count == 2
        assert all(c.args[1] == "danarygp" for c in mock_share.call_args_list)
        assert mock_photo.call_count == 1
        assert mock_photo.call_args.args[1] == "danarygp"

    def test_send_share_card_refuses_stale_template_or_header_mismatch(self):
        from src.share import send_share_card

        mock_b = MagicMock()
        # 1) Wrong active header -> refuse immediately without calling fetch_item_detail or send
        with patch("src.share.get_active_chat_handle", return_value="mlnnt"), \
             patch("src.share.fetch_item_detail") as mock_detail:
            res = send_share_card(mock_b, "mevaldi_", "7684838545659317524")
        assert res["sent"] is False
        assert "mismatch" in (res["error"] or "").lower()
        mock_detail.assert_not_called()

        # 2) Explicit template belonging to another friend -> rejected
        with patch("src.share.get_active_chat_handle", return_value="mevaldi_"), \
             patch("src.share.fetch_item_detail", return_value={"ok": True, "data": {}}), \
             patch("src.share.build_share_json", return_value="{}"):
            mock_b.execute_async_script.return_value = {"ok": False, "error": "no captured HTTP send for target recipient"}
            res2 = send_share_card(
                mock_b,
                "mevaldi_",
                "7684838545659317524",
                template={"b64": "AAAA", "sock": 0, "handle": "danarygp"},
            )
        assert res2["sent"] is False
        assert "no captured http send" in (res2["error"] or "").lower()

    def test_send_photo_card_aborts_and_dismisses_modal_on_header_mismatch(self, tmp_path):
        from src.share import send_photo_card

        photo = tmp_path / "p.png"
        photo.write_bytes(b"PNG")
        mock_b = MagicMock()
        mock_b.current_url = "https://www.tiktok.com/messages?lang=en"
        mock_inp = MagicMock()
        mock_b.find_elements.return_value = [mock_inp]

        # Sequence of active chat checks:
        # 1 (already on friend), 2 (verified after open), 3 (after media btn wait),
        # 4 (pre-CLICK_MEDIA_JS) -> "mevaldi_",
        # 5 (inside modal loop right before Send click) -> user clicked "mlnnt"!
        handles = iter(["mevaldi_", "mevaldi_", "mevaldi_", "mevaldi_", "mlnnt"])
        clicked_send = {"yes": False}

        def fake_exec(script, *args):
            if "dm-new-chat-header" in script:
                return next(handles, "mlnnt")
            if "dm-new-media-btn" in script:
                return True
            if "SEND_BTN_PRESENT" in script or "^send\\b" in script and "best.click" not in script:
                return True
            if "__mediaSend" in script and "commit" in script:
                return {"commit": 0, "media": None}
            if "__recv" in script:
                return []
            if "best.click" in script:
                clicked_send["yes"] = True
                return {"clicked": True}
            return None

        mock_b.execute_script.side_effect = fake_exec
        with patch("src.actions.dismiss_modals") as mock_dismiss, \
             patch("time.sleep", return_value=None):
            res = send_photo_card(mock_b, "mevaldi_", str(photo))

        assert res["sent"] is False
        assert "mismatch" in (res["error"] or "").lower()
        assert clicked_send["yes"] is False
        mock_dismiss.assert_called()

    def test_headful_guard_installed_only_when_not_headless(self):
        from src.browser import HEADFUL_GUARD_JS, init_browser

        mock_chrome = MagicMock()
        with patch("src.browser.webdriver.Chrome", return_value=mock_chrome):
            init_browser(headless=True)
        headless_sources = [c.args[1]["source"] for c in mock_chrome.execute_cdp_cmd.call_args_list if len(c.args) > 1]
        assert not any("__headfulGuard" in s for s in headless_sources)

        mock_chrome2 = MagicMock()
        with patch("src.browser.webdriver.Chrome", return_value=mock_chrome2):
            init_browser(headless=False)
        headful_sources = [c.args[1]["source"] for c in mock_chrome2.execute_cdp_cmd.call_args_list if len(c.args) > 1]
        assert any("__headfulGuard" in s for s in headful_sources)
        assert "captcha" in HEADFUL_GUARD_JS.lower()
        assert "__botAllowSidebarClick" in HEADFUL_GUARD_JS
        assert "dm-new-conversation-list" in HEADFUL_GUARD_JS

    def test_read_active_chat_handle_js_scene_business_dom_and_exclusion_isolation(self):
        from urllib.parse import quote
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from src.share import INSPECT_INBOX_ROWS_JS, READ_ACTIVE_CHAT_HANDLE_JS

        html_doc = """<!DOCTYPE html>
<html><body style="margin:0;">
<div style="display:flex;width:1280px;height:720px;">
  <div data-e2e="dm-new-conversation-list" style="width:280px;">
    <div data-conv-id="0:1:100:200" data-e2e="dm-new-conversation-item" aria-selected="true" style="height:60px;">
      <p class="PInfoNickname">cel</p>
      <a href="/@wrong_sidebar">@wrong_sidebar</a>
    </div>
    <div data-conv-id="0:1:100:300" data-e2e="dm-new-conversation-item" aria-selected="false" style="height:60px;">
      <p class="PInfoNickname">Ribca</p>
      <div data-e2e="dm-new-conversation-unread">1</div>
    </div>
  </div>
  <div data-e2e="dm-new-chatbox" style="flex:1;">
    <div class="css-1pr30s--DivChatHeader" style="height:56px;">
      <a href="/@celuley">
        <p data-e2e="dm-new-chat-nickname">cel</p>
        <p data-e2e="chat-uniqueid">@celuley</p>
      </a>
    </div>
    <div data-e2e="dm-new-message-list" style="height:500px;">
      <div data-e2e="dm-new-chat-item">
        <a href="/@other_bubble">other</a>
      </div>
    </div>
    <div data-e2e="dm-new-chat-bottom" style="height:60px;">
      <div data-e2e="dm-new-input-editor"></div>
    </div>
  </div>
</div>
</body></html>"""

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1280,720")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=opts)
        try:
            driver.get("data:text/html;charset=utf-8," + quote(html_doc))
            assert driver.execute_script(READ_ACTIVE_CHAT_HANDLE_JS) == "celuley"
            rows = driver.execute_script(INSPECT_INBOX_ROWS_JS)
            assert len(rows) == 2
            assert rows[0]["selected"] is True
            assert rows[0]["rowKey"] == "0:1:100:200"
            assert rows[1]["unread"] is True

            # Removing the right-pane header must NOT leak sidebar or message-bubble handles
            driver.execute_script("document.querySelector('div[class*=\"DivChatHeader\"]').remove();")
            assert driver.execute_script(READ_ACTIVE_CHAT_HANDLE_JS) is None
        finally:
            driver.quit()

    def test_open_inbox_chat_tier2_probe_skips_selector_timeout_dump_and_dumps_on_failure(self):
        from src.actions import INBOX_SEARCH_CANDIDATES, open_inbox_chat
        from src.exceptions import DMBlockedError

        assert all("search-user-input" not in sel for _by, sel in INBOX_SEARCH_CANDIDATES)
        mock_b = MagicMock()
        mock_b.current_url = "https://www.tiktok.com/messages?lang=en"
        mock_b.find_elements.return_value = []
        mock_b.execute_script.side_effect = lambda script, *args: [] if "dm-new-conversation-item" in script else None

        with patch("src.actions.dump_debug_diagnostics") as mock_dump, \
             patch("time.sleep", return_value=None):
            with pytest.raises(DMBlockedError):
                open_inbox_chat(mock_b, "celuley")

        reasons = [c.args[1] for c in mock_dump.call_args_list if len(c.args) > 1]
        assert "selector_timeout" not in reasons
        assert "open_chat_failed_celuley" in reasons


class TestGitHubWorkflowControlAndDiscovery:
    def test_workflow_pause_resume_cancel_and_discover_bot_repos(self):
        from gui.services.github_api import GitHub, GitHubError

        gh = GitHub("ghp_tok")
        reqs: list[tuple[str, str]] = []

        def fake_req(method, path, body=None, api=None):
            reqs.append((method, path))
            if path.startswith("/user/repos"):
                return [
                    {"full_name": "alice/notes", "name": "notes", "owner": {"login": "alice"}, "private": True, "archived": False, "fork": False, "default_branch": "main", "html_url": ""},
                    {"full_name": "alice/tiktok-streak", "name": "tiktok-streak", "owner": {"login": "alice"}, "private": True, "archived": False, "fork": False, "default_branch": "main", "html_url": ""},
                    {"full_name": "alice/tiktok-streak-2", "name": "tiktok-streak-2", "owner": {"login": "alice"}, "private": False, "archived": False, "fork": True, "default_branch": "main", "html_url": ""},
                    {"full_name": "alice/old-archived", "name": "old-archived", "owner": {"login": "alice"}, "private": True, "archived": True, "fork": False, "default_branch": "main", "html_url": ""},
                    {"full_name": "alice/paused-bot", "name": "paused-bot", "owner": {"login": "alice"}, "private": True, "archived": False, "fork": False, "default_branch": "main", "html_url": ""},
                ]
            if path == "/repos/alice/notes/actions/workflows/streak.yml":
                raise GitHubError("Not Found", status_code=404)
            if path == "/repos/alice/tiktok-streak/actions/workflows/streak.yml":
                return {"id": 1, "state": "active"}
            if path == "/repos/alice/tiktok-streak-2/actions/workflows/streak.yml":
                return {"id": 2, "state": "active"}
            if path == "/repos/alice/paused-bot/actions/workflows/streak.yml":
                return {"id": 3, "state": "disabled_manually"}
            if "/actions/workflows/streak.yml/runs" in path:
                return {
                    "workflow_runs": [
                        {"id": 1001, "status": "in_progress"},
                        {"id": 1002, "status": "completed"},
                    ]
                }
            return {}

        with patch.object(gh, "_req", side_effect=fake_req):
            disc = gh.discover_bot_repos()
            assert [r["full_name"] for r in disc["bot_repos"]] == [
                "alice/tiktok-streak",
                "alice/tiktok-streak-2",
                "alice/paused-bot",
            ]
            assert disc["has_duplicates"] is True
            assert len(disc["active_bot_repos"]) == 2

            pause_res = gh.pause_workflow("alice/tiktok-streak")
            assert pause_res["paused"] is True
            assert pause_res["cancelled_run_ids"] == [1001]

        assert ("PUT", "/repos/alice/tiktok-streak/actions/workflows/streak.yml/disable") in reqs
        assert ("POST", "/repos/alice/tiktok-streak/actions/runs/1001/cancel") in reqs
        assert ("POST", "/repos/alice/tiktok-streak/actions/runs/1002/cancel") not in reqs


class TestUpdaterService:
    def test_version_comparison_check_for_update_and_sha256_download_and_self_update_cmd(self, tmp_path):
        from gui.services.updater import (
            check_for_update,
            download_update_asset,
            is_newer_version,
            launch_windows_self_update,
            parse_version,
        )

        assert parse_version("v1.3.0") == (1, 3, 0)
        assert is_newer_version("v1.3.1", "1.3.0") is True
        assert is_newer_version("1.3.0", "1.3.0") is False
        assert is_newer_version("1.2.1", "1.3.0") is False

        # check_for_update filters untrusted URLs and surfaces valid SHA-256 digest
        fake_payload = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/alexanderfrit/tiktok-streak/releases/tag/v1.4.0",
            "body": "Fix recipient mismatch",
            "assets": [
                {
                    "name": "TikTokStreakSetup.exe",
                    "browser_download_url": "https://github.com/alexanderfrit/tiktok-streak/releases/download/v1.4.0/TikTokStreakSetup.exe",
                    "size": 4096,
                    "digest": "sha256:" + ("a" * 64),
                }
            ],
        }

        class _MockResp:
            url = "https://objects.githubusercontent.com/release/TikTokStreakSetup.exe"
            headers = {"Content-Length": "2048"}

            def __init__(self, chunks):
                self._chunks = list(chunks)

            def read(self, _n=65536):
                return self._chunks.pop(0) if self._chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import hashlib
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(fake_payload).encode()
            info = check_for_update(current_version="1.3.0")
        assert info["ok"] is True
        assert info["has_update"] is True
        assert info["latest_version"] == "1.4.0"
        assert info["asset_sha256"] == "a" * 64

        # Download validates MZ header + exact SHA-256 + rejects bad checksum
        exe_bytes = b"MZ" + (b"\x90" * 2046)
        good_sha = hashlib.sha256(exe_bytes).hexdigest()
        dest = tmp_path / "dl" / "TikTokStreakSetup-v1.4.0.exe"

        with patch("urllib.request.urlopen", return_value=_MockResp([exe_bytes])):
            out = download_update_asset(
                "https://github.com/alexanderfrit/tiktok-streak/releases/download/v1.4.0/TikTokStreakSetup.exe",
                dest,
                expected_sha256=good_sha,
                expected_size=len(exe_bytes),
            )
        assert out.is_file() and out.read_bytes() == exe_bytes

        bad_dest = tmp_path / "dl" / "bad.exe"
        with patch("urllib.request.urlopen", return_value=_MockResp([exe_bytes])):
            with pytest.raises(ValueError, match="SHA-256"):
                download_update_asset(
                    "https://github.com/alexanderfrit/tiktok-streak/releases/download/v1.4.0/TikTokStreakSetup.exe",
                    bad_dest,
                    expected_sha256="b" * 64,
                )
        assert not bad_dest.exists()

        # Self-update helper script writes clean rollback batch script without spawning real cmd in test
        from gui.services.updater import _sanitize_relaunch_env

        sanitized = _sanitize_relaunch_env(
            {
                "PATH": "C:\\Windows",
                "_MEIPASS2": "C:\\Temp\\_MEI123",
                "_PYI_ARCHIVE_FILE": "C:\\old.exe",
                "QT_PLUGIN_PATH": "C:\\Temp\\_MEI123\\PySide6\\plugins",
                "SYSTEMROOT": "C:\\Windows",
            }
        )
        assert "_MEIPASS2" not in sanitized
        assert "_PYI_ARCHIVE_FILE" not in sanitized
        assert "QT_PLUGIN_PATH" not in sanitized
        assert sanitized["SYSTEMROOT"] == "C:\\Windows"

        fake_target = tmp_path / "TikTokStreakSetup.exe"
        fake_target.write_bytes(b"MZ_OLD")
        with patch("subprocess.Popen") as mock_popen:
            cmd_file = launch_windows_self_update(
                out,
                target_exe_path=fake_target,
                pid=99999,
                work_dir=tmp_path,
                relaunch=True,
                parent_pid=88888,
            )
        assert cmd_file.is_file()
        cmd_text = cmd_file.read_text(encoding="ascii")
        assert "99999" in cmd_text and "88888" in cmd_text
        assert ".bak" in cmd_text and "ping -n 2 127.0.0.1" in cmd_text
        assert ":retry_swap" in cmd_text and 'start "" /D ' in cmd_text
        assert "timeout /t" not in cmd_text
        if sys.platform == "win32":
            mock_popen.assert_called_once()
            popen_kw = mock_popen.call_args.kwargs
            flags = int(popen_kw.get("creationflags") or 0)
            assert (flags & 0x08000000) == 0x08000000  # CREATE_NO_WINDOW
            assert (flags & 0x00000008) == 0  # must NOT set DETACHED_PROCESS
            assert popen_kw.get("stdin") == subprocess.DEVNULL
            assert popen_kw.get("stdout") == subprocess.DEVNULL
            assert popen_kw.get("stderr") == subprocess.DEVNULL

    def test_check_interval_and_markdown_to_update_html(self):
        from gui.services.updater import CHECK_INTERVAL_SECONDS, markdown_to_update_html

        assert CHECK_INTERVAL_SECONDS == 60

        md = (
            "### What's New in v1.3.1 (Hotfix)\n\n"
            "#### Critical Fix — `scene=business`\n"
            "- **Fixed `RecipientMismatchError`:** Handles `<p data-e2e=\"chat-uniqueid\">@username</p>` safely.\n"
            "- See [Release Notes](https://github.com/alexanderfrit/tiktok-streak/releases) and "
            "[Bad Link](javascript:alert(1)).\n\n"
            "1. Step *one*\n"
            "2. Step two\n"
        )
        light_html = markdown_to_update_html(md, dark=False)
        dark_html = markdown_to_update_html(md, dark=True)

        assert "<h3" in light_html and "<h4" in light_html
        assert "<ul><li><b>Fixed " in light_html
        assert "<ol><li>Step <i>one</i></li><li>Step two</li></ol>" in light_html
        assert "&lt;p data-e2e=&quot;chat-uniqueid&quot;&gt;@username&lt;/p&gt;" in light_html
        assert '<p data-e2e=' not in light_html
        assert 'href="https://github.com/alexanderfrit/tiktok-streak/releases"' in light_html
        assert "javascript:alert" not in light_html
        assert "#1f1f1f" in light_html and "#f3f3f3" in dark_html


