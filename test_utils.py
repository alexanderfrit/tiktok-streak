import os
from unittest.mock import MagicMock
from utils import login_tiktok

# 1. Test missing session ID raises ValueError
if "TIKTOK_SESSION_ID" in os.environ:
    del os.environ["TIKTOK_SESSION_ID"]

mock_browser = MagicMock()
mock_wait = MagicMock()

try:
    login_tiktok(mock_browser, mock_wait)
    assert False, "Should raise ValueError when TIKTOK_SESSION_ID is missing"
except ValueError:
    pass

# 2. Test valid session ID injects cookies
os.environ["TIKTOK_SESSION_ID"] = "mock_session_123"
mock_browser.current_url = "https://www.tiktok.com/messages?lang=vi"
added_cookies = []
mock_browser.add_cookie.side_effect = lambda c: added_cookies.append(c)

login_tiktok(mock_browser, mock_wait)

cookie_names = [c["name"] for c in added_cookies]
assert "sessionid" in cookie_names, "sessionid cookie missing"
assert "sessionid_ss" in cookie_names, "sessionid_ss cookie missing"
assert all(c["value"] == "mock_session_123" for c in added_cookies), "cookie value mismatch"
assert mock_browser.get.call_count >= 2, "browser did not navigate"

print("Self-check passed: session cookie injection verified.")
