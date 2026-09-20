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
    "MESSAGE", "FRIENDS_LIST", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
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

    # ---- meta ----------------------------------------------------------
    def secret_names(self) -> dict:
        return {"ok": True, "names": SECRET_NAMES, "source_repo": DEFAULT_SOURCE_REPO}


def main() -> None:
    try:
        import webview
    except ImportError:
        print("PyWebview is not installed. Run:  pip install -r requirements.txt")
        raise SystemExit(1)

    here = os.path.dirname(os.path.abspath(__file__))
    index = os.path.join(here, "web", "index.html")
    webview.create_window("TikTok Streak - Setup", index, js_api=Api(),
                          width=980, height=760, min_size=(820, 640))
    webview.start()


if __name__ == "__main__":
    main()
