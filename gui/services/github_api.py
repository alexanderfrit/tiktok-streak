"""GitHub REST API & Telegram Bot API helpers (pure Python stdlib + PyNaCl).

Zero Qt dependencies. Covers Device Flow OAuth, scope checking, private repo
creation via Git Data API, public fork + upstream merge, secret syncing with
stale-secret cleanup, workflow cron editing, run/log/artifact diagnostics, and
Telegram setup verification.
"""
from __future__ import annotations

import base64
import fnmatch
import io
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from gui.services.validators import validate_telegram_token

logger = logging.getLogger("tiktok-streak")

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"

OAUTH_CLIENT_ID = "Ov23liH6R2uxBFftAzIk"
DEFAULT_SOURCE_REPO = "alexanderfrit/tiktok-streak"
WORKFLOW_FILE = "streak.yml"

SECRET_NAMES: list[str] = [
    "TIKTOK_SESSION_ID",
    "TIKTOK_ACCOUNTS_JSON",
    "TIKTOK_COOKIES",
    "MESSAGE",
    "FRIENDS_LIST",
    "STREAK_VIDEO_URL",
    "SHARE_TIMES",
    "PHOTO_PATH",
    "PHOTO_DATA_URL",
    "DAILY_CAP",
    "DISABLE_VIDEO",
    "DISABLE_PHOTO",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

REQUIRED_OAUTH_SCOPES: tuple[str, ...] = ("repo", "workflow")

_CRON_LINE_RE = re.compile(
    r"^([ \t]*-\s*cron:\s*)(['\"]?)([^'\"\r\n#]+)\2([ \t]*(?:#[^\r\n]*)?)\r?$",
    re.MULTILINE,
)


class GitHubError(RuntimeError):
    """Error raised for GitHub API / OAuth failures."""

    def __init__(self, message: str, status_code: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class _NoCrossHostAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Strip Authorization header when GitHub redirects to Azure Blob Storage."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            orig_host = urllib.parse.urlparse(req.full_url).netloc.lower()
            next_host = urllib.parse.urlparse(newurl).netloc.lower()
            if orig_host != next_host:
                new_req.remove_header("Authorization")
                new_req.remove_header("Accept")
        return new_req


_REDIRECT_OPENER = urllib.request.build_opener(_NoCrossHostAuthRedirect)


def _encode_seg(segment: str) -> str:
    return urllib.parse.quote(str(segment).strip(), safe="")


def _encode_repo(full_name: str) -> str:
    cleaned = str(full_name or "").strip().strip("/")
    parts = cleaned.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubError(f"Invalid repository full_name: {full_name!r} (expected 'owner/repo').")
    return f"{_encode_seg(parts[0])}/{_encode_seg(parts[1])}"


def _encode_rel_path(rel_path: str) -> str:
    parts = [p for p in str(rel_path).replace("\\", "/").split("/") if p]
    return "/".join(_encode_seg(p) for p in parts)


def resolve_repo_parts(repo_input: str, default_owner: str = "") -> tuple[str, str]:
    """Parse ``'owner/repo'`` or ``'repo'`` into ``(owner, repo)``."""
    raw = str(repo_input or "").strip()
    fallback_owner = str(default_owner or "").strip()
    if raw.startswith(("https://github.com/", "http://github.com/")):
        parsed = urllib.parse.urlparse(raw)
        raw = parsed.path.strip("/")
        if raw.endswith(".git"):
            raw = raw[:-4]
    elif raw.count("/") >= 2 and raw.endswith("/"):
        raw = raw.rstrip("/")
    if not raw:
        return fallback_owner, ""
    if "/" in raw:
        parts = [p.strip() for p in raw.split("/")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise GitHubError(
                f"Invalid repository name: {repo_input!r} (expected 'owner/repo' or 'repo')."
            )
        return parts[0], parts[1]
    return fallback_owner, raw


def has_required_scopes(scopes: Iterable[str]) -> bool:
    """Return True when scopes include both ``repo`` and ``workflow``."""
    norm = {str(s).strip().lower() for s in scopes if str(s).strip()}
    return all(req in norm for req in REQUIRED_OAUTH_SCOPES)


def resolve_source_dir(source_dir: str | Path | None = None) -> Path:
    """Resolve the directory holding bot source files (`main.py`, `src/`, `.github/`)."""
    if source_dir is not None:
        return Path(source_dir)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        if (mp / "main.py").is_file() and (mp / "src").is_dir():
            return mp
    return Path(__file__).resolve().parents[2]


def _collect_bot_files(source_dir: Path, cron_expr: str | None = None) -> dict[str, bytes]:
    """Collect bundled bot source files to commit into a private repo."""
    required_paths = [
        "main.py",
        "requirements.txt",
        "test_runner.py",
        "assets/default_photo.png",
        f".github/workflows/{WORKFLOW_FILE}",
    ]
    files: dict[str, bytes] = {}
    for rel in required_paths:
        full = source_dir / rel
        if not full.is_file():
            raise GitHubError(f"Required bundled bot file not found: {full}")
        data = full.read_bytes()
        if rel == f".github/workflows/{WORKFLOW_FILE}" and cron_expr:
            text = _rewrite_workflow_cron_text(data.decode("utf-8"), cron_expr)
            data = text.encode("utf-8")
        files[rel] = data

    src_dir = source_dir / "src"
    if not src_dir.is_dir():
        raise GitHubError(f"Required bundled src directory not found: {src_dir}")
    py_files = sorted(src_dir.glob("*.py"))
    if not py_files:
        raise GitHubError(f"No Python modules found under {src_dir}")
    for py_path in py_files:
        files[f"src/{py_path.name}"] = py_path.read_bytes()

    return files


def _extract_workflow_cron_text(yaml_text: str) -> str:
    m = _CRON_LINE_RE.search(yaml_text)
    return m.group(3).strip() if m else ""


def _rewrite_workflow_cron_text(yaml_text: str, cron_expr: str) -> str:
    cleaned = " ".join(str(cron_expr or "").strip().split())
    if len(cleaned.split(" ")) != 5:
        raise GitHubError(f"Invalid 5-field cron expression: {cron_expr!r}")

    def _repl(match: re.Match[str]) -> str:
        prefix, _quote, _old, suffix = match.groups()
        return f"{prefix}'{cleaned}'{suffix}"

    updated, count = _CRON_LINE_RE.subn(_repl, yaml_text, count=1)
    if count == 0:
        raise GitHubError("Could not locate `- cron:` entry inside workflow YAML.")
    return updated


class GitHub:
    """Thin, zero-dependency (except PyNaCl for secret sealing) GitHub REST client."""

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()

    def _req_raw(
        self,
        method: str,
        path: str,
        body: Any = None,
        api: str = API,
        follow_cross_host_auth_strip: bool = False,
    ) -> tuple[bytes, dict[str, str], int]:
        url = path if path.startswith("http") else f"{api}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "tiktok-streak-gui")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        opener = _REDIRECT_OPENER.open if follow_cross_host_auth_strip else urllib.request.urlopen
        try:
            with opener(req, timeout=30) as r:
                raw = r.read()
                hdrs = {k.lower(): v for k, v in (getattr(r, "headers", None) or {}).items()}
                status = getattr(r, "status", 200)
                return raw, hdrs, status
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise GitHubError(
                f"{method} {path} -> {e.code}: {detail[:300]}",
                status_code=e.code,
                detail=detail,
            ) from e
        except urllib.error.URLError as e:
            raise GitHubError(f"{method} {path} network error: {e.reason}") from e
        except TimeoutError as e:
            raise GitHubError(f"{method} {path} timed out.") from e

    def _req_with_headers(self, method: str, path: str, body: Any = None, api: str = API) -> tuple[dict, dict[str, str]]:
        raw, hdrs, _status = self._req_raw(method, path, body=body, api=api)
        if not raw:
            return {}, hdrs
        text = raw.decode("utf-8", "ignore").strip()
        if not text:
            return {}, hdrs
        try:
            return json.loads(text), hdrs
        except json.JSONDecodeError as e:
            raise GitHubError(f"{method} {path} returned invalid JSON: {e}") from e

    def _req(self, method: str, path: str, body: Any = None, api: str = API) -> dict:
        data, _hdrs = self._req_with_headers(method, path, body=body, api=api)
        return data

    # -- auth ------------------------------------------------------------
    def whoami(self) -> dict[str, Any]:
        data, hdrs = self._req_with_headers("GET", "/user")
        scope_hdr = hdrs.get("x-oauth-scopes")
        if scope_hdr is not None:
            scopes = [s.strip() for s in scope_hdr.split(",") if s.strip()]
            data["scopes"] = scopes
            data["scopes_header_present"] = True
            data["has_required_scopes"] = has_required_scopes(scopes)
        else:
            data["scopes"] = []
            data["scopes_header_present"] = False
            data["has_required_scopes"] = True  # Fine-grained PATs omit X-OAuth-Scopes
        return data

    # -- repos -----------------------------------------------------------
    def get_repo(self, full_name: str) -> dict:
        return self._req("GET", f"/repos/{_encode_repo(full_name)}")

    def list_user_repos(self, limit: int = 100) -> list[dict[str, Any]]:
        """List repositories accessible to the authenticated user, most recently updated first."""
        max_items = int(limit)
        if max_items <= 0:
            return []
        per_page = min(100, max_items)
        results: list[dict[str, Any]] = []
        page = 1
        while len(results) < max_items:
            path = (
                "/user/repos?sort=updated"
                "&affiliation=owner,collaborator,organization_member"
                f"&per_page={per_page}"
            )
            if page > 1:
                path = f"{path}&page={page}"
            batch = self._req("GET", path)
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                full_name = str(item.get("full_name") or "")
                owner_obj = item.get("owner")
                if isinstance(owner_obj, dict):
                    owner_login = str(owner_obj.get("login") or "")
                else:
                    owner_login = str(owner_obj or "")
                if not owner_login and "/" in full_name:
                    owner_login = full_name.split("/", 1)[0]
                results.append({
                    "full_name": full_name,
                    "name": str(item.get("name") or ""),
                    "owner": owner_login,
                    "private": bool(item.get("private", False)),
                    "default_branch": str(item.get("default_branch") or "main"),
                    "html_url": str(item.get("html_url") or ""),
                })
                if len(results) >= max_items:
                    break
            if len(batch) < per_page:
                break
            page += 1
        return results

    def repo_has_workflow(self, full_name: str, workflow_file: str = WORKFLOW_FILE) -> bool:
        """Return True when ``.github/workflows/{workflow_file}`` exists in the repository."""
        enc_repo = _encode_repo(full_name)
        wf_path = _encode_rel_path(f".github/workflows/{workflow_file}")
        try:
            data = self._req("GET", f"/repos/{enc_repo}/contents/{wf_path}")
            return isinstance(data, dict)
        except GitHubError as exc:
            if exc.status_code == 404:
                return False
            raise

    def default_branch(self, full_name: str) -> str:
        return self.get_repo(full_name).get("default_branch", "main")

    def fork(
        self,
        upstream_full: str,
        org: str | None = None,
        name: str | None = None,
        wait_ready: bool = True,
        timeout: float = 40.0,
    ) -> dict:
        path = f"/repos/{_encode_repo(upstream_full)}/forks"
        body: dict[str, Any] = {}
        if org:
            body["organization"] = org
        if name:
            body["name"] = name
        created = self._req("POST", path, body)

        target_full = created.get("full_name")
        if not target_full:
            owner = org or self.whoami().get("login")
            repo_part = name or upstream_full.split("/")[-1]
            target_full = f"{owner}/{repo_part}"

        if not wait_ready:
            return created

        deadline = time.time() + max(5.0, float(timeout))
        while time.time() < deadline:
            try:
                repo = self.get_repo(target_full)
                if repo.get("default_branch"):
                    return repo
            except GitHubError as exc:
                if exc.status_code not in (404, 409, None):
                    raise
            time.sleep(2.0)

        raise GitHubError(
            f"Timed out after {int(timeout)}s waiting for forked repository '{target_full}' to become ready.",
            status_code=408,
        )

    def merge_upstream(self, full_name: str, branch: str | None = None) -> dict:
        target_branch = branch or self.default_branch(full_name)
        return self._req(
            "POST",
            f"/repos/{_encode_repo(full_name)}/merge-upstream",
            {"branch": target_branch},
        )

    def create_from_template(
        self,
        template_full: str,
        name: str,
        owner: str | None = None,
        private: bool = False,
    ) -> dict:
        path = f"/repos/{_encode_repo(template_full)}/generate"
        body: dict[str, Any] = {"name": name, "private": private, "include_all_branches": False}
        if owner:
            body["owner"] = owner
        return self._req("POST", path, body)

    def create_private_repo_with_bot(
        self,
        owner: str,
        repo_name: str,
        source_dir: str | Path | None = None,
        cron_expr: str | None = None,
        progress_cb: Callable[[str], None] | None = None,
        push_files: bool = True,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Create (or update) a private repository and commit bundled bot files via Git Data API."""
        def _progress(msg: str) -> None:
            if progress_cb:
                try:
                    progress_cb(msg)
                except Exception:
                    pass

        if "/" in str(repo_name or ""):
            owner, repo_name = resolve_repo_parts(repo_name, default_owner=owner)
        else:
            owner = str(owner or "").strip()
            repo_name = str(repo_name or "").strip()
        if not owner or not repo_name:
            raise GitHubError("Both repository owner and name are required.")

        full_name = f"{owner}/{repo_name}"
        enc_repo = _encode_repo(full_name)

        _progress(f"Checking repository {full_name}...")
        repo_data: dict[str, Any] | None = None
        try:
            repo_data = self.get_repo(full_name)
        except GitHubError as exc:
            if exc.status_code != 404:
                raise

        if repo_data is not None and not push_files:
            resolved_full = str(repo_data.get("full_name") or full_name)
            branch = repo_data.get("default_branch") or "main"
            if not self.repo_has_workflow(resolved_full, WORKFLOW_FILE):
                raise GitHubError(
                    f"Repository '{resolved_full}' exists, but '.github/workflows/{WORKFLOW_FILE}' was not found. "
                    "Push bot files first before using sync-only mode.",
                    status_code=404,
                )

            _progress("Enabling GitHub Actions and workflow...")
            self.enable_actions(resolved_full)
            try:
                self.enable_workflow(resolved_full, WORKFLOW_FILE)
            except GitHubError as exc:
                if exc.status_code == 404:
                    raise GitHubError(
                        f"Workflow '{WORKFLOW_FILE}' could not be enabled in '{resolved_full}'. "
                        f"Verify '.github/workflows/{WORKFLOW_FILE}' exists on '{branch}'.",
                        status_code=404,
                    ) from exc
                raise

            if cron_expr:
                cleaned_cron = " ".join(str(cron_expr).strip().split())
                try:
                    current_cron = self.get_workflow_cron(resolved_full, WORKFLOW_FILE, ref=branch)
                except GitHubError as exc:
                    if exc.status_code == 404:
                        raise GitHubError(
                            f"Repository '{resolved_full}' is missing '.github/workflows/{WORKFLOW_FILE}' on branch '{branch}'.",
                            status_code=404,
                        ) from exc
                    raise
                if current_cron != cleaned_cron:
                    _progress(f"Updating workflow schedule to '{cleaned_cron}'...")
                    self.update_workflow_cron(resolved_full, cleaned_cron, WORKFLOW_FILE, ref=branch)

            return {
                "full_name": resolved_full,
                "private": bool(repo_data.get("private", True)),
                "default_branch": branch,
                "files_pushed": 0,
                "html_url": repo_data.get("html_url", f"https://github.com/{resolved_full}"),
            }

        src_root = resolve_source_dir(source_dir)
        files = _collect_bot_files(src_root, cron_expr=cron_expr)

        if repo_data is None:
            _progress(f"Creating private repository {full_name}...")
            me = self.whoami()
            login = str(me.get("login") or "")
            create_body = {
                "name": repo_name,
                "private": True,
                "auto_init": True,
                "description": "TikTok Daily Streak Automation",
            }
            if owner.lower() == login.lower() or not owner:
                repo_data = self._req("POST", "/user/repos", create_body)
            else:
                repo_data = self._req("POST", f"/orgs/{_encode_seg(owner)}/repos", create_body)

        branch = repo_data.get("default_branch") or "main"
        enc_branch = _encode_rel_path(branch)

        # Wait briefly for auto_init branch ref to materialize.
        ref_data: dict[str, Any] | None = None
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            try:
                ref_data = self._req("GET", f"/repos/{enc_repo}/git/ref/heads/{enc_branch}")
                break
            except GitHubError as exc:
                if exc.status_code not in (404, 409):
                    raise
                time.sleep(1.0)
        if not ref_data:
            raise GitHubError(f"Repository {full_name} branch '{branch}' did not initialize in time.", status_code=408)

        head_commit_sha = ref_data["object"]["sha"]
        head_commit = self._req("GET", f"/repos/{enc_repo}/git/commits/{_encode_seg(head_commit_sha)}")
        base_tree_sha = head_commit["tree"]["sha"]

        _progress(f"Uploading {len(files)} bot files...")
        tree_entries: list[dict[str, str]] = []
        for rel_path, content_bytes in files.items():
            blob = self._req(
                "POST",
                f"/repos/{enc_repo}/git/blobs",
                {
                    "content": base64.b64encode(content_bytes).decode("ascii"),
                    "encoding": "base64",
                },
            )
            tree_entries.append({
                "path": rel_path.replace("\\", "/"),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })

        _progress("Creating git commit...")
        tree = self._req(
            "POST",
            f"/repos/{enc_repo}/git/trees",
            {"base_tree": base_tree_sha, "tree": tree_entries},
        )
        commit = self._req(
            "POST",
            f"/repos/{enc_repo}/git/commits",
            {
                "message": "Deploy TikTok Streak bot",
                "tree": tree["sha"],
                "parents": [head_commit_sha],
            },
        )
        self._req(
            "PATCH",
            f"/repos/{enc_repo}/git/refs/heads/{enc_branch}",
            {"sha": commit["sha"], "force": False},
        )

        _progress("Enabling GitHub Actions and workflow...")
        self.enable_actions(full_name)
        for attempt in range(4):
            try:
                self.enable_workflow(full_name, WORKFLOW_FILE)
                break
            except GitHubError as exc:
                if exc.status_code == 404 and attempt < 3:
                    time.sleep(1.5)
                    continue
                raise

        return {
            "full_name": full_name,
            "private": bool(repo_data.get("private", True)),
            "default_branch": branch,
            "commit_sha": commit["sha"],
            "files_pushed": len(files),
            "html_url": repo_data.get("html_url", f"https://github.com/{full_name}"),
        }

    # -- actions & secrets -----------------------------------------------
    def enable_actions(self, full_name: str) -> dict:
        return self._req(
            "PUT",
            f"/repos/{_encode_repo(full_name)}/actions/permissions",
            {"enabled": True, "allowed_actions": "all"},
        )

    def actions_enabled(self, full_name: str) -> bool:
        try:
            d = self._req("GET", f"/repos/{_encode_repo(full_name)}/actions/permissions")
            return bool(d.get("enabled"))
        except GitHubError:
            return False

    def enable_workflow(self, full_name: str, workflow_file: str = WORKFLOW_FILE) -> None:
        self._req(
            "PUT",
            f"/repos/{_encode_repo(full_name)}/actions/workflows/{_encode_seg(workflow_file)}/enable",
        )

    def secret_key(self, full_name: str) -> dict:
        return self._req("GET", f"/repos/{_encode_repo(full_name)}/actions/secrets/public-key")

    def set_secret(
        self,
        full_name: str,
        name: str,
        value: str,
        public_key: dict | None = None,
    ) -> None:
        from nacl import encoding, public

        key = public_key or self.secret_key(full_name)
        pk = public.PublicKey(key["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed = public.SealedBox(pk).encrypt(str(value).encode("utf-8"))
        enc = base64.b64encode(sealed).decode("ascii")
        self._req(
            "PUT",
            f"/repos/{_encode_repo(full_name)}/actions/secrets/{_encode_seg(name)}",
            {"encrypted_value": enc, "key_id": key["key_id"]},
        )

    def delete_secret(self, full_name: str, name: str) -> bool:
        try:
            self._req(
                "DELETE",
                f"/repos/{_encode_repo(full_name)}/actions/secrets/{_encode_seg(name)}",
            )
            return True
        except GitHubError as exc:
            if exc.status_code == 404:
                return False
            raise

    def list_secrets(self, full_name: str) -> list[str]:
        enc_repo = _encode_repo(full_name)
        names: list[str] = []
        page = 1
        while True:
            d = self._req("GET", f"/repos/{enc_repo}/actions/secrets?per_page=100&page={page}")
            batch = [s["name"] for s in d.get("secrets", []) if s.get("name")]
            names.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return names

    def set_secrets(self, full_name: str, secrets: dict[str, str]) -> list[str]:
        key = self.secret_key(full_name)
        written: list[str] = []
        for name, value in secrets.items():
            if value is None or value == "":
                continue
            self.set_secret(full_name, name, str(value), public_key=key)
            written.append(name)
        return written

    def sync_secrets(
        self,
        full_name: str,
        desired: dict[str, str],
        managed_names: Iterable[str] | None = None,
    ) -> dict[str, list[str]]:
        """Write desired secrets (using one cached public key) and delete stale managed secrets."""
        key = self.secret_key(full_name)
        existing = set(self.list_secrets(full_name))
        managed = set(managed_names) if managed_names is not None else set(SECRET_NAMES)

        active_desired = {
            str(k): str(v)
            for k, v in desired.items()
            if v is not None and str(v) != ""
        }

        written: list[str] = []
        for name, val in active_desired.items():
            self.set_secret(full_name, name, val, public_key=key)
            written.append(name)

        deleted: list[str] = []
        for stale in sorted((existing & managed) - set(active_desired.keys())):
            if self.delete_secret(full_name, stale):
                deleted.append(stale)

        return {"written": written, "deleted": deleted}

    # -- workflow schedule via Contents API ------------------------------
    def get_workflow_cron(
        self,
        full_name: str,
        workflow_file: str = WORKFLOW_FILE,
        ref: str | None = None,
    ) -> str:
        enc_repo = _encode_repo(full_name)
        wf_path = _encode_rel_path(f".github/workflows/{workflow_file}")
        query = f"?ref={_encode_seg(ref)}" if ref else ""
        data = self._req("GET", f"/repos/{enc_repo}/contents/{wf_path}{query}")
        content_b64 = data.get("content") or ""
        if not content_b64:
            return ""
        raw_yaml = base64.b64decode(content_b64).decode("utf-8", "replace")
        return _extract_workflow_cron_text(raw_yaml)

    def update_workflow_cron(
        self,
        full_name: str,
        cron_expr: str,
        workflow_file: str = WORKFLOW_FILE,
        ref: str | None = None,
    ) -> dict:
        enc_repo = _encode_repo(full_name)
        wf_path = _encode_rel_path(f".github/workflows/{workflow_file}")
        branch = ref or self.default_branch(full_name)
        current = self._req("GET", f"/repos/{enc_repo}/contents/{wf_path}?ref={_encode_seg(branch)}")
        raw_yaml = base64.b64decode(current["content"]).decode("utf-8", "replace")
        updated_yaml = _rewrite_workflow_cron_text(raw_yaml, cron_expr)
        return self._req(
            "PUT",
            f"/repos/{enc_repo}/contents/{wf_path}",
            {
                "message": f"Update streak schedule to {cron_expr}",
                "content": base64.b64encode(updated_yaml.encode("utf-8")).decode("ascii"),
                "sha": current["sha"],
                "branch": branch,
            },
        )

    # -- workflow runs, logs & artifacts ---------------------------------
    def dispatch(
        self,
        full_name: str,
        workflow_file: str = WORKFLOW_FILE,
        ref: str | None = None,
    ) -> None:
        branch = ref or self.default_branch(full_name)
        self._req(
            "POST",
            f"/repos/{_encode_repo(full_name)}/actions/workflows/{_encode_seg(workflow_file)}/dispatches",
            {"ref": branch},
        )

    def runs(
        self,
        full_name: str,
        workflow_file: str = WORKFLOW_FILE,
        limit: int = 5,
    ) -> list[dict]:
        d = self._req(
            "GET",
            f"/repos/{_encode_repo(full_name)}/actions/workflows/{_encode_seg(workflow_file)}/runs?per_page={int(limit)}",
        )
        return d.get("workflow_runs", [])

    def latest_run(self, full_name: str, workflow_file: str = WORKFLOW_FILE) -> dict:
        runs = self.runs(full_name, workflow_file, 1)
        return runs[0] if runs else {}

    def dispatch_and_track(
        self,
        full_name: str,
        workflow_file: str = WORKFLOW_FILE,
        ref: str | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Dispatch workflow and poll until the newly created run appears."""
        branch = ref or self.default_branch(full_name)
        prev = self.runs(full_name, workflow_file, limit=1)
        prev_id = prev[0].get("id") if prev else None
        floor_dt = datetime.now(timezone.utc) - timedelta(seconds=5)

        self.dispatch(full_name, workflow_file, branch)

        deadline = time.time() + max(3.0, float(timeout))
        while time.time() < deadline:
            candidates = self.runs(full_name, workflow_file, limit=5)
            for run in candidates:
                rid = run.get("id")
                if prev_id is not None and rid == prev_id:
                    continue
                created_s = run.get("created_at") or ""
                if created_s:
                    try:
                        created_dt = datetime.fromisoformat(created_s.replace("Z", "+00:00"))
                        if created_dt < floor_dt and prev_id is None:
                            continue
                    except ValueError:
                        pass
                if prev_id is None or (rid is not None and rid > prev_id) or run.get("event") == "workflow_dispatch":
                    return run
            time.sleep(1.5)

        raise GitHubError(
            f"Workflow '{workflow_file}' was dispatched, but the new run did not appear within {int(timeout)}s.",
            status_code=408,
        )

    def get_run_jobs(self, full_name: str, run_id: int) -> list[dict]:
        d = self._req(
            "GET",
            f"/repos/{_encode_repo(full_name)}/actions/runs/{int(run_id)}/jobs?per_page=100",
        )
        return d.get("jobs", [])

    def get_job_logs(self, full_name: str, job_id: int) -> str:
        raw, _hdrs, _status = self._req_raw(
            "GET",
            f"/repos/{_encode_repo(full_name)}/actions/jobs/{int(job_id)}/logs",
            follow_cross_host_auth_strip=True,
        )
        return raw.decode("utf-8", "replace")

    def download_diagnostics_artifact(
        self,
        full_name: str,
        run_id: int,
        dest_dir: str | Path,
        artifact_name: str = "debug-diagnostics",
    ) -> list[Path]:
        """Download and extract `debug_*.png` / `debug_*.html` from a run's diagnostics artifact."""
        enc_repo = _encode_repo(full_name)
        d = self._req("GET", f"/repos/{enc_repo}/actions/runs/{int(run_id)}/artifacts")
        artifacts = [a for a in d.get("artifacts", []) if not a.get("expired")]
        target = next((a for a in artifacts if a.get("name") == artifact_name), None)
        if target is None:
            raise GitHubError(
                f"No active '{artifact_name}' artifact found for run #{run_id}.",
                status_code=404,
            )

        raw_zip, _hdrs, _status = self._req_raw(
            "GET",
            f"/repos/{enc_repo}/actions/artifacts/{int(target['id'])}/zip",
            follow_cross_host_auth_strip=True,
        )

        out_dir = Path(dest_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                base = os.path.basename(member.filename.replace("\\", "/"))
                if not base:
                    continue
                if not (fnmatch.fnmatch(base, "debug_*.png") or fnmatch.fnmatch(base, "debug_*.html")):
                    continue
                target_path = (out_dir / base).resolve()
                if out_dir.resolve() not in target_path.parents:
                    continue
                target_path.write_bytes(zf.read(member))
                extracted.append(target_path)
        return extracted


# --------------------------------------------------------------------------
# Device Flow OAuth
# --------------------------------------------------------------------------

def device_flow_start() -> dict[str, Any]:
    """Start GitHub Device Flow OAuth (`repo workflow` scopes)."""
    if not OAUTH_CLIENT_ID:
        raise GitHubError("Device Flow is not configured (no OAuth client id).")
    req = urllib.request.Request(
        DEVICE_CODE_URL,
        data=urllib.parse.urlencode({
            "client_id": OAUTH_CLIENT_ID,
            "scope": "repo workflow",
        }).encode("utf-8"),
        headers={"Accept": "application/json", "User-Agent": "tiktok-streak-gui"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise GitHubError(f"Device flow start failed -> {e.code}: {detail[:300]}", status_code=e.code) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Device flow start network error: {e.reason}") from e
    except (TimeoutError, json.JSONDecodeError) as e:
        raise GitHubError(f"Device flow start failed: {e}") from e

    if not d.get("device_code"):
        raise GitHubError(f"Device flow start failed: {d.get('error_description') or d}")
    return d


def device_flow_poll(
    device_code: str,
    interval: int = 5,
    timeout: int | None = None,
    expires_in: int | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Poll GitHub Device Flow until authorized, expired, denied, or cancelled."""
    if not OAUTH_CLIENT_ID:
        raise GitHubError("Device Flow is not configured (no OAuth client id).")

    wait_sec = max(1, int(interval or 5))
    limits = [int(v) for v in (timeout, expires_in) if v is not None and int(v) > 0]
    effective_timeout = min(limits) if limits else 600
    end = time.time() + effective_timeout

    def _sleep_cancelable(seconds: float) -> bool:
        if cancel_event is not None:
            return cancel_event.wait(seconds)
        time.sleep(seconds)
        return False

    while time.time() < end:
        if cancel_event is not None and cancel_event.is_set():
            raise GitHubError("Device flow authorization cancelled.")

        req = urllib.request.Request(
            TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": OAUTH_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode("utf-8"),
            headers={"Accept": "application/json", "User-Agent": "tiktok-streak-gui"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise GitHubError(f"Device flow poll failed -> {e.code}: {detail[:300]}", status_code=e.code) from e
        except urllib.error.URLError as e:
            raise GitHubError(f"Device flow poll network error: {e.reason}") from e
        except (TimeoutError, json.JSONDecodeError) as e:
            raise GitHubError(f"Device flow poll failed: {e}") from e

        if d.get("access_token"):
            return str(d["access_token"])

        err = d.get("error")
        if err == "authorization_pending":
            if _sleep_cancelable(min(wait_sec, max(0.1, end - time.time()))):
                raise GitHubError("Device flow authorization cancelled.")
            continue
        if err == "slow_down":
            wait_sec = max(wait_sec + 5, int(d.get("interval") or 0))
            if _sleep_cancelable(min(wait_sec, max(0.1, end - time.time()))):
                raise GitHubError("Device flow authorization cancelled.")
            continue
        if err in ("expired_token", "access_denied"):
            raise GitHubError(f"Device flow error ({err}): {d.get('error_description') or err}")
        raise GitHubError(f"Device flow error: {d.get('error_description') or err or d}")

    raise GitHubError("Device flow timed out (expired_token).")


# --------------------------------------------------------------------------
# Telegram setup helpers
# --------------------------------------------------------------------------

def telegram_find_chat_id(token: str) -> dict[str, Any]:
    """Read recent Telegram bot updates (`offset=-1`), reporting 409 webhook conflicts."""
    check = validate_telegram_token(token)
    if not check["valid"]:
        return {"ok": False, "error": check["error"]}
    clean_token = check["token"]

    url = f"https://api.telegram.org/bot{clean_token}/getUpdates?offset=-1&limit=10"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        desc = body
        try:
            desc = json.loads(body).get("description", body)
        except Exception:
            pass
        if exc.code == 409:
            return {
                "ok": False,
                "webhook_conflict": True,
                "status_code": 409,
                "error": f"Telegram 409 conflict (active webhook): {desc}",
            }
        return {
            "ok": False,
            "status_code": exc.code,
            "error": f"Telegram HTTP {exc.code}: {desc}",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not data.get("ok"):
        return {"ok": False, "error": data.get("description", "getUpdates failed")}

    ids: list[str] = []
    for upd in data.get("result", []):
        msg = (
            upd.get("message")
            or upd.get("edited_message")
            or upd.get("channel_post")
            or upd.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            ids.append(str(chat["id"]))

    if not ids:
        return {
            "ok": False,
            "error": "No messages yet. Open the bot in Telegram, press Start, then retry.",
        }
    return {"ok": True, "chat_id": ids[-1], "all": sorted(set(ids))}


def telegram_test(token: str, chat_id: str = "") -> dict[str, Any]:
    """Send a test Telegram message to verify bot token + chat id."""
    check = validate_telegram_token(token)
    if not check["valid"]:
        return {"ok": False, "error": check["error"]}
    clean_token = check["token"]

    cid = str(chat_id or "").strip()
    if not cid:
        got = telegram_find_chat_id(clean_token)
        if not got.get("ok"):
            return got
        cid = got["chat_id"]

    payload = urllib.parse.urlencode({
        "chat_id": cid,
        "text": "TikTok Streak Bot is connected ✅",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{clean_token}/sendMessage",
            data=payload,
            timeout=15,
        ) as r:
            resp = json.loads(r.read().decode("utf-8", "ignore"))
            ok = bool(resp.get("ok"))
            return {"ok": ok, "chat_id": cid, "error": None if ok else resp.get("description", "sendMessage failed")}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        desc = body
        try:
            desc = json.loads(body).get("description", body)
        except Exception:
            pass
        return {"ok": False, "chat_id": cid, "status_code": exc.code, "error": f"Telegram HTTP {exc.code}: {desc}"}
    except Exception as exc:
        return {"ok": False, "chat_id": cid, "error": str(exc)}


__all__ = [
    "API",
    "DEFAULT_SOURCE_REPO",
    "DEVICE_CODE_URL",
    "GitHub",
    "GitHubError",
    "OAUTH_CLIENT_ID",
    "REQUIRED_OAUTH_SCOPES",
    "SECRET_NAMES",
    "TOKEN_URL",
    "WORKFLOW_FILE",
    "device_flow_poll",
    "device_flow_start",
    "has_required_scopes",
    "resolve_repo_parts",
    "resolve_source_dir",
    "telegram_find_chat_id",
    "telegram_test",
]
