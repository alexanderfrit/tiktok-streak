"""Deprecated backward-compatible shim for the old flat-module layout.

Not used by main.py or the test suite. Prefer importing from src.* directly.
`auto_send_message` here still reads os.getenv("MESSAGE") and diverges from the
account-aware flow in main.py - do not wire it into the live path.
"""
import os
from src.browser import init_browser, authenticate_session, dump_debug_diagnostics
from src.config import load_friends_csv, load_accounts, AccountConfig
from src.actions import (
    find_elements_by_candidates,
    find_element_by_candidates,
    type_human_like,
    send_streak_message,
    CHAT_ITEM_CANDIDATES,
    MESSAGE_INPUT_CANDIDATES,
)
from src.notifier import get_wib_timestamp, notify_telegram, notify_streak_summary, logger
from src.exceptions import (
    TikTokError,
    SessionExpiredError,
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
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
    sent = 0
    failures = []
    for f in friends:
        res = send_streak_message(browser, f, msg)
        if res["sent"]:
            sent += 1
        else:
            failures.append(f"@{f}: {res['error']}")
    return sent, len(friends), failures
