"""Backward-compatible shim pointing to modular src/ package."""
import os
from src.browser import init_browser, authenticate_session, dump_debug_diagnostics
from src.config import load_friends_csv, load_accounts, AccountConfig
from src.actions import (
    find_elements_by_candidates,
    find_element_by_candidates,
    type_human_like,
    send_text_message,
    send_media_photo,
    share_video_post,
    execute_streak_bundle,
    CHAT_ITEM_CANDIDATES,
    PROFILE_MESSAGE_BUTTON_CANDIDATES,
    MESSAGE_INPUT_CANDIDATES,
)
from src.notifier import get_wib_timestamp, notify_telegram, notify_streak_summary, logger
from src.exceptions import (
    TikTokError,
    SessionExpiredError,
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
    MediaUploadError,
    VideoShareError,
)


def login_tiktok(browser, wait=None, username=None, password=None):
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        raise SessionExpiredError("TIKTOK_SESSION_ID not found in .env")
    authenticate_session(browser, session_id, "Main")


def load_friends(filepath="friends.csv"):
    env_friends = os.getenv("FRIENDS_LIST")
    if env_friends:
        return {f.strip().lstrip("@") for f in env_friends.split(",") if f.strip().lstrip("@")}
    return set(load_friends_csv(filepath))


def auto_send_message(browser, wait=None):
    friends = sorted(load_friends())
    msg = os.getenv("MESSAGE", "🔥 Daily Streak")
    img = os.getenv("STREAK_IMAGE", "assets/streak.png")
    posts_str = os.getenv("STREAK_POSTS", "")
    posts = [p.strip() for p in posts_str.split(",") if p.strip()]

    sent = 0
    failures = []
    for f in friends:
        res = execute_streak_bundle(browser, f, msg, img, posts)
        if res["text"]:
            sent += 1
        if res["error"]:
            failures.append(f"@{f}: {res['error']}")
    return sent, len(friends), failures
