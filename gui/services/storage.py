"""Persistent desktop configuration, encrypted secret storage, workspace I/O, and secret redaction.

Zero Qt dependencies. Secrets are stored in OS keyring with a Windows DPAPI
encrypted file fallback (never plaintext).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from gui.services.validators import (
    _parse_hhmm,
    crons_to_local_times,
    detect_local_timezone,
    export_friends_csv,
    get_timezone_info,
    import_friends_csv,
    normalize_friends,
)

logger = logging.getLogger("tiktok-streak")

APP_DIR_NAME = "TikTokStreak"
KEYRING_SERVICE = "tiktok-streak-gui"
DEFAULT_VIDEO_URL = "https://www.tiktok.com/@yutta/video/7684838545659317524"
DEFAULT_PHOTO_PATH = "assets/default_photo.png"
DEFAULT_SOURCE_REPO = "alexanderfrit/tiktok-streak"

_DISABLE_SENTINELS = {"none", "off", "disabled"}


def default_config_dir() -> Path:
    """Return the platform-appropriate configuration directory."""
    appdata = os.environ.get("APPDATA", "").strip()
    if sys.platform == "win32" and appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".tiktok-streak-gui"


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
    return path


def _atomic_write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
    return path


def _is_truthy(val: Any) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _is_disabled_sentinel(val: Any) -> bool:
    return str(val or "").strip().lower() in _DISABLE_SENTINELS


@dataclass
class AccountProfile:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "Main"
    friends: list[str] = field(default_factory=list)
    message_override: str | None = None
    video_url_override: str | None = None
    share_times_override: int | None = None
    photo_path_override: str | None = None
    username: str = ""
    inbox_verified: bool = False
    auth_method: str = ""
    last_checked: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountProfile":
        if not isinstance(data, dict):
            return cls()
        raw_friends = data.get("friends") or []
        if isinstance(raw_friends, str):
            friends, _ = normalize_friends(raw_friends)
        elif isinstance(raw_friends, list):
            friends, _ = normalize_friends("\n".join(str(x) for x in raw_friends))
        else:
            friends = []

        share_raw = data.get("share_times_override")
        share_override: int | None
        if share_raw is None or share_raw == "":
            share_override = None
        else:
            try:
                share_override = max(0, int(share_raw))
            except (TypeError, ValueError):
                share_override = None

        def _opt_str(key: str) -> str | None:
            v = data.get(key)
            if v is None:
                return None
            return str(v)

        return cls(
            id=str(data.get("id") or "").strip() or uuid.uuid4().hex[:12],
            name=str(data.get("name") or "Main").strip() or "Main",
            friends=friends,
            message_override=_opt_str("message_override"),
            video_url_override=_opt_str("video_url_override"),
            share_times_override=share_override,
            photo_path_override=_opt_str("photo_path_override"),
            username=str(data.get("username") or "").strip(),
            inbox_verified=bool(data.get("inbox_verified", False)),
            auth_method=str(data.get("auth_method") or "").strip(),
            last_checked=str(data.get("last_checked") or "").strip(),
        )


@dataclass
class AppState:
    language: str = "en"
    theme: str = "system"
    accent_mode: str = "system"
    mode: str = "cloud"
    multi_account: bool = False
    accounts: list[AccountProfile] = field(default_factory=list)
    message: str = "🔥 Daily Streak"
    video_enabled: bool = True
    video_url: str = DEFAULT_VIDEO_URL
    share_times: int = 2
    photo_enabled: bool = True
    photo_path: str = ""
    daily_cap: int = 0
    headless_local: bool = True
    timezone: str = field(default_factory=detect_local_timezone)
    local_times: list[str] = field(default_factory=lambda: ["08:00"])
    cloud_times: list[str] = field(default_factory=lambda: ["08:00"])
    cloud_cron: str = "0 1 * * *"
    github_login: str = ""
    github_repo_name: str = "tiktok-streak"
    github_private: bool = True
    github_source_repo: str = DEFAULT_SOURCE_REPO
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
    workspace_dir: str = ""
    last_test_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["accounts"] = [a.to_dict() for a in self.accounts]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        if not isinstance(data, dict):
            return cls()
        raw_accounts = data.get("accounts") or []
        accounts = [
            AccountProfile.from_dict(item)
            for item in raw_accounts
            if isinstance(item, dict)
        ]

        raw_times = data.get("local_times")
        if isinstance(raw_times, list):
            local_times = [str(t).strip() for t in raw_times if str(t).strip()] or ["08:00"]
        elif isinstance(raw_times, str) and raw_times.strip():
            local_times = [t.strip() for t in raw_times.split(",") if t.strip()] or ["08:00"]
        else:
            local_times = ["08:00"]

        raw_tz = str(data.get("timezone") or "").strip()
        resolved_tz = get_timezone_info(raw_tz or detect_local_timezone())["id"]

        cloud_cron = str(data.get("cloud_cron") or "0 1 * * *").strip() or "0 1 * * *"

        raw_cloud_times = data.get("cloud_times")
        if isinstance(raw_cloud_times, list):
            cloud_candidates = [str(t).strip() for t in raw_cloud_times if str(t).strip()]
        elif isinstance(raw_cloud_times, str) and raw_cloud_times.strip():
            cloud_candidates = [t.strip() for t in raw_cloud_times.split(",") if t.strip()]
        else:
            cloud_candidates = []

        cloud_times: list[str] = []
        seen_cloud: set[str] = set()
        for item in cloud_candidates:
            try:
                hh, mm = _parse_hhmm(item)
            except ValueError:
                continue
            norm = f"{hh:02d}:{mm:02d}"
            if norm not in seen_cloud:
                seen_cloud.add(norm)
                cloud_times.append(norm)
        if not cloud_times:
            cloud_times = crons_to_local_times(cloud_cron, tz_id=resolved_tz) or ["08:00"]

        try:
            share_times = max(0, int(data.get("share_times", 2)))
        except (TypeError, ValueError):
            share_times = 2

        try:
            daily_cap = max(0, int(data.get("daily_cap", 0)))
        except (TypeError, ValueError):
            daily_cap = 0

        lang = str(data.get("language") or "en").strip().lower()
        if lang not in ("en", "id"):
            lang = "en"

        theme = str(data.get("theme") or "system").strip().lower()
        if theme not in ("system", "light", "dark", "auto"):
            theme = "system"

        accent_mode = str(data.get("accent_mode") or "system").strip().lower()
        if accent_mode not in ("system", "tiktok", "custom"):
            accent_mode = "system"

        mode = str(data.get("mode") or "cloud").strip().lower()
        if mode not in ("cloud", "local", "both"):
            mode = "cloud"

        return cls(
            language=lang,
            theme=theme,
            accent_mode=accent_mode,
            mode=mode,
            multi_account=bool(data.get("multi_account", False)),
            accounts=accounts,
            message=str(data.get("message") if data.get("message") is not None else "🔥 Daily Streak"),
            video_enabled=bool(data.get("video_enabled", True)),
            video_url=str(data.get("video_url") if data.get("video_url") is not None else DEFAULT_VIDEO_URL),
            share_times=share_times,
            photo_enabled=bool(data.get("photo_enabled", True)),
            photo_path=str(data.get("photo_path") or ""),
            daily_cap=daily_cap,
            headless_local=bool(data.get("headless_local", True)),
            timezone=resolved_tz,
            local_times=local_times,
            cloud_times=cloud_times,
            cloud_cron=cloud_cron,
            github_login=str(data.get("github_login") or "").strip(),
            github_repo_name=str(data.get("github_repo_name") or "tiktok-streak").strip() or "tiktok-streak",
            github_private=bool(data.get("github_private", True)),
            github_source_repo=str(data.get("github_source_repo") or DEFAULT_SOURCE_REPO).strip() or DEFAULT_SOURCE_REPO,
            telegram_chat_id=str(data.get("telegram_chat_id") or "").strip(),
            telegram_enabled=bool(data.get("telegram_enabled", False)),
            workspace_dir=str(data.get("workspace_dir") or "").strip(),
            last_test_ok=bool(data.get("last_test_ok", False)),
        )


class ConfigStore:
    """Atomic JSON configuration persistence for non-secret AppState."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
        self.config_path = self.config_dir / "config.json"

    def exists(self) -> bool:
        return self.config_path.is_file()

    def load(self) -> AppState:
        if not self.config_path.is_file():
            return AppState()
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return AppState.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load config %s (%s); returning defaults.", self.config_path, exc)
            return AppState()

    def save(self, state: AppState) -> Path:
        payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False)
        return _atomic_write_text(self.config_path, payload, encoding="utf-8")

    def reset(self) -> None:
        try:
            self.config_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed deleting config %s: %s", self.config_path, exc)


class SecretStore:
    """Secure secret storage using OS keyring with Windows DPAPI encrypted fallback.

    Plaintext storage is never used. If keyring fails (for example, Windows
    Credential Manager's 2560-byte limit for large TikTok cookie dumps), the
    secret is stored inside a DPAPI-protected file (`secrets.dpapi`).
    """

    KEY_GITHUB_TOKEN = "github_token"
    KEY_TELEGRAM_TOKEN = "telegram_token"
    ACCOUNT_PREFIX = "account:"

    def __init__(self, config_dir: str | Path | None = None, service_name: str = KEYRING_SERVICE) -> None:
        self.config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
        self.service_name = service_name
        self._dpapi_path = self.config_dir / "secrets.dpapi"
        self._index_path = self.config_dir / "secrets_index.json"

    @classmethod
    def account_key(cls, account_id: str) -> str:
        return f"{cls.ACCOUNT_PREFIX}{account_id}"

    def _load_tracked_keys(self) -> set[str]:
        if not self._index_path.is_file():
            return set()
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(k) for k in data if k}
        except Exception:
            pass
        return set()

    def _save_tracked_keys(self, keys: set[str]) -> None:
        if not keys:
            try:
                self._index_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        _atomic_write_text(self._index_path, json.dumps(sorted(keys), indent=2))

    def _track_key(self, key: str) -> None:
        keys = self._load_tracked_keys()
        if key not in keys:
            keys.add(key)
            self._save_tracked_keys(keys)

    def _untrack_key(self, key: str) -> None:
        keys = self._load_tracked_keys()
        if key in keys:
            keys.discard(key)
            self._save_tracked_keys(keys)

    @staticmethod
    def _import_win32crypt():
        try:
            import win32crypt
            return win32crypt
        except Exception:
            return None

    def _dpapi_read_all(self) -> dict[str, str]:
        if not self._dpapi_path.is_file():
            return {}
        win32crypt = self._import_win32crypt()
        if win32crypt is None:
            return {}
        raw = self._dpapi_path.read_bytes()
        if not raw:
            return {}
        _desc, plain_bytes = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
        parsed = json.loads(plain_bytes.decode("utf-8"))
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v is not None}
        return {}

    def _dpapi_write_all(self, data: dict[str, str]) -> None:
        if not data:
            try:
                self._dpapi_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        win32crypt = self._import_win32crypt()
        if win32crypt is None:
            raise RuntimeError(
                "Secure storage unavailable: keyring failed and Windows DPAPI (win32crypt) is not available."
            )
        plain = json.dumps(data, ensure_ascii=False).encode("utf-8")
        protected = win32crypt.CryptProtectData(plain, "TikTokStreakSecrets", None, None, None, 0)
        _atomic_write_bytes(self._dpapi_path, protected)

    def _keyring_get(self, key: str) -> str | None:
        try:
            import keyring
            val = keyring.get_password(self.service_name, key)
            return val if val else None
        except Exception:
            return None

    def _keyring_set(self, key: str, value: str) -> bool:
        try:
            import keyring
            keyring.set_password(self.service_name, key, value)
            return True
        except Exception as exc:
            logger.debug("keyring.set_password failed for %s (%s); trying DPAPI.", key, exc)
            return False

    def _keyring_delete(self, key: str) -> None:
        try:
            import keyring
            keyring.delete_password(self.service_name, key)
        except Exception:
            pass

    def get(self, key: str, default: str = "") -> str:
        val = self._keyring_get(key)
        if val is not None:
            return val
        try:
            dpapi_map = self._dpapi_read_all()
            if key in dpapi_map:
                return dpapi_map[key]
        except Exception as exc:
            logger.warning("DPAPI read failed for %s: %s", key, exc)
        return default

    def set(self, key: str, value: str) -> str:
        val = "" if value is None else str(value)
        if not val:
            self.delete(key)
            return "deleted"

        if self._keyring_set(key, val):
            # Remove any stale DPAPI copy for this key so reads stay consistent.
            try:
                dpapi_map = self._dpapi_read_all()
                if key in dpapi_map:
                    dpapi_map.pop(key, None)
                    self._dpapi_write_all(dpapi_map)
            except Exception:
                pass
            self._track_key(key)
            return "keyring"

        # Clear any stale shorter value in keyring before storing in DPAPI.
        self._keyring_delete(key)
        dpapi_map = self._dpapi_read_all()
        dpapi_map[key] = val
        self._dpapi_write_all(dpapi_map)
        self._track_key(key)
        return "dpapi"

    def delete(self, key: str) -> None:
        self._keyring_delete(key)
        try:
            dpapi_map = self._dpapi_read_all()
            if key in dpapi_map:
                dpapi_map.pop(key, None)
                self._dpapi_write_all(dpapi_map)
        except Exception:
            pass
        self._untrack_key(key)

    def get_github_token(self) -> str:
        return self.get(self.KEY_GITHUB_TOKEN, "")

    def set_github_token(self, token: str) -> str:
        return self.set(self.KEY_GITHUB_TOKEN, str(token or "").strip())

    def delete_github_token(self) -> None:
        self.delete(self.KEY_GITHUB_TOKEN)

    def get_telegram_token(self) -> str:
        return self.get(self.KEY_TELEGRAM_TOKEN, "")

    def set_telegram_token(self, token: str) -> str:
        return self.set(self.KEY_TELEGRAM_TOKEN, str(token or "").strip())

    def delete_telegram_token(self) -> None:
        self.delete(self.KEY_TELEGRAM_TOKEN)

    def get_account_cookie(self, account_id: str) -> str:
        return self.get(self.account_key(account_id), "")

    def set_account_cookie(self, account_id: str, payload: str) -> str:
        return self.set(self.account_key(account_id), str(payload or "").strip())

    def delete_account_cookie(self, account_id: str) -> None:
        self.delete(self.account_key(account_id))

    def clear_all(self, account_ids: Iterable[str] = ()) -> None:
        keys = {self.KEY_GITHUB_TOKEN, self.KEY_TELEGRAM_TOKEN}
        keys.update(self._load_tracked_keys())
        for aid in account_ids:
            if aid:
                keys.add(self.account_key(str(aid)))
        try:
            keys.update(self._dpapi_read_all().keys())
        except Exception:
            pass

        for key in keys:
            self._keyring_delete(key)

        for path in (self._dpapi_path, self._index_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _format_dotenv_line(key: str, value: Any) -> str:
    s = str(value if value is not None else "")
    s = s.replace("\r", "").replace("\n", "\\n")
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values
        raw_map = dotenv_values(str(path))
        return {str(k): ("" if v is None else str(v)) for k, v in raw_map.items() if k}
    except Exception:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (len(v) >= 2) and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
                v = v[1:-1]
            out[k] = v
        return out


def export_local_workspace(
    state: AppState,
    secret_store: SecretStore,
    target_dir: str | Path,
) -> dict[str, Any]:
    """Write ``.env``, ``accounts.json`` (multi-account), and ``friends.csv`` safely."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    written_files: list[str] = []

    tg_token = secret_store.get_telegram_token() if state.telegram_enabled else ""
    tg_chat = state.telegram_chat_id.strip() if state.telegram_enabled else ""

    eff_video_url = state.video_url.strip() if state.video_enabled else ""
    eff_share_times = max(0, int(state.share_times)) if state.video_enabled else 0
    eff_photo_path = (state.photo_path.strip() or DEFAULT_PHOTO_PATH) if state.photo_enabled else ""

    is_multi = bool(state.multi_account)
    primary = state.accounts[0] if state.accounts else AccountProfile()
    primary_cookie = secret_store.get_account_cookie(primary.id) if state.accounts else ""

    env_lines = [
        "# Generated by TikTok Streak Setup GUI",
        _format_dotenv_line("TIKTOK_SESSION_ID", "" if is_multi else primary_cookie),
        _format_dotenv_line("MESSAGE", state.message),
        _format_dotenv_line("FRIENDS_LIST", "" if is_multi else ",".join(primary.friends)),
        _format_dotenv_line("STREAK_VIDEO_URL", eff_video_url if state.video_enabled else "none"),
        _format_dotenv_line("SHARE_TIMES", str(eff_share_times)),
        _format_dotenv_line("PHOTO_PATH", eff_photo_path if state.photo_enabled else "none"),
        _format_dotenv_line("DISABLE_VIDEO", "false" if state.video_enabled else "true"),
        _format_dotenv_line("DISABLE_PHOTO", "false" if state.photo_enabled else "true"),
        _format_dotenv_line("DAILY_CAP", str(max(0, int(state.daily_cap)))),
        _format_dotenv_line("HEADLESS", "true" if state.headless_local else "false"),
        _format_dotenv_line("TELEGRAM_BOT_TOKEN", tg_token),
        _format_dotenv_line("TELEGRAM_CHAT_ID", tg_chat),
    ]
    env_path = _atomic_write_text(target / ".env", "\n".join(env_lines) + "\n")
    written_files.append(str(env_path))

    accounts_json_path = target / "accounts.json"
    if is_multi and state.accounts:
        payload = []
        for acc in state.accounts:
            acc_cookie = secret_store.get_account_cookie(acc.id)
            acc_msg = acc.message_override if acc.message_override is not None else state.message
            if acc.video_url_override is not None:
                acc_video = acc.video_url_override.strip()
            else:
                acc_video = eff_video_url

            if acc.share_times_override is not None:
                acc_shares = max(0, int(acc.share_times_override))
            else:
                acc_shares = eff_share_times

            if acc.photo_path_override is not None:
                acc_photo = acc.photo_path_override.strip()
            else:
                acc_photo = eff_photo_path

            payload.append({
                "name": acc.name or "Account",
                "session_id": acc_cookie,
                "friends": list(acc.friends),
                "message": acc_msg,
                "video_url": acc_video,
                "share_times": acc_shares,
                "photo_path": acc_photo,
            })
        _atomic_write_text(accounts_json_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        written_files.append(str(accounts_json_path))
    else:
        # Remove stale accounts.json so single-account .env takes effect cleanly.
        try:
            accounts_json_path.unlink(missing_ok=True)
        except OSError:
            pass

    all_friends: list[str] = []
    seen_friends: set[str] = set()
    for acc in (state.accounts if is_multi else [primary]):
        for f in acc.friends:
            low = f.lower()
            if low not in seen_friends:
                seen_friends.add(low)
                all_friends.append(f)

    friends_csv_path = export_friends_csv(target / "friends.csv", all_friends)
    written_files.append(str(friends_csv_path))

    return {
        "ok": True,
        "target_dir": str(target),
        "mode": "multi" if is_multi else "single",
        "files": written_files,
    }


def import_local_workspace(target_dir: str | Path) -> dict[str, Any]:
    """Read ``.env``, ``accounts.json``, and ``friends.csv`` into an ``AppState`` + secrets dict."""
    target = Path(target_dir)
    if not target.is_dir():
        raise FileNotFoundError(f"Workspace directory not found: {target}")

    files_found: list[str] = []
    warnings: list[str] = []
    env_map: dict[str, str] = {}

    env_path = target / ".env"
    if env_path.is_file():
        env_map = _parse_dotenv_file(env_path)
        files_found.append(str(env_path))

    csv_friends: list[str] = []
    friends_csv_path = target / "friends.csv"
    if friends_csv_path.is_file():
        try:
            csv_friends = import_friends_csv(friends_csv_path)
            files_found.append(str(friends_csv_path))
        except Exception as exc:
            warnings.append(f"Could not parse friends.csv: {exc}")

    disable_video = _is_truthy(env_map.get("DISABLE_VIDEO")) or _is_disabled_sentinel(env_map.get("STREAK_VIDEO_URL"))
    disable_photo = _is_truthy(env_map.get("DISABLE_PHOTO")) or _is_disabled_sentinel(env_map.get("PHOTO_PATH"))

    raw_video = env_map.get("STREAK_VIDEO_URL")
    if raw_video is None:
        video_url = DEFAULT_VIDEO_URL
        video_enabled = not disable_video
    elif raw_video.strip() == "" or _is_disabled_sentinel(raw_video):
        video_url = ""
        video_enabled = False
    else:
        video_url = raw_video.strip()
        video_enabled = not disable_video

    raw_photo = env_map.get("PHOTO_PATH")
    if raw_photo is None:
        photo_path = ""
        photo_enabled = not disable_photo
    elif raw_photo.strip() == "" or _is_disabled_sentinel(raw_photo):
        photo_path = ""
        photo_enabled = False
    else:
        photo_path = "" if raw_photo.strip() == DEFAULT_PHOTO_PATH else raw_photo.strip()
        photo_enabled = not disable_photo

    try:
        share_times = max(0, int(env_map.get("SHARE_TIMES", "2")))
    except (TypeError, ValueError):
        share_times = 2
        warnings.append("Invalid SHARE_TIMES in .env; defaulted to 2.")

    try:
        daily_cap = max(0, int(env_map.get("DAILY_CAP") or "0"))
    except (TypeError, ValueError):
        daily_cap = 0
        warnings.append("Invalid DAILY_CAP in .env; defaulted to 0.")

    headless_raw = env_map.get("HEADLESS", "true").strip().lower()
    headless_local = headless_raw not in ("false", "0", "no")

    tg_token = env_map.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = env_map.get("TELEGRAM_CHAT_ID", "").strip()
    message = env_map.get("MESSAGE", "🔥 Daily Streak")

    account_secrets: dict[str, str] = {}
    accounts: list[AccountProfile] = []
    multi_account = False

    accounts_json_path = target / "accounts.json"
    if accounts_json_path.is_file():
        try:
            raw_list = json.loads(accounts_json_path.read_text(encoding="utf-8"))
            if isinstance(raw_list, list) and raw_list:
                files_found.append(str(accounts_json_path))
                multi_account = len(raw_list) > 1
                eff_global_photo = (photo_path or DEFAULT_PHOTO_PATH) if photo_enabled else ""
                for item in raw_list:
                    if not isinstance(item, dict):
                        continue
                    friends_raw = item.get("friends") or []
                    friends, w = normalize_friends("\n".join(str(x) for x in friends_raw))
                    warnings.extend(w)

                    acc_msg = item.get("message")
                    msg_ov = str(acc_msg) if acc_msg is not None and str(acc_msg) != message else None

                    vid_ov = str(item["video_url"]) if "video_url" in item and item["video_url"] is not None else None
                    if vid_ov is not None and vid_ov == video_url:
                        vid_ov = None

                    sh_ov: int | None = None
                    if "share_times" in item and item["share_times"] is not None:
                        try:
                            sh_val = max(0, int(item["share_times"]))
                            if sh_val != share_times:
                                sh_ov = sh_val
                        except (TypeError, ValueError):
                            pass

                    ph_ov = str(item["photo_path"]) if "photo_path" in item and item["photo_path"] is not None else None
                    if ph_ov is not None:
                        ph_ov = ph_ov.strip()
                        if ph_ov == eff_global_photo:
                            ph_ov = None

                    acc = AccountProfile(
                        name=str(item.get("name") or "Account").strip() or "Account",
                        friends=friends,
                        message_override=msg_ov,
                        video_url_override=vid_ov,
                        share_times_override=sh_ov,
                        photo_path_override=ph_ov,
                    )
                    sid = str(item.get("session_id") or "").strip()
                    if sid:
                        account_secrets[acc.id] = sid
                    accounts.append(acc)
        except Exception as exc:
            warnings.append(f"Could not parse accounts.json: {exc}")

    if not accounts:
        env_friends_raw = env_map.get("FRIENDS_LIST", "")
        if env_friends_raw.strip():
            friends, w = normalize_friends(env_friends_raw)
            warnings.extend(w)
        else:
            friends = csv_friends

        sid = env_map.get("TIKTOK_SESSION_ID", "").strip()
        if sid or friends:
            acc = AccountProfile(name="Main", friends=friends)
            if sid:
                account_secrets[acc.id] = sid
            accounts.append(acc)

    state = AppState(
        multi_account=multi_account,
        accounts=accounts,
        message=message,
        video_enabled=video_enabled,
        video_url=video_url,
        share_times=share_times,
        photo_enabled=photo_enabled,
        photo_path=photo_path,
        daily_cap=daily_cap,
        headless_local=headless_local,
        telegram_chat_id=tg_chat_id,
        telegram_enabled=bool(tg_token and tg_chat_id),
        workspace_dir=str(target),
    )

    return {
        "ok": True,
        "state": state,
        "secrets": {
            "telegram_token": tg_token,
            "accounts": account_secrets,
        },
        "files": files_found,
        "warnings": warnings,
    }


_BUILTIN_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})\b"), "<REDACTED_GITHUB_TOKEN>"),
    (re.compile(r"(/bot)\d{6,15}:[A-Za-z0-9_-]{20,}"), r"\1<REDACTED_TELEGRAM_TOKEN>"),
    (re.compile(r"\b\d{6,15}:[A-Za-z0-9_-]{20,}\b"), "<REDACTED_TELEGRAM_TOKEN>"),
    (re.compile(r"(?i)(Authorization\s*:\s*(?:Bearer|Token|Basic)\s+)[^\s\r\n]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(Cookie\s*:\s*)[^\r\n]+"), r"\1<REDACTED>"),
    (
        re.compile(
            r"(?i)\b(sessionid|sessionid_ss|sid_tt|sid_guard|uid_tt|ttwid|passport_csrf_token|odin_tt)\s*=\s*(\"[^\"]*\"|[^;\s\r\n]+)"
        ),
        r"\1=<REDACTED>",
    ),
    (
        re.compile(
            r'(?i)"(session_id|sessionid|sessionid_ss|sid_tt|token|access_token|bot_token|TELEGRAM_BOT_TOKEN|TIKTOK_SESSION_ID|TIKTOK_COOKIES|PHOTO_DATA_URL)"\s*:\s*"[^"]*"'
        ),
        r'"\1": "<REDACTED>"',
    ),
    (
        re.compile(
            r"(?im)^(\s*(?:TIKTOK_SESSION_ID|TIKTOK_COOKIES|TIKTOK_ACCOUNTS_JSON|TELEGRAM_BOT_TOKEN|PHOTO_DATA_URL)\s*=\s*)([^\r\n]+)"
        ),
        r"\1<REDACTED>",
    ),
)


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Redact GitHub tokens, Telegram bot tokens, sessionids, and Cookie headers."""
    if not text:
        return ""
    out = str(text)

    extras = sorted(
        {str(s).strip() for s in extra_secrets if s and len(str(s).strip()) >= 6},
        key=len,
        reverse=True,
    )
    for secret in extras:
        if secret in out:
            out = out.replace(secret, "<REDACTED>")

    for pattern, repl in _BUILTIN_REDACT_PATTERNS:
        out = pattern.sub(repl, out)

    return out


__all__ = [
    "APP_DIR_NAME",
    "AccountProfile",
    "AppState",
    "ConfigStore",
    "DEFAULT_PHOTO_PATH",
    "DEFAULT_SOURCE_REPO",
    "DEFAULT_VIDEO_URL",
    "KEYRING_SERVICE",
    "SecretStore",
    "default_config_dir",
    "export_local_workspace",
    "import_local_workspace",
    "redact_secrets",
]
