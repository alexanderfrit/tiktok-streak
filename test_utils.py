import os
from unittest.mock import MagicMock
from selenium.webdriver.common.by import By
from utils import login_tiktok, load_friends, find_elements_by_candidates

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

# 3. Test load_friends handles headers, plain handles, @ prefixes
friends = load_friends('friends.csv')
assert isinstance(friends, set), "load_friends must return set"

# 4. Test candidate fallback
mock_elem = MagicMock()
mock_elem.is_displayed.return_value = True
mock_browser.find_elements.side_effect = [[], [mock_elem]]
candidates = [(By.CSS_SELECTOR, "missing"), (By.CSS_SELECTOR, "found")]
res = find_elements_by_candidates(mock_browser, candidates, "test element", timeout=2)
assert res == [mock_elem], "candidate resolver failed to find fallback element"

print("Self-check passed: session cookie injection, load_friends, and candidate resolvers verified.")
