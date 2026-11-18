"""In-process local streak execution bridging AppState/SecretStore to main.run().

Zero Qt dependencies. Lazily imports `main` and `src.config` so importing
`gui.services` never triggers Selenium or dotenv side effects upfront.
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from gui.services.storage import (
    AppState,
    DEFAULT_PHOTO_PATH,
    DEFAULT_VIDEO_URL,
    SecretStore,
    redact_secrets,
)

logger = logging.getLogger("tiktok-streak")

_DISABLE_SENTINELS = {"none", "off", "disabled"}


def _resolve_asset_path(raw_path: str) -> str:
    """Resolve default or relative asset paths across repo root and PyInstaller bundle."""
    cleaned = str(raw_path or "").strip()
    if not cleaned or cleaned.lower() in _DISABLE_SENTINELS:
        return ""
    if os.path.isabs(cleaned) or os.path.exists(cleaned):
        return cleaned
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, cleaned)
        if os.path.exists(bundled):
            return bundled
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / cleaned
    if candidate.exists():
        return str(candidate)
    return cleaned


class _RedactingCallbackHandler(logging.Handler):
    """Capture log lines from `tiktok-streak`, redact secrets, and forward to callback."""

    def __init__(
        self,
        callback: Callable[[str], None],
        extra_secrets: Iterable[str] = (),
    ) -> None:
        super().__init__(level=logging.INFO)
        self._callback = callback
        self._extra_secrets = tuple(extra_secrets)
        self.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            safe_line = redact_secrets(formatted, extra_secrets=self._extra_secrets)
            self._callback(safe_line)
        except Exception:
            self.handleError(record)


class LocalRunner:
    """Runs the core TikTok streak automation in-process for local execution or 1-friend smoke tests."""

    def build_accounts(
        self,
        state: AppState,
        secret_store: SecretStore,
        only_account: str | None = None,
        only_friend: str | None = None,
    ) -> list[Any]:
        """Convert `AppState` and `SecretStore` into `src.config.AccountConfig` instances."""
        from src.config import AccountConfig, clean_handle

        if not state.accounts:
            return []

        selected_profiles = list(state.accounts) if state.multi_account else [state.accounts[0]]
        if only_account:
            target = str(only_account).strip()
            selected_profiles = [
                acc for acc in selected_profiles
                if acc.id == target or acc.name == target
            ]

        accounts: list[AccountConfig] = []
        for acc in selected_profiles:
            cookie_payload = secret_store.get_account_cookie(acc.id)
            friends = [clean_handle(f) for f in acc.friends if clean_handle(f)]
            if only_friend:
                target_friend = clean_handle(only_friend)
                friends = [target_friend] if target_friend else []

            message = acc.message_override if acc.message_override is not None else state.message

            if acc.video_url_override is not None:
                raw_vid = acc.video_url_override.strip()
                video_url = "" if raw_vid.lower() in _DISABLE_SENTINELS else raw_vid
            elif state.video_enabled:
                video_url = (state.video_url or DEFAULT_VIDEO_URL).strip()
                if video_url.lower() in _DISABLE_SENTINELS:
                    video_url = ""
            else:
                video_url = ""

            if acc.share_times_override is not None:
                share_times = max(0, int(acc.share_times_override))
            elif state.video_enabled:
                share_times = max(0, int(state.share_times))
            else:
                share_times = 0

            if not video_url:
                share_times = 0

            if acc.photo_path_override is not None:
                photo_path = _resolve_asset_path(acc.photo_path_override)
            elif state.photo_enabled:
                photo_path = _resolve_asset_path(state.photo_path or DEFAULT_PHOTO_PATH)
            else:
                photo_path = ""

            accounts.append(
                AccountConfig(
                    name=acc.name or "Account",
                    session_id=cookie_payload,
                    friends=friends,
                    message=message,
                    video_url=video_url,
                    share_times=share_times,
                    photo_path=photo_path,
                )
            )
        return accounts

    @staticmethod
    def _collect_secrets(state: AppState, secret_store: SecretStore) -> list[str]:
        from gui.services.tiktok_auth import parse_cookie_payload

        secrets: list[str] = []
        gh = secret_store.get_github_token()
        if gh:
            secrets.append(gh)
        tg = secret_store.get_telegram_token()
        if tg:
            secrets.append(tg)
        for acc in state.accounts:
            cookie = secret_store.get_account_cookie(acc.id)
            if cookie:
                secrets.append(cookie)
                for val in parse_cookie_payload(cookie).values():
                    if val and len(val) >= 6:
                        secrets.append(val)
        return secrets

    def run(
        self,
        state: AppState,
        secret_store: SecretStore,
        *,
        only_account: str | None = None,
        only_friend: str | None = None,
        headless: bool | None = None,
        stop_event: threading.Event | None = None,
        log_cb: Callable[[str], None] | None = None,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run local streak automation safely, restoring env and logging handler in `finally`."""
        accounts = self.build_accounts(
            state,
            secret_store,
            only_account=only_account,
            only_friend=only_friend,
        )
        if not accounts:
            return {
                "ok": False,
                "results": [],
                "elapsed": 0,
                "critical_failure": True,
                "error": "No matching TikTok account configured.",
            }

        missing_cookies = [acc.name for acc in accounts if not acc.session_id.strip()]
        if len(missing_cookies) == len(accounts):
            return {
                "ok": False,
                "results": [],
                "elapsed": 0,
                "critical_failure": True,
                "error": "No TikTok session cookie found for the selected account(s).",
            }

        import main as bot_main

        eff_headless = state.headless_local if headless is None else bool(headless)
        tg_token = secret_store.get_telegram_token() if state.telegram_enabled else ""
        tg_chat = state.telegram_chat_id.strip() if state.telegram_enabled else ""

        env_updates = {
            "DAILY_CAP": str(max(0, int(state.daily_cap))),
            "HEADLESS": "true" if eff_headless else "false",
            "TELEGRAM_BOT_TOKEN": tg_token,
            "TELEGRAM_CHAT_ID": tg_chat,
        }
        sentinel = object()
        saved_env = {k: os.environ.get(k, sentinel) for k in env_updates}

        handler: _RedactingCallbackHandler | None = None
        prev_level = logger.level
        if log_cb is not None:
            extra_secrets = self._collect_secrets(state, secret_store)
            handler = _RedactingCallbackHandler(log_cb, extra_secrets=extra_secrets)
            logger.addHandler(handler)
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)

        try:
            for k, v in env_updates.items():
                os.environ[k] = v

            sig = inspect.signature(bot_main.run)
            kwargs: dict[str, Any] = {}
            if "accounts_override" in sig.parameters:
                kwargs["accounts_override"] = accounts
            if "headless" in sig.parameters:
                kwargs["headless"] = eff_headless
            if "only_account" in sig.parameters:
                kwargs["only_account"] = None
            if "only_friend" in sig.parameters:
                kwargs["only_friend"] = only_friend
            if "stop_event" in sig.parameters:
                kwargs["stop_event"] = stop_event
            if "progress_cb" in sig.parameters:
                kwargs["progress_cb"] = progress_cb

            result = bot_main.run(**kwargs)
            if isinstance(result, dict):
                return result
            return {
                "ok": True,
                "results": [],
                "elapsed": 0,
                "critical_failure": False,
            }
        except SystemExit as exc:
            ok = exc.code in (0, None)
            return {
                "ok": ok,
                "results": [],
                "elapsed": 0,
                "critical_failure": not ok,
                "error": None if ok else f"Bot exited with code {exc.code}",
            }
        except Exception as exc:
            logger.exception("LocalRunner failed: %s", exc)
            return {
                "ok": False,
                "results": [],
                "elapsed": 0,
                "critical_failure": True,
                "error": redact_secrets(str(exc), self._collect_secrets(state, secret_store)),
            }
        finally:
            for k, old_val in saved_env.items():
                if old_val is sentinel:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = str(old_val)
            if handler is not None:
                logger.removeHandler(handler)
                logger.setLevel(prev_level)

    def run_smoke_test(
        self,
        state: AppState,
        secret_store: SecretStore,
        friend: str,
        *,
        account_id: str | None = None,
        headless: bool = False,
        stop_event: threading.Event | None = None,
        log_cb: Callable[[str], None] | None = None,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a targeted 1-friend smoke test (visible browser by default)."""
        return self.run(
            state,
            secret_store,
            only_account=account_id,
            only_friend=friend,
            headless=headless,
            stop_event=stop_event,
            log_cb=log_cb,
            progress_cb=progress_cb,
        )


__all__ = ["LocalRunner"]
