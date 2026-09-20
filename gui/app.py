"""PyWebview desktop app: guided setup for the TikTok streak bot.

Run:  python -m gui.app

The window is a single HTML wizard (gui/web/index.html). All privileged work
(GitHub REST, reading browser cookies) runs here in Python and is exposed to the
page through window.pywebview.api.*. Nothing is uploaded anywhere except to
GitHub with the user's own token.
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui import browsers
from gui.github_api import GitHub, GitHubError

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")

# Repo users fork from by default (the maintained bot with streak + share + photo).
DEFAULT_SOURCE_REPO = "alexanderfrit/tiktok-streak"
WORKFLOW_FILE = "streak.yml"

# Secret names the workflow consumes (see .github/workflows/streak.yml).
SECRET_NAMES = [
    "TIKTOK_SESSION_ID", "TIKTOK_ACCOUNTS_JSON", "TIKTOK_COOKIES",
    "MESSAGE", "FRIENDS_LIST", "STREAK_VIDEO_URL", "SHARE_TIMES", "PHOTO_PATH",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]

_CONF_DIR = os.path.join(os.path.expanduser("~"), ".tiktok-streak-gui")
_TOKEN_FILE = os.path.join(_CONF_DIR, "auth.json")


def _load_token() -> str:
    """Token from OS keyring if available, else a local file."""
    try:
        import keyring
        tok = keyring.get_password("tiktok-streak-gui", "github")
        if tok:
            return tok
    except Exception:
        pass
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f).get("token", "")
    except Exception:
        return ""


def _save_token(token: str) -> str:
    """Persist the token; returns where it was stored ('keyring' or path)."""
    try:
        import keyring
        keyring.set_password("tiktok-streak-gui", "github", token)
        return "keyring"
    except Exception:
        pass
    os.makedirs(_CONF_DIR, exist_ok=True)
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"token": token}, f)
    try:
        os.chmod(_TOKEN_FILE, 0o600)
    except Exception:
        pass
    return _TOKEN_FILE


class Api:
    # ---- browsers / cookies -------------------------------------------
    def list_browsers(self) -> dict:
        try:
            return {"ok": True, "sources": browsers.list_sources()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_cookies(self, source_id: str) -> dict:
        try:
            data = browsers.grab_session(source_id)
            # never log cookie values
            return {"ok": True, "sessionid": data["sessionid"],
                    "cookie_text": data["cookie_text"],
                    "count": len(data["cookies"])}
        except browsers.BrowserLocked:
            return {"ok": False, "locked": True,
                    "error": "Browser itu sedang terbuka, jadi datanya terkunci. "
                             "Tutup browser tersebut, lalu coba lagi."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def parse_cookie(self, raw: str) -> dict:
        """Parse a pasted cookie string / raw token using the bot's own parser."""
        try:
            from src.browser import parse_cookie_payload
            parsed = parse_cookie_payload(raw)
            return {"ok": True, "sessionid": parsed.get("sessionid", ""),
                    "count": len(parsed), "parsed": parsed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- github --------------------------------------------------------
    def github_whoami(self, token: str) -> dict:
        try:
            me = GitHub(token).whoami()
            return {"ok": True, "login": me.get("login"), "avatar": me.get("avatar_url")}
        except GitHubError as e:
            return {"ok": False, "error": str(e)}

    def save_token(self, token: str) -> dict:
        try:
            from gui.github_api import GitHub
            me = GitHub(token).whoami()
            where = _save_token(token.strip())
            return {"ok": True, "login": me.get("login"), "stored": where}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def load_token(self) -> dict:
        tok = _load_token()
        return {"ok": True, "token": tok}

    # ---- github sign-in (Device Flow) ----------------------------------
    def github_login_start(self) -> dict:
        """Begin 1-click GitHub sign-in; returns the code + URL for the user."""
        try:
            from gui.github_api import device_flow_start, device_flow_poll, GitHubError  # noqa
        except Exception as e:
            return {"ok": False, "error": str(e)}
        try:
            d = device_flow_start()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        # stash the poll info for github_login_poll (device_code stays server-side)
        return {"ok": True,
                "user_code": d.get("user_code"),
                "verification_uri": d.get("verification_uri"),
                "device_code": d.get("device_code"),
                "interval": d.get("interval", 5),
                "expires_in": d.get("expires_in", 900)}

    def github_login_poll(self, device_code: str, interval: int = 5) -> dict:
        """Block until the user authorizes (or timeout). Saves the token on success."""
        try:
            from gui.github_api import device_flow_poll, GitHub
        except Exception as e:
            return {"ok": False, "error": str(e)}
        try:
            token = device_flow_poll(device_code, interval=int(interval or 5))
            me = GitHub(token).whoami()
            where = _save_token(token.strip())
            return {"ok": True, "token": token, "login": me.get("login"), "stored": where}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url: str) -> dict:
        """Open a URL in the user's default browser (for the device-code page)."""
        import webbrowser
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- tiktok session check ------------------------------------------
    def check_session(self, sessionid: str) -> dict:
        """Verify a sessionid is live; return the logged-in username.

        Catches an expired cookie up front instead of failing silently in the run.
        """
        import urllib.request, urllib.error
        sid = (sessionid or "").strip()
        if not sid:
            return {"ok": False, "error": "sessionid is empty"}
        req = urllib.request.Request(
            "https://www.tiktok.com/passport/web/account/info/?aid=1459",
            headers={"Cookie": f"sessionid={sid}",
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        d = data.get("data") or {}
        username = d.get("username") or d.get("unique_id") or d.get("screen_name")
        if username:
            return {"ok": True, "username": username}
        return {"ok": False, "error": data.get("message") or "session invalid or expired"}

    def provision(self, opts: dict) -> dict:
        """Fork/create repo, enable Actions, set secrets, optionally dispatch.

        opts: {token, source_repo, owner, repo_name, private, secrets{},
               dispatch, ref}
        """
        steps = []
        try:
            gh = GitHub(opts["token"])
            login = gh.whoami().get("login")
            steps.append(f"Authenticated as {login}")

            source = opts.get("source_repo") or DEFAULT_SOURCE_REPO
            owner = opts.get("owner") or login
            name = opts["repo_name"]
            full = f"{owner}/{name}"
            exists = False
            try:
                gh.get_repo(full)
                exists = True
            except GitHubError:
                exists = False

            if not exists:
                steps.append(f"Forking {source} -> {full} ...")
                # fork into the user's account (or org)
                gh.fork(source, org=(owner if owner != login else None))
                # fork is async; wait for it to appear
                import time
                for _ in range(20):
                    try:
                        gh.get_repo(full)
                        break
                    except GitHubError:
                        time.sleep(2)
                steps.append("Fork ready")
            else:
                steps.append(f"Repo {full} already exists; reusing")

            steps.append("Enabling Actions ...")
            gh.enable_actions(full)

            ref = opts.get("ref") or gh.default_branch(full)
            steps.append("Writing repository secrets ...")
            written = gh.set_secrets(full, opts["secrets"])
            steps.append(f"Secrets set: {', '.join(written)}")

            run_info = None
            if opts.get("dispatch"):
                steps.append(f"Dispatching {WORKFLOW_FILE} on {ref} ...")
                gh.dispatch(full, WORKFLOW_FILE, ref)
                import time
                time.sleep(3)
                run = gh.latest_run(full, WORKFLOW_FILE)
                run_info = {"status": run.get("status"), "conclusion": run.get("conclusion"),
                            "html_url": run.get("html_url")}

            return {"ok": True, "full_name": full, "ref": ref, "steps": steps, "run": run_info,
                    "actions_url": f"https://github.com/{full}/actions",
                    "secrets_url": f"https://github.com/{full}/settings/secrets/actions"}
        except Exception as e:
            logger.error("provision failed: %s", e)
            return {"ok": False, "error": str(e), "steps": steps}

    def run_status(self, token: str, full_name: str) -> dict:
        try:
            run = GitHub(token).latest_run(full_name, WORKFLOW_FILE)
            return {"ok": True, "status": run.get("status"), "conclusion": run.get("conclusion"),
                    "html_url": run.get("html_url")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- telegram ------------------------------------------------------
    def telegram_find_chat_id(self, token: str) -> dict:
        """Read the chat id from the bot's recent updates (user must have /start-ed it)."""
        import urllib.request
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "bot token empty"}
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description", "getUpdates failed")}
        ids = []
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                ids.append(str(chat["id"]))
        if not ids:
            return {"ok": False,
                    "error": "No messages yet. Open the bot in Telegram, press Start, then retry."}
        # most recent chat id
        return {"ok": True, "chat_id": ids[-1], "all": sorted(set(ids))}

    def telegram_test(self, token: str, chat_id: str = "") -> dict:
        """Send a test message so the user sees the bot working immediately."""
        import urllib.request, urllib.parse
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "bot token empty"}
        cid = (chat_id or "").strip()
        if not cid:
            got = self.telegram_find_chat_id(token)
            if not got.get("ok"):
                return got
            cid = got["chat_id"]
        data = urllib.parse.urlencode({"chat_id": cid, "text": "Bot kamu siap ✅"}).encode()
        try:
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
            ) as r:
                ok = json.loads(r.read().decode("utf-8", "ignore")).get("ok")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": bool(ok), "chat_id": cid, "error": None if ok else "sendMessage failed"}

    def run_now(self, token: str, full_name: str, ref: str = "") -> dict:
        """Dispatch the workflow on demand (the dashboard's Run now button)."""
        try:
            gh = GitHub(token)
            r = ref or gh.default_branch(full_name)
            gh.dispatch(full_name, WORKFLOW_FILE, r)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- meta ----------------------------------------------------------
    def secret_names(self) -> dict:
        return {"ok": True, "names": SECRET_NAMES, "source_repo": DEFAULT_SOURCE_REPO}


def _index_path() -> str:
    """Locate web/index.html, both from source and from a PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "gui", "web", "index.html")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "web", "index.html")


def main() -> None:
    try:
        import webview
    except ImportError:
        print("PyWebview is not installed. Run:  pip install -r requirements.txt -r requirements-gui.txt")
        raise SystemExit(1)

    webview.create_window("TikTok Streak - Setup", _index_path(), js_api=Api(),
                          width=980, height=760, min_size=(820, 640))
    webview.start()


if __name__ == "__main__":
    main()
