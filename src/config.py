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
    image_path: str = "assets/streak.png"
    posts: list = field(default_factory=list)


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


def parse_post_urls(raw) -> list:
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    if isinstance(raw, str):
        return [u.strip() for u in raw.split(",") if u.strip()]
    return []


def load_accounts(config_file: str = "accounts.json") -> list:
    # 1. Multi-account local file
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = []
            for item in data:
                friends = [clean_handle(f) for f in item.get("friends", []) if clean_handle(f)]
                posts = parse_post_urls(item.get("posts", []))
                accounts.append(
                    AccountConfig(
                        name=item.get("name", "Account"),
                        session_id=item.get("session_id", "").strip(),
                        friends=friends,
                        message=item.get("message", "🔥 Daily Streak"),
                        image_path=item.get("image_path", "assets/streak.png"),
                        posts=posts,
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
                posts = parse_post_urls(item.get("posts", []))
                accounts.append(
                    AccountConfig(
                        name=item.get("name", "Account"),
                        session_id=item.get("session_id", "").strip(),
                        friends=friends,
                        message=item.get("message", "🔥 Daily Streak"),
                        image_path=item.get("image_path", "assets/streak.png"),
                        posts=posts,
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
    image_path = os.getenv("STREAK_IMAGE", "assets/streak.png")
    posts = parse_post_urls(os.getenv("STREAK_POSTS", ""))

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
                image_path=image_path,
                posts=posts,
            )
        ]

    return []
