from dataclasses import dataclass, field
import json, logging, os
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tiktok-streak")

# Shipped defaults, used when a per-account value and the matching env var are
# both unset. Set the env/secret to "", "none", "off", or "disabled" (or set
# DISABLE_VIDEO / DISABLE_PHOTO=true) to disable the feature.
DEFAULT_VIDEO_URL = "https://www.tiktok.com/@yutta/video/7684838545659317524"
DEFAULT_PHOTO_PATH = "assets/default_photo.png"

DISABLE_SENTINELS = ("none", "off", "disabled")
TRUTHY_VALUES = ("1", "true", "yes")


@dataclass
class AccountConfig:
    name: str
    session_id: str
    friends: list = field(default_factory=list)
    message: str = "🔥 Daily Streak"
    video_url: str = DEFAULT_VIDEO_URL
    share_times: int = 2
    photo_path: str = DEFAULT_PHOTO_PATH


def clean_handle(raw: str) -> str:
    return (raw or "").strip().lstrip("@").strip()


def _is_truthy(val: object) -> bool:
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() in TRUTHY_VALUES


def _is_disabled_sentinel(val: object) -> bool:
    return str(val or "").strip().lower() in DISABLE_SENTINELS


def _dedupe_friends(raw_friends) -> list:
    if isinstance(raw_friends, str):
        raw_friends = raw_friends.split(",")
    if not isinstance(raw_friends, (list, tuple)):
        return []
    seen = set()
    out = []
    for item in raw_friends:
        if item is None:
            continue
        cleaned = clean_handle(str(item))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def _resolve_asset(item: dict | None, key: str, env_key: str, default_val: str, disable_env_key: str) -> str:
    """Resolve an optional asset path/URL for a JSON account or env fallback.

    Explicit empty strings or disable sentinels ("none", "off", "disabled")
    disable the feature instead of falling back. Omitted or null keys fall back
    to the environment variable (honoring DISABLE_VIDEO / DISABLE_PHOTO) and
    finally to `default_val`.
    """
    if item is not None and key in item and item[key] is not None:
        if item[key] is False:
            return ""
        raw = str(item[key]).strip()
        if not raw or _is_disabled_sentinel(raw):
            return ""
        return raw

    if _is_truthy(os.getenv(disable_env_key)):
        return ""
    raw_env = os.getenv(env_key)
    if raw_env is None:
        return default_val
    stripped = raw_env.strip()
    if not stripped or _is_disabled_sentinel(stripped):
        return ""
    return stripped


def _resolve_share_times(item: dict | None) -> int:
    """Resolve share_times, preserving explicit integer 0 without `or 2` fallback."""
    if item is not None and "share_times" in item and item["share_times"] is not None:
        raw = item["share_times"]
        if isinstance(raw, bool):
            return 1 if raw else 0
        if isinstance(raw, int):
            return max(0, raw)
        s = str(raw).strip()
        if s:
            if _is_disabled_sentinel(s):
                return 0
            try:
                return max(0, int(s))
            except ValueError:
                logger.warning("Invalid share_times=%r; falling back to env/default.", raw)

    raw_env = os.getenv("SHARE_TIMES")
    if raw_env is None:
        return 2
    s = raw_env.strip()
    if not s:
        return 2
    if _is_disabled_sentinel(s):
        return 0
    try:
        return max(0, int(s))
    except ValueError:
        logger.warning("Invalid SHARE_TIMES=%r; defaulting to 2.", raw_env)
        return 2


def _parse_accounts_list(data, source_label: str) -> list:
    if not isinstance(data, list):
        raise ValueError(f"{source_label} must contain a JSON array.")
    accounts = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"Each entry in {source_label} must be a JSON object.")
        friends = _dedupe_friends(item.get("friends", []))
        raw_msg = item.get("message")
        if raw_msg is not None and str(raw_msg).strip():
            message = str(raw_msg)
        else:
            message = os.getenv("MESSAGE") or "🔥 Daily Streak"
        raw_name = item.get("name")
        name = str(raw_name).strip() if raw_name is not None else ""
        if not name:
            name = "Account"
        raw_sid = item.get("session_id")
        session_id = str(raw_sid).strip() if raw_sid is not None else ""
        accounts.append(
            AccountConfig(
                name=name,
                session_id=session_id,
                friends=friends,
                message=message,
                video_url=_resolve_asset(item, "video_url", "STREAK_VIDEO_URL", DEFAULT_VIDEO_URL, "DISABLE_VIDEO"),
                share_times=_resolve_share_times(item),
                photo_path=_resolve_asset(item, "photo_path", "PHOTO_PATH", DEFAULT_PHOTO_PATH, "DISABLE_PHOTO"),
            )
        )
    return accounts


def load_friends_csv(filepath: str = "friends.csv") -> list:
    if not os.path.exists(filepath):
        return []
    friends = []
    seen = set()
    with open(filepath, mode="r", encoding="utf-8-sig") as file:
        for line in file:
            cleaned = clean_handle(line)
            if cleaned and cleaned.lower() != "username":
                key = cleaned.lower()
                if key not in seen:
                    seen.add(key)
                    friends.append(cleaned)
    return friends


def load_accounts(config_file: str = "accounts.json") -> list:
    # 1. Multi-account local file
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            accounts = _parse_accounts_list(data, config_file)
            if accounts:
                logger.info("Loaded %d account(s) from %s.", len(accounts), config_file)
                return accounts
        except Exception as e:
            logger.warning("Failed parsing %s: %s. Falling back to environment variables.", config_file, e)

    # 2. Multi-account via GitHub Secret TIKTOK_ACCOUNTS_JSON
    env_json = os.getenv("TIKTOK_ACCOUNTS_JSON")
    if env_json:
        try:
            data = json.loads(env_json)
            accounts = _parse_accounts_list(data, "TIKTOK_ACCOUNTS_JSON")
            if accounts:
                logger.info("Loaded %d account(s) from TIKTOK_ACCOUNTS_JSON secret.", len(accounts))
                return accounts
        except Exception as e:
            logger.warning("Failed parsing TIKTOK_ACCOUNTS_JSON: %s", e)

    # 3. Single-account fallback from standard .env
    session_id = os.getenv("TIKTOK_SESSION_ID", "").strip()
    message = os.getenv("MESSAGE") or "🔥 Daily Streak"

    env_friends = os.getenv("FRIENDS_LIST")
    if env_friends:
        friends = _dedupe_friends(env_friends)
    else:
        friends = load_friends_csv("friends.csv")

    if session_id:
        return [
            AccountConfig(
                name="Main",
                session_id=session_id,
                friends=friends,
                message=message,
                video_url=_resolve_asset(None, "video_url", "STREAK_VIDEO_URL", DEFAULT_VIDEO_URL, "DISABLE_VIDEO"),
                share_times=_resolve_share_times(None),
                photo_path=_resolve_asset(None, "photo_path", "PHOTO_PATH", DEFAULT_PHOTO_PATH, "DISABLE_PHOTO"),
            )
        ]

    return []
