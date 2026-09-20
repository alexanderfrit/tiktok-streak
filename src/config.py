from dataclasses import dataclass, field
import json, logging, os
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tiktok-streak")


@dataclass
class AccountConfig:
    name: str
    session_id: str
    friends: list = field(default_factory=list)
    message: str = "🔥 Daily Streak"
    video_url: str = ""
    share_times: int = 2
    photo_path: str = ""


def clean_handle(raw: str) -> str:
    return raw.strip().lstrip("@")


def load_friends_csv(filepath: str = "friends.csv") -> list:
    if not os.path.exists(filepath):
        return []
    friends = []
    with open(filepath, mode="r", encoding="utf-8") as file:
        for line in file:
            cleaned = clean_handle(line)
            if cleaned and cleaned.lower() != "username":
                if cleaned not in friends:
                    friends.append(cleaned)
    return friends


def load_accounts(config_file: str = "accounts.json") -> list:
    # 1. Multi-account local file
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = []
            for item in data:
                friends = [clean_handle(f) for f in item.get("friends", []) if clean_handle(f)]
                accounts.append(
                    AccountConfig(
                        name=item.get("name", "Account"),
                        session_id=item.get("session_id", "").strip(),
                        friends=friends,
                        message=item.get("message", "🔥 Daily Streak"),
                        video_url=(item.get("video_url") or os.getenv("STREAK_VIDEO_URL", "")).strip(),
                        share_times=int(item.get("share_times", os.getenv("SHARE_TIMES", "2")) or 2),
                        photo_path=(item.get("photo_path") or os.getenv("PHOTO_PATH", "")).strip(),
                    )
                )
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
            accounts = []
            for item in data:
                friends = [clean_handle(f) for f in item.get("friends", []) if clean_handle(f)]
                accounts.append(
                    AccountConfig(
                        name=item.get("name", "Account"),
                        session_id=item.get("session_id", "").strip(),
                        friends=friends,
                        message=item.get("message", "🔥 Daily Streak"),
                        video_url=(item.get("video_url") or os.getenv("STREAK_VIDEO_URL", "")).strip(),
                        share_times=int(item.get("share_times", os.getenv("SHARE_TIMES", "2")) or 2),
                        photo_path=(item.get("photo_path") or os.getenv("PHOTO_PATH", "")).strip(),
                    )
                )
            if accounts:
                logger.info("Loaded %d account(s) from TIKTOK_ACCOUNTS_JSON secret.", len(accounts))
                return accounts
        except Exception as e:
            logger.warning("Failed parsing TIKTOK_ACCOUNTS_JSON: %s", e)

    # 3. Single-account fallback from standard .env
    session_id = os.getenv("TIKTOK_SESSION_ID", "").strip()
    message = os.getenv("MESSAGE", "🔥 Daily Streak")

    friends = []
    env_friends = os.getenv("FRIENDS_LIST")
    if env_friends:
        friends = [clean_handle(f) for f in env_friends.split(",") if clean_handle(f)]
    else:
        friends = load_friends_csv("friends.csv")

    if session_id:
        return [
            AccountConfig(
                name="Main",
                session_id=session_id,
                friends=friends,
                message=message,
                video_url=os.getenv("STREAK_VIDEO_URL", "").strip(),
                share_times=int(os.getenv("SHARE_TIMES", "2") or 2),
                photo_path=os.getenv("PHOTO_PATH", "").strip(),
            )
        ]

    return []
