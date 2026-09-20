"""GitHub automation for the setup GUI (REST API via urllib, no extra deps).

Covers: auth whoami, fork/create repo, enable Actions, read the secrets public
key, set repo secrets (libsodium sealed box), dispatch the workflow and read
run status. The workflow file this bot ships is .github/workflows/streak.yml.

Device Flow needs a registered OAuth App client_id; PAT works without one, so
PAT is the practical default.
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger("tiktok-streak")

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"

# Set this to your registered GitHub OAuth App's client id to enable Device Flow.
OAUTH_CLIENT_ID = "Ov23liH6R2uxBFftAzIk"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str):
        self.token = token.strip()

    # -- low level -------------------------------------------------------
    def _req(self, method: str, path: str, body=None, api: str = API) -> dict:
        url = path if path.startswith("http") else f"{api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "tiktok-streak-gui")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "ignore")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise GitHubError(f"{method} {path} -> {e.code}: {detail[:300]}")

    # -- auth ------------------------------------------------------------
    def whoami(self) -> dict:
        return self._req("GET", "/user")

    # -- repos -----------------------------------------------------------
    def get_repo(self, full_name: str) -> dict:
        return self._req("GET", f"/repos/{full_name}")

    def fork(self, upstream_full: str, org: str = None) -> dict:
        path = f"/repos/{upstream_full}/forks"
        body = {"organization": org} if org else {}
        return self._req("POST", path, body)

    def create_from_template(self, template_full: str, name: str,
                             owner: str = None, private: bool = False) -> dict:
        path = f"/repos/{template_full}/generate"
        body = {"name": name, "private": private, "include_all_branches": False}
        if owner:
            # generate uses `owner` as the org/account to create under
            body["owner"] = owner
        return self._req("POST", path, body)

    def default_branch(self, full_name: str) -> str:
        return self.get_repo(full_name).get("default_branch", "main")

    # -- actions ---------------------------------------------------------
    def enable_actions(self, full_name: str) -> dict:
        # For a fork, Actions is disabled by default; this turns it on.
        return self._req("PUT", f"/repos/{full_name}/actions/permissions",
                         {"enabled": True, "allowed_actions": "all"})

    def actions_enabled(self, full_name: str) -> bool:
        try:
            d = self._req("GET", f"/repos/{full_name}/actions/permissions")
            return bool(d.get("enabled"))
        except GitHubError:
            return False

    def secret_key(self, full_name: str) -> dict:
        return self._req("GET", f"/repos/{full_name}/actions/secrets/public-key")

    def set_secret(self, full_name: str, name: str, value: str) -> None:
        from nacl import encoding, public  # PyNaCl
        key = self.secret_key(full_name)
        pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
        sealed = public.SealedBox(pk).encrypt(value.encode("utf-8"))
        enc = base64.b64encode(sealed).decode()
        self._req("PUT", f"/repos/{full_name}/actions/secrets/{name}",
                  {"encrypted_value": enc, "key_id": key["key_id"]})

    def list_secrets(self, full_name: str) -> list:
        d = self._req("GET", f"/repos/{full_name}/actions/secrets")
        return [s["name"] for s in d.get("secrets", [])]

    def set_secrets(self, full_name: str, secrets: dict) -> list:
        """Set many secrets; returns the names written. Raises on first error."""
        written = []
        for name, value in secrets.items():
            self.set_secret(full_name, name, value)
            written.append(name)
        return written

    # -- workflow --------------------------------------------------------
    def dispatch(self, full_name: str, workflow_file: str, ref: str) -> None:
        self._req("POST", f"/repos/{full_name}/actions/workflows/{workflow_file}/dispatches",
                  {"ref": ref})

    def runs(self, full_name: str, workflow_file: str, limit: int = 5) -> list:
        d = self._req("GET",
                      f"/repos/{full_name}/actions/workflows/{workflow_file}/runs?per_page={limit}")
        return d.get("workflow_runs", [])

    def latest_run(self, full_name: str, workflow_file: str) -> dict:
        runs = self.runs(full_name, workflow_file, 1)
        return runs[0] if runs else {}


# --------------------------------------------------------------------------
# Device Flow (optional; needs OAUTH_CLIENT_ID)
# --------------------------------------------------------------------------

def device_flow_start() -> dict:
    """Begin Device Flow. Returns the verification code/URL for the user to enter.

    Needs OAUTH_CLIENT_ID (a registered OAuth App's public client id). Scope
    `repo workflow` lets the app write secrets and dispatch the workflow in the
    user's own fork; classic `repo workflow` (not a fine-grained PAT) keeps this
    a one-click authorize.
    """
    if not OAUTH_CLIENT_ID:
        raise GitHubError("Device Flow is not configured (no OAuth client id).")
    req = urllib.request.Request(
        DEVICE_CODE_URL,
        data=urllib.parse.urlencode({"client_id": OAUTH_CLIENT_ID,
                                     "scope": "repo workflow"}).encode(),
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    if not d.get("device_code"):
        raise GitHubError(f"Device flow start failed: {d.get('error_description') or d}")
    return d


def device_flow_poll(device_code: str, interval: int = 5, timeout: int = 600) -> str:
    """Wait for the user to authorize; return the access token.

    Honors the server's interval and `slow_down`. GitHub sends
    `authorization_pending` until the user approves, then the token.
    """
    end = time.time() + timeout
    while time.time() < end:
        req = urllib.request.Request(
            TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": OAUTH_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode(),
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        if d.get("access_token"):
            return d["access_token"]
        err = d.get("error")
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise GitHubError(f"Device flow error: {d.get('error_description') or err}")
    raise GitHubError("Device flow timed out.")


import urllib.parse  # noqa: E402  (used above)
