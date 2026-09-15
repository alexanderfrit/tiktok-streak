import os, re
from unittest.mock import MagicMock, patch
from selenium.webdriver.common.by import By
from utils import (
    login_tiktok,
    load_friends,
    find_elements_by_candidates,
    auto_send_message,
    get_wib_timestamp,
    notify_telegram,
    type_human_like,
    SessionExpiredError,
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
)

# 1. Test WIB timestamp format (YYYY-MM-DD HH:MM:SS WIB)
wib_ts = get_wib_timestamp()
assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WIB$", wib_ts), f"Invalid WIB format: {wib_ts}"

# 2. Test classified error types and action tips
assert SessionExpiredError.action_tip != ""
assert UserNotFoundError.action_tip != ""
assert DMBlockedError.action_tip != ""
assert RateLimitError.action_tip != ""

# 3. Test missing session ID raises SessionExpiredError
if "TIKTOK_SESSION_ID" in os.environ:
    del os.environ["TIKTOK_SESSION_ID"]

mock_browser = MagicMock()
mock_wait = MagicMock()

try:
    login_tiktok(mock_browser, mock_wait)
    assert False, "Should raise SessionExpiredError when TIKTOK_SESSION_ID is missing"
except SessionExpiredError:
    pass

# 4. Test valid session ID injects cookies
os.environ["TIKTOK_SESSION_ID"] = "mock_session_123"
mock_browser.current_url = "https://www.tiktok.com/messages?lang=vi"
added_cookies = []
mock_browser.add_cookie.side_effect = lambda c: added_cookies.append(c)

login_tiktok(mock_browser, mock_wait)

cookie_names = [c["name"] for c in added_cookies]
assert "sessionid" in cookie_names, "sessionid cookie missing"
assert "sessionid_ss" in cookie_names, "sessionid_ss cookie missing"
assert all(c["value"] == "mock_session_123" for c in added_cookies), "cookie value mismatch"

# 5. Test load_friends with FRIENDS_LIST env var fallback
with patch.dict(os.environ, {"FRIENDS_LIST": "user_a, @user_b"}):
    env_friends = load_friends()
    assert env_friends == {"user_a", "user_b"}, "FRIENDS_LIST parsing failed"

# 6. Test candidate fallback
mock_elem = MagicMock()
mock_elem.is_displayed.return_value = True
mock_browser.find_elements.side_effect = [[], [mock_elem]]
candidates = [(By.CSS_SELECTOR, "missing"), (By.CSS_SELECTOR, "found")]
res = find_elements_by_candidates(mock_browser, candidates, "test element", timeout=2)
assert res == [mock_elem], "candidate resolver failed to find fallback element"

# 7. Test human-like typing
typed_keys = []
mock_input = MagicMock()
mock_input.send_keys.side_effect = lambda k: typed_keys.append(k)
with patch("time.sleep", return_value=None):
    type_human_like(mock_input, "hi")
assert typed_keys == ["h", "i"], "human typing did not send character-by-character"

# 8. Test notify_telegram sends request with correct payload
with patch("urllib.request.urlopen") as mock_urlopen, \
     patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "999"}):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    success = notify_telegram("Test Title", "Test Details", action_tip="Do something", is_error=True)
    assert success is True, "notify_telegram failed"
    req_sent = mock_urlopen.call_args[0][0]
    payload = req_sent.data.decode("utf-8")
    assert "999" in payload, "chat_id missing from payload"
    assert "Test+Title" in payload or "Test Title" in payload, "title missing from payload"
    assert "WIB" in payload, "WIB timestamp missing from payload"

# 9. Test auto_send_message navigates directly to user profile
with patch("utils.load_friends", return_value={"testuser"}), \
     patch("utils.find_element_by_candidates") as mock_find, \
     patch("time.sleep", return_value=None):
    mock_find.return_value = MagicMock()
    mock_browser.get.reset_mock()
    mock_browser.page_source = "<html>profile ok</html>"
    sent_count, total_friends, failures = auto_send_message(mock_browser, mock_wait)
    assert sent_count == 1, "auto_send_message should have sent 1 message"
    mock_browser.get.assert_called_with("https://www.tiktok.com/@testuser")

print("Self-check passed: WIB time, errors, Telegram alerts, human typing, and runner verified.")
