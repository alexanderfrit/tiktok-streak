import os, re, json, tempfile
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

print("All 17 test suites in test_runner.py passed successfully!")
