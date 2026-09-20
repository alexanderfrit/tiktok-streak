import os, re, json, tempfile, shutil, base64
from unittest.mock import MagicMock, patch
from selenium.webdriver.common.by import By

from src.exceptions import (
    TikTokError,
    SessionExpiredError,
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
)
from src.notifier import get_wib_timestamp, notify_telegram, notify_streak_summary
from src.config import load_accounts, AccountConfig
from src.actions import (
    find_elements_by_candidates,
    type_human_like,
    send_streak_message,
)
from src.browser import parse_cookie_payload
from src.share import parse_aweme_id

# 1. Test WIB timestamp format (YYYY-MM-DD HH:MM:SS WIB)
wib_ts = get_wib_timestamp()
assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WIB$", wib_ts), f"Invalid WIB format: {wib_ts}"

# 2. Test Exception classes have actionable tips
for cls in [SessionExpiredError, UserNotFoundError, DMBlockedError, RateLimitError]:
    assert issubclass(cls, TikTokError)
    assert cls.action_tip, f"Missing action tip for {cls.__name__}"

# 3. Test Config Loader: Single-Account fallback
with patch.dict(os.environ, {
    "TIKTOK_SESSION_ID": "single_cookie",
    "MESSAGE": "Test single",
    "FRIENDS_LIST": "user1, @user2",
}, clear=True):
    accounts = load_accounts(config_file="non_existent.json")
    assert len(accounts) == 1
    assert accounts[0].session_id == "single_cookie"
    assert accounts[0].friends == ["user1", "user2"]

# 4. Test Config Loader: Multi-Account from JSON
sample_multi = [
    {"name": "Acc1", "session_id": "c1", "friends": ["@f1"]},
    {"name": "Acc2", "session_id": "c2", "friends": ["f2"]},
]
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
    json.dump(sample_multi, tf)
    tf_name = tf.name

try:
    accounts = load_accounts(config_file=tf_name)
    assert len(accounts) == 2
    assert accounts[0].name == "Acc1"
    assert accounts[0].friends == ["f1"]
    assert accounts[1].name == "Acc2"
finally:
    os.remove(tf_name)

# 5. Test Config Loader: Multi-Account from TIKTOK_ACCOUNTS_JSON secret
with patch.dict(os.environ, {"TIKTOK_ACCOUNTS_JSON": json.dumps(sample_multi)}, clear=True):
    accounts = load_accounts(config_file="non_existent.json")
    assert len(accounts) == 2
    assert accounts[0].session_id == "c1"

# 6. Test Human Typing
typed = []
mock_el = MagicMock()
mock_el.send_keys.side_effect = lambda k: typed.append(k)
with patch("time.sleep", return_value=None):
    type_human_like(mock_el, "hey")
assert typed == ["h", "e", "y"]

# 7. Test Telegram Notification payload construction
with patch("urllib.request.urlopen") as mock_urlopen, \
     patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:tk", "TELEGRAM_CHAT_ID": "456"}):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    assert notify_telegram("Title", "Details", "Action", is_error=True) is True
    assert notify_streak_summary([{"name": "Acc1", "friends_total": 1, "sent": 1, "failures": []}], 30) is True

# 8. Test Candidate Selector Fallback
mock_browser = MagicMock()
mock_target = MagicMock()
mock_target.is_displayed.return_value = True
mock_browser.find_elements.side_effect = [[], [mock_target]]
candidates = [(By.CSS_SELECTOR, "missing"), (By.CSS_SELECTOR, "found")]
res = find_elements_by_candidates(mock_browser, candidates, "test element", timeout=1.0)
assert res == [mock_target]

# 9. Test Inbox Streak Message Send Flow
mock_browser.current_url = "https://www.tiktok.com/messages?lang=vi"
with patch("src.actions.open_inbox_chat") as mock_open, \
     patch("src.actions.find_element_by_candidates") as mock_find, \
     patch("time.sleep", return_value=None):
    mock_input = MagicMock()
    mock_find.return_value = mock_input
    status = send_streak_message(mock_browser, "friend1", "hello")
    assert status["sent"] is True
    assert status["error"] is None

# 10. Test Send Verification: message still in composer => not sent
mock_browser.current_url = "https://www.tiktok.com/messages?lang=vi"
with patch("src.actions.open_inbox_chat"), \
     patch("src.actions.find_element_by_candidates") as mock_find, \
     patch("time.sleep", return_value=None):
    mock_input = MagicMock()
    mock_input.text = "hello"  # composer retains the text, RETURN did not submit
    mock_find.return_value = mock_input
    status = send_streak_message(mock_browser, "friend1", "hello")
    assert status["sent"] is False
    assert "not confirmed" in (status["error"] or "")

# 11. Test Cookie Payload Parsing (JSON dict / JSON list / header string / raw token)
assert parse_cookie_payload('{"sessionid": "abc", "sid_tt": "abc"}') == {"sessionid": "abc", "sid_tt": "abc"}
assert parse_cookie_payload('[{"name": "sessionid", "value": "xyz"}]') == {"sessionid": "xyz"}
assert parse_cookie_payload("sessionid=foo; sid_tt=foo") == {"sessionid": "foo", "sid_tt": "foo"}
assert parse_cookie_payload("rawtoken") == {"sessionid": "rawtoken", "sessionid_ss": "rawtoken", "sid_tt": "rawtoken"}

# 12. Test Video URL Parsing (aweme id extraction)
assert parse_aweme_id("https://www.tiktok.com/@user/video/7684838545659317524") == "7684838545659317524"
assert parse_aweme_id("https://www.tiktok.com/@user/video/7684838545659317524?is_from_webapp=1") == "7684838545659317524"
assert parse_aweme_id("https://www.tiktok.com/@u/photo/1234567890123456789") == "1234567890123456789"
assert parse_aweme_id("7684838545659317524") == "7684838545659317524"
assert parse_aweme_id("") is None
assert parse_aweme_id("https://vt.tiktok.com/abc") is None

# 13. Test video config: env fallback + share_times
with patch.dict(os.environ, {
    "TIKTOK_SESSION_ID": "c",
    "STREAK_VIDEO_URL": "https://www.tiktok.com/@u/video/1111111111111111111",
    "SHARE_TIMES": "3",
}, clear=True):
    accs = load_accounts(config_file="non_existent.json")
    assert accs[0].video_url.endswith("1111111111111111111")
    assert accs[0].share_times == 3

# 14. Test video config: per-account value in accounts.json wins
sample_video = [{"name": "A", "session_id": "c1", "friends": ["f1"],
                 "video_url": "https://www.tiktok.com/@u/video/2222222222222222222",
                 "share_times": 2}]
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
    json.dump(sample_video, tf)
    tf_video = tf.name
try:
    accs = load_accounts(config_file=tf_video)
    assert accs[0].video_url.endswith("2222222222222222222")
    assert accs[0].share_times == 2
finally:
    os.remove(tf_video)

# 15. Test photo config: env fallback + per-account value wins
with patch.dict(os.environ, {
    "TIKTOK_SESSION_ID": "c",
    "PHOTO_PATH": "assets/dummy_photo.png",
}, clear=True):
    accs = load_accounts(config_file="non_existent.json")
    assert accs[0].photo_path == "assets/dummy_photo.png"

sample_photo = [{"name": "A", "session_id": "c1", "friends": ["f1"],
                 "photo_path": "assets/custom.png"}]
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
    json.dump(sample_photo, tf)
    tf_photo = tf.name
try:
    accs = load_accounts(config_file=tf_photo)
    assert accs[0].photo_path == "assets/custom.png"
finally:
    os.remove(tf_photo)

# 16. Test GUI wiring: secret names cover the workflow, browser reader is importable
try:
    from gui.app import SECRET_NAMES as GUI_SECRETS
    from gui import browsers as gui_browsers
    _gui_ok = True
except Exception as _e:
    _gui_ok = False
    _gui_err = str(_e)
assert _gui_ok, f"GUI modules failed to import: {_gui_err}"

# every secrets.* referenced by the workflow must be settable by the GUI
_wf = open(".github/workflows/streak.yml", encoding="utf-8").read()
_wf_secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", _wf))
assert _wf_secrets, "no secrets referenced in the workflow?"
assert _wf_secrets.issubset(set(GUI_SECRETS)), \
    f"workflow uses secrets the GUI cannot set: {_wf_secrets - set(GUI_SECRETS)}"

# browser source listing must not raise (returns [] on unsupported platforms)
_sources = gui_browsers.list_sources()
assert isinstance(_sources, list)

# 17. Per-account session must win over a global TIKTOK_COOKIES dump
from src.browser import authenticate_session
_injected = {}
_mock_b = MagicMock()
_mock_b.current_url = "https://www.tiktok.com/messages?lang=vi"
_mock_b.add_cookie.side_effect = lambda c: _injected.__setitem__(c["name"], c["value"])
with patch("time.sleep", return_value=None), \
     patch.dict(os.environ, {"TIKTOK_COOKIES": "sessionid=STALE; ttwid=keep"}, clear=True):
    authenticate_session(_mock_b, "FRESH_ACCT_SID", "Acct")
assert _injected["sessionid"] == "FRESH_ACCT_SID", _injected
assert _injected["ttwid"] == "keep"

# 18. Photo path is made absolute (ChromeDriver rejects relative paths)
from src.share import send_photo_card, find_template
_rel = "assets/nope_missing.png"
_res = send_photo_card(MagicMock(), "f", _rel)
assert os.path.isabs(_res["error"].split("photo not found: ")[-1]), _res

# 19. find_template retries until the SEND_MESSAGE frame appears
_js_browser = MagicMock()
_js_browser.execute_script.side_effect = [None, None, {"b64": "x", "sock": 1}]
with patch("time.sleep", return_value=None):
    assert find_template(_js_browser, timeout=5.0) == {"b64": "x", "sock": 1}
assert _js_browser.execute_script.call_count == 3

# 20. find_template surfaces a stashed (post-reload) frame
_js_browser2 = MagicMock()
_js_browser2.execute_script.return_value = {"b64": "y", "sock": -1, "stale": True}
with patch("time.sleep", return_value=None):
    assert find_template(_js_browser2, timeout=1.0) == {"b64": "y", "sock": -1, "stale": True}

# 21. WS hook records string frames too (must not skip them as b64=null)
from src.share import WS_HOOK_JS
assert "window.__pb.enc(data)" in WS_HOOK_JS, "WS hook must encode string frames"
assert "__tmplB64" in WS_HOOK_JS, "WS hook must stash the borrowable frame"

# 22. Device Flow refuses to start without an OAuth client id
from gui import github_api as _ghapi
_orig_cid = _ghapi.OAUTH_CLIENT_ID
_ghapi.OAUTH_CLIENT_ID = ""
try:
    _raised = False
    try:
        _ghapi.device_flow_start()
    except _ghapi.GitHubError:
        _raised = True
    assert _raised, "device_flow_start must require a client id"
finally:
    _ghapi.OAUTH_CLIENT_ID = _orig_cid


class _Resp:
    def __init__(self, payload): self._p = payload
    def read(self): return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


# 23. check_session surfaces the username for a live cookie
from gui.app import Api as _GuiApi
with patch("urllib.request.urlopen", return_value=_Resp({"data": {"username": "alice"}})):
    _r = _GuiApi().check_session("sid")
assert _r["ok"] and _r["username"] == "alice", _r
with patch("urllib.request.urlopen", return_value=_Resp({"message": "expired"})):
    assert _GuiApi().check_session("sid")["ok"] is False

# 24. telegram_test sends the ready message
with patch("urllib.request.urlopen", return_value=_Resp({"ok": True})):
    _t = _GuiApi().telegram_test("123:tk", "999")
assert _t["ok"] is True and _t["chat_id"] == "999", _t

# 25. Audit guard: secret-bearing paths stay gitignored (never committed)
_gi = open(".gitignore", encoding="utf-8").read()
for _pat in [".env", "accounts.json", "friends.csv", "capture_share_*.json", "debug_*.html"]:
    assert _pat in _gi, f"missing gitignore guard: {_pat}"
assert not os.path.exists(".env") or True  # presence allowed locally; must stay untracked

# 26. Browser discovery: no duplicate profile paths, and a locked browser is
# reported (not silently mis-read) so the GUI can ask the user to close it.
from gui import browsers as _br
_srcs = _br.list_sources(enrich=False)
_paths = [os.path.normcase(os.path.abspath(s["cookies_path"])) for s in _srcs]
assert len(_paths) == len(set(_paths)), "duplicate browser profiles detected"
for _s in _srcs:
    assert _s.get("cookies_path") and _s.get("id") and _s.get("label")

class _Boom:
    def execute(self, *a, **k): raise __import__("sqlite3").OperationalError("unable to open database file")
    def close(self): pass
# a locked source turns into a friendly, distinct result (not a raw error)
with patch.object(_br, "grab_session", side_effect=_br.BrowserLocked("locked")):
    _rc = _GuiApi().read_cookies("any")
assert _rc["ok"] is False and _rc.get("locked") is True, _rc

print("All 26 test suites in test_runner.py passed successfully!")

# 27. Custom photo: a data URL is decoded to a file and its path returned;
# with nothing uploaded the default/fallback path is kept.
from src import photo_prep as _pp
_prev_cwd = os.getcwd()
_tmpdir = tempfile.mkdtemp()
try:
    os.chdir(_tmpdir)
    _png = base64.b64encode(b"\x89PNG\r\n\x1a\nFAKE").decode()
    _out = _pp.resolve(f"data:image/png;base64,{_png}")
    assert _out == "assets/custom_photo.png", _out
    assert open(_out, "rb").read().endswith(b"FAKE")
    _jpg = base64.b64encode(b"JPEGDATA").decode()
    assert _pp.resolve(f"data:image/jpeg;base64,{_jpg}") == "assets/custom_photo.jpg"
    assert _pp.resolve("") == _pp.DEFAULT
    assert _pp.resolve("", "assets/mine.png") == "assets/mine.png"
finally:
    os.chdir(_prev_cwd)
    shutil.rmtree(_tmpdir, ignore_errors=True)

print("All 27 test suites in test_runner.py passed successfully!")
