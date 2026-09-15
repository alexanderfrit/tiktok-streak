import os, re, json, tempfile
from unittest.mock import MagicMock, patch
from selenium.webdriver.common.by import By

from src.exceptions import (
    TikTokError,
    SessionExpiredError,
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
    MediaUploadError,
    VideoShareError,
)
from src.notifier import get_wib_timestamp, notify_telegram, notify_streak_summary
from src.config import load_accounts, AccountConfig
from src.actions import (
    find_elements_by_candidates,
    type_human_like,
    execute_streak_bundle,
)

# 1. Test WIB timestamp format (YYYY-MM-DD HH:MM:SS WIB)
wib_ts = get_wib_timestamp()
assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WIB$", wib_ts), f"Invalid WIB format: {wib_ts}"

# 2. Test Exception classes have actionable tips
for cls in [SessionExpiredError, UserNotFoundError, DMBlockedError, RateLimitError, MediaUploadError, VideoShareError]:
    assert issubclass(cls, TikTokError)
    assert cls.action_tip, f"Missing action tip for {cls.__name__}"

# 3. Test Config Loader: Single-Account fallback
with patch.dict(os.environ, {
    "TIKTOK_SESSION_ID": "single_cookie",
    "MESSAGE": "Test single",
    "FRIENDS_LIST": "user1, @user2",
    "STREAK_POSTS": "http://p1, http://p2",
}, clear=True):
    accounts = load_accounts(config_file="non_existent.json")
    assert len(accounts) == 1
    assert accounts[0].session_id == "single_cookie"
    assert accounts[0].friends == ["user1", "user2"]
    assert accounts[0].posts == ["http://p1", "http://p2"]

# 4. Test Config Loader: Multi-Account from JSON
sample_multi = [
    {"name": "Acc1", "session_id": "c1", "friends": ["@f1"], "posts": ["http://a"]},
    {"name": "Acc2", "session_id": "c2", "friends": ["f2"], "posts": []},
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

# 6. Test Bundled Asset
assert os.path.exists("assets/streak.png"), "assets/streak.png is missing"
with open("assets/streak.png", "rb") as f:
    header = f.read(8)
    assert header == b"\x89PNG\r\n\x1a\n", "assets/streak.png invalid PNG header"

# 7. Test Human Typing
typed = []
mock_el = MagicMock()
mock_el.send_keys.side_effect = lambda k: typed.append(k)
with patch("time.sleep", return_value=None):
    type_human_like(mock_el, "hey")
assert typed == ["h", "e", "y"]

# 8. Test Telegram Notification payload construction
with patch("urllib.request.urlopen") as mock_urlopen, \
     patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:tk", "TELEGRAM_CHAT_ID": "456"}):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    assert notify_telegram("Title", "Details", "Action", is_error=True) is True
    assert notify_streak_summary([{"name": "Acc1", "friends_total": 1, "text_sent": 1, "photos_sent": 1, "posts_sent": 2, "failures": []}], 30) is True

# 9. Test Candidate Selector Fallback
mock_browser = MagicMock()
mock_target = MagicMock()
mock_target.is_displayed.return_value = True
mock_browser.find_elements.side_effect = [[], [mock_target]]
candidates = [(By.CSS_SELECTOR, "missing"), (By.CSS_SELECTOR, "found")]
res = find_elements_by_candidates(mock_browser, candidates, "test element", timeout=1.0)
assert res == [mock_target]

# 10. Test Streak Bundle Flow (Text + Photo + Posts)
mock_browser.get.reset_mock()
mock_browser.page_source = "<html>profile</html>"
with patch("src.actions.send_text_message") as mock_text, \
     patch("src.actions.send_media_photo", return_value=True) as mock_photo, \
     patch("src.actions.share_video_post", return_value=True) as mock_post, \
     patch("time.sleep", return_value=None):
    status = execute_streak_bundle(mock_browser, "friend1", "hello", "assets/streak.png", ["http://v1", "http://v2"])
    assert status["text"] is True
    assert status["photo"] is True
    assert status["posts"] == 2
    assert status["error"] is None

print("All 10 test suites in test_runner.py passed successfully!")
