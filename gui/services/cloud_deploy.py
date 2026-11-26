"""Pure-Python GitHub Cloud deployment, repository validation, and inline preflight service.

Zero Qt imports so it can be unit-tested headlessly and shared cleanly between
AutomationPage, ContentPage (backward re-exports), and background workers.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Iterable

import gui.services.github_api as github_api
from gui.services.github_api import (
    WORKFLOW_FILE,
    GitHub,
    GitHubError,
    SECRET_NAMES,
    has_required_scopes,
    resolve_source_dir,
)
from gui.services.i18n import tr
from gui.services.local_runner import _resolve_asset_path
from gui.services.storage import (
    AppState,
    DEFAULT_PHOTO_PATH,
    DEFAULT_VIDEO_URL,
    SecretStore,
)

try:
    from gui.services.tiktok_auth import check_cloud_cookie_readiness
except ImportError:  # pragma: no cover
    from gui.services.tiktok_auth import extract_session_summary

    def check_cloud_cookie_readiness(raw_or_dict):  # type: ignore[misc]
        summary = extract_session_summary(raw_or_dict)
        cookies = summary.get("cookies") or {}
        has_regional = any(bool(cookies.get(k)) for k in ("tt-target-idc", "store-idc"))
        sessionid = summary.get("sessionid") or ""
        count = int(summary.get("count") or len(cookies))
        return {
            "cloud_ready": bool(sessionid and has_regional),
            "is_bare_session": bool(sessionid and not has_regional and count <= 4),
            "missing_regional": [k for k in ("tt-target-idc", "store-idc") if not cookies.get(k)],
            "count": count,
        }

from gui.services.validators import (
    prepare_photo_secret,
    validate_repo_name,
    validate_video_url,
)


def _parse_repo_input(raw: str, default_owner: str = "") -> dict[str, Any]:
    """Validate and split ``repo`` or ``owner/repo`` into structured parts."""
    cleaned = str(raw or "").strip().strip("/")
    if not cleaned:
        res = validate_repo_name("")
        res["owner"] = default_owner.strip()
        res["full_name"] = ""
        return res

    resolver = getattr(github_api, "resolve_repo_parts", None)
    if "/" in cleaned:
        if callable(resolver):
            try:
                owner_part, repo_part = resolver(cleaned, default_owner)
            except Exception as exc:
                return {
                    "valid": False,
                    "ok": False,
                    "owner": "",
                    "name": cleaned,
                    "full_name": "",
                    "error": str(exc),
                }
        else:
            parts = [p.strip() for p in cleaned.split("/")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return {
                    "valid": False,
                    "ok": False,
                    "owner": "",
                    "name": cleaned,
                    "full_name": "",
                    "error": "Use 'repo-name' or 'owner/repo-name'.",
                }
            owner_part, repo_part = parts[0], parts[1]

        owner_chk = validate_repo_name(owner_part)
        if not owner_chk["valid"]:
            return {
                "valid": False,
                "ok": False,
                "owner": owner_part,
                "name": repo_part,
                "full_name": "",
                "error": owner_chk.get("error"),
            }
        repo_chk = validate_repo_name(repo_part)
        if not repo_chk["valid"]:
            return {
                "valid": False,
                "ok": False,
                "owner": owner_part,
                "name": repo_part,
                "full_name": "",
                "error": repo_chk.get("error"),
            }
        return {
            "valid": True,
            "ok": True,
            "owner": owner_part,
            "name": repo_part,
            "full_name": f"{owner_part}/{repo_part}",
            "error": None,
        }

    repo_chk = validate_repo_name(cleaned)
    owner_part = default_owner.strip()
    repo_chk["owner"] = owner_part
    repo_chk["full_name"] = f"{owner_part}/{cleaned}" if owner_part and repo_chk["valid"] else cleaned
    return repo_chk


def _validate_pat_task(token: str) -> dict[str, Any]:
    gh = GitHub(token.strip())
    me = gh.whoami()
    return {"token": token.strip(), "user": me}


def _discover_bot_repos_task(token: str) -> dict[str, Any]:
    gh = GitHub(token.strip())
    return gh.discover_bot_repos(limit=100, workflow_file=WORKFLOW_FILE)


def _toggle_bot_repo_workflow_task(token: str, full_name: str, pause: bool) -> dict[str, Any]:
    gh = GitHub(token.strip())
    if pause:
        res = gh.pause_workflow(full_name, WORKFLOW_FILE, cancel_running=True)
        return {
            "full_name": full_name,
            "active": False,
            "workflow_state": "disabled_manually",
            "cancelled_run_ids": list(res.get("cancelled_run_ids") or []),
        }
    gh.enable_workflow(full_name, WORKFLOW_FILE)
    return {
        "full_name": full_name,
        "active": True,
        "workflow_state": "active",
        "cancelled_run_ids": [],
    }


def _pause_multiple_bot_repos_task(token: str, repo_names: list[str]) -> dict[str, Any]:
    gh = GitHub(token.strip())
    paused: list[str] = []
    for full_name in repo_names:
        clean = str(full_name or "").strip()
        if not clean:
            continue
        gh.pause_workflow(clean, WORKFLOW_FILE, cancel_running=True)
        paused.append(clean)
    return {"paused": paused}


def _build_cloud_secrets(state: AppState, secret_store: SecretStore) -> dict[str, str]:
    is_multi = bool(state.multi_account)
    active = list(state.accounts) if is_multi else ([state.accounts[0]] if state.accounts else [])
    if not active:
        raise ValueError("Configure at least one TikTok account first.")

    eff_video_url = (state.video_url or DEFAULT_VIDEO_URL).strip() if state.video_enabled else ""
    eff_share_times = max(1, int(state.share_times)) if state.video_enabled else 0

    desired: dict[str, str] = {
        "MESSAGE": state.message or "🔥 Daily Streak",
        "STREAK_VIDEO_URL": eff_video_url if state.video_enabled else "none",
        "SHARE_TIMES": str(eff_share_times) if state.video_enabled else "0",
    }
    if not state.video_enabled:
        desired["DISABLE_VIDEO"] = "true"

    if not state.photo_enabled:
        desired["DISABLE_PHOTO"] = "true"
        desired["PHOTO_PATH"] = "none"
    elif state.photo_path.strip():
        resolved_photo = _resolve_asset_path(state.photo_path.strip())
        desired["PHOTO_DATA_URL"] = prepare_photo_secret(resolved_photo)

    if int(state.daily_cap) > 0:
        desired["DAILY_CAP"] = str(int(state.daily_cap))

    if state.telegram_enabled:
        tg_tok = secret_store.get_telegram_token().strip()
        tg_chat = state.telegram_chat_id.strip()
        if tg_tok and tg_chat:
            desired["TELEGRAM_BOT_TOKEN"] = tg_tok
            desired["TELEGRAM_CHAT_ID"] = tg_chat

    if is_multi:
        payload: list[dict[str, Any]] = []
        for acc in active:
            cookie = secret_store.get_account_cookie(acc.id).strip()
            if not cookie:
                raise ValueError(f"Account '{acc.name}' is missing a TikTok session cookie.")
            if not acc.friends:
                raise ValueError(f"Account '{acc.name}' has no target friends configured.")

            item: dict[str, Any] = {
                "name": acc.name or "Account",
                "session_id": cookie,
                "friends": list(acc.friends),
                "message": (
                    acc.message_override
                    if acc.message_override is not None
                    else (state.message or "🔥 Daily Streak")
                ),
            }
            if acc.video_url_override is not None:
                ov_v = acc.video_url_override.strip()
                item["video_url"] = (
                    "none" if (not ov_v or ov_v.lower() in ("none", "off", "disabled")) else ov_v
                )
            else:
                item["video_url"] = eff_video_url if state.video_enabled else "none"

            if acc.share_times_override is not None:
                item["share_times"] = max(0, int(acc.share_times_override))
            else:
                item["share_times"] = eff_share_times

            if acc.photo_path_override is not None:
                ov_p = acc.photo_path_override.strip()
                if not ov_p or ov_p.lower() in ("none", "off", "disabled"):
                    item["photo_path"] = "none"
                elif ov_p == DEFAULT_PHOTO_PATH:
                    item["photo_path"] = DEFAULT_PHOTO_PATH
            elif not state.photo_enabled:
                item["photo_path"] = "none"

            payload.append(item)
        desired["TIKTOK_ACCOUNTS_JSON"] = json.dumps(payload, ensure_ascii=False)
    else:
        primary = active[0]
        primary_cookie = secret_store.get_account_cookie(primary.id).strip()
        if not primary_cookie:
            raise ValueError(f"Account '{primary.name}' is missing a TikTok session cookie.")
        if not primary.friends:
            raise ValueError(f"Account '{primary.name}' has no target friends configured.")
        desired["TIKTOK_SESSION_ID"] = primary_cookie
        desired["FRIENDS_LIST"] = ",".join(primary.friends)

    return desired


def _deploy_github_task(
    token: str,
    repo_name: str,
    is_private: bool,
    source_repo: str,
    cron_expr: str,
    desired_secrets: dict[str, str],
    log_cb: Callable[[str], None] | None = None,
    sync_only: bool = False,
    pause_repos: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    gh = GitHub(token)
    _log("Verifying GitHub token and scopes...")
    me = gh.whoami()
    if me.get("scopes_header_present") and not has_required_scopes(me.get("scopes") or []):
        raise GitHubError("Token is missing required 'repo' and 'workflow' scopes.", status_code=403)

    login = str(me.get("login") or "").strip()
    if not login:
        raise GitHubError("Could not determine GitHub username.", status_code=401)

    parsed = _parse_repo_input(repo_name, default_owner=login)
    if not parsed["valid"]:
        raise GitHubError(str(parsed.get("error") or "Invalid repository name."), status_code=422)
    owner_part = parsed["owner"] or login
    repo_part = parsed["name"]
    full_name = f"{owner_part}/{repo_part}"

    for old_repo in pause_repos or ():
        cleaned_old = str(old_repo or "").strip()
        if not cleaned_old or cleaned_old.lower() == full_name.lower():
            continue
        _log(f"Pausing extra bot schedule in {cleaned_old}...")
        try:
            gh.pause_workflow(cleaned_old, WORKFLOW_FILE, cancel_running=True)
        except GitHubError as exc:
            _log(f"Note pausing {cleaned_old}: {exc}")

    if is_private:
        create_kwargs: dict[str, Any] = {
            "owner": owner_part,
            "repo_name": repo_part,
            "source_dir": resolve_source_dir(),
            "cron_expr": cron_expr,
            "progress_cb": _log,
        }
        if sync_only:
            try:
                sig = inspect.signature(gh.create_private_repo_with_bot)
                if "push_files" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    create_kwargs["push_files"] = False
            except (TypeError, ValueError):
                pass
        repo_info = gh.create_private_repo_with_bot(**create_kwargs)
        html_url = repo_info.get("html_url", f"https://github.com/{full_name}")
    else:
        _log(f"Checking public repository {full_name}...")
        exists = False
        try:
            repo_info = gh.get_repo(full_name)
            exists = True
        except GitHubError as exc:
            if exc.status_code != 404:
                raise
            repo_info = {}

        if not exists:
            _log(f"Forking {source_repo} -> {full_name}...")
            fork_kwargs: dict[str, Any] = {"name": repo_part, "wait_ready": True}
            if owner_part.lower() != login.lower():
                fork_kwargs["org"] = owner_part
            repo_info = gh.fork(source_repo, **fork_kwargs)
        elif not sync_only:
            _log(f"Syncing upstream changes into {full_name}...")
            try:
                gh.merge_upstream(full_name)
            except GitHubError as exc:
                _log(f"Upstream sync note: {exc}")
        else:
            _log(f"Keeping existing repository code for {full_name} (sync-only mode)...")

        _log("Enabling GitHub Actions and workflow...")
        gh.enable_actions(full_name)
        gh.enable_workflow(full_name, WORKFLOW_FILE)

        try:
            current_cron = gh.get_workflow_cron(full_name, WORKFLOW_FILE)
        except GitHubError:
            current_cron = ""
        if current_cron != cron_expr:
            _log(f"Updating workflow schedule to '{cron_expr}'...")
            gh.update_workflow_cron(full_name, cron_expr, WORKFLOW_FILE)
        html_url = repo_info.get("html_url", f"https://github.com/{full_name}")

    _log(f"Syncing {len(desired_secrets)} GitHub secrets (removing stale managed secrets)...")
    sync_res = gh.sync_secrets(full_name, desired_secrets, managed_names=SECRET_NAMES)
    _log(
        f"Deploy complete: {full_name} "
        f"(written={len(sync_res['written'])}, deleted={len(sync_res['deleted'])})"
    )
    return {
        "ok": True,
        "login": login,
        "full_name": full_name,
        "html_url": html_url,
        "written": sync_res["written"],
        "deleted": sync_res["deleted"],
        "sync_only": bool(sync_only),
    }


def evaluate_deploy_preflight(
    state: AppState,
    secret_store: SecretStore,
    raw_repo_text: str,
    *,
    discovered_bot_repos: list[dict[str, Any]] | None = None,
    discovered_all_repos: Iterable[str] | dict[str, Any] | None = None,
    sync_only: bool = False,
    allow_existing_non_bot: bool = False,
) -> dict[str, Any]:
    """Evaluate TikTok Session, Target Repo, and Active Schedule conflicts inline.

    Replaces the 3 sequential late deploy confirmation popups with live,
    deterministic status checks and 1-click inline fixes.
    """
    bot_repos = list(discovered_bot_repos or [])
    if isinstance(discovered_all_repos, dict):
        all_repos_low = {str(k).strip().lower() for k in discovered_all_repos.keys() if str(k).strip()}
    else:
        all_repos_low = {str(x).strip().lower() for x in (discovered_all_repos or ()) if str(x).strip()}

    has_blocking = False
    blocking_reasons: list[str] = []
    warnings: list[dict[str, Any]] = []

    # 1. TikTok Session check
    active_accounts = (
        list(state.accounts)
        if state.multi_account
        else ([state.accounts[0]] if state.accounts else [])
    )
    missing_cookie_names: list[str] = []
    missing_friends_names: list[str] = []
    unready_names: list[str] = []

    for acc in active_accounts:
        label = acc.name or acc.username or "Main"
        cookie = secret_store.get_account_cookie(acc.id).strip()
        if not cookie:
            missing_cookie_names.append(label)
            continue
        if not acc.friends:
            missing_friends_names.append(label)
        if not check_cloud_cookie_readiness(cookie).get("cloud_ready", False):
            unready_names.append(label)

    if not active_accounts or missing_cookie_names:
        has_blocking = True
        names_str = ", ".join(missing_cookie_names) if missing_cookie_names else "Main"
        detail = tr("automation.preflight_session_missing", accounts=names_str)
        blocking_reasons.append(detail)
        session_check: dict[str, Any] = {
            "id": "session",
            "status": "error",
            "blocking": True,
            "title": tr("automation.preflight_session_title"),
            "detail": detail,
            "fix": "browser_login",
            "fix_label": tr("accounts.sign_in_browser"),
            "unready_accounts": list(missing_cookie_names),
        }
    elif missing_friends_names:
        has_blocking = True
        names_str = ", ".join(missing_friends_names)
        detail = tr("automation.preflight_friends_missing", accounts=names_str)
        blocking_reasons.append(detail)
        session_check = {
            "id": "session",
            "status": "error",
            "blocking": True,
            "title": tr("automation.preflight_session_title"),
            "detail": detail,
            "fix": "browser_login",
            "fix_label": tr("nav.accounts"),
            "unready_accounts": list(missing_friends_names),
        }
    elif unready_names:
        names_str = ", ".join(unready_names)
        detail = tr("automation.preflight_session_bare", accounts=names_str)
        session_check = {
            "id": "session",
            "status": "error",
            "blocking": False,
            "title": tr("automation.preflight_session_title"),
            "detail": detail,
            "fix": "browser_login",
            "fix_label": tr("accounts.sign_in_browser"),
            "unready_accounts": list(unready_names),
        }
        warnings.append(
            {
                "id": "bare_session",
                "text": detail,
                "accounts": list(unready_names),
            }
        )
    else:
        primary_lbl = active_accounts[0].name or "Main"
        if len(active_accounts) == 1:
            detail = tr("automation.preflight_session_ok_one", account=primary_lbl)
        else:
            detail = tr("automation.preflight_session_ok_multi", count=len(active_accounts))
        session_check = {
            "id": "session",
            "status": "pass",
            "blocking": False,
            "title": tr("automation.preflight_session_title"),
            "detail": detail,
            "fix": None,
            "fix_label": "",
            "unready_accounts": [],
        }

    # 2. Target Repo & GitHub Auth check
    has_token = bool(secret_store.get_github_token().strip())
    repo_chk = _parse_repo_input(raw_repo_text, default_owner=state.github_login)
    target_full = str(repo_chk.get("full_name") or "").strip()
    target_full_low = target_full.lower()
    bot_match = next(
        (r for r in bot_repos if str(r.get("full_name") or "").strip().lower() == target_full_low),
        None,
    )

    if not has_token:
        has_blocking = True
        detail = tr("schedule.not_signed_in")
        blocking_reasons.append(detail)
        repo_check: dict[str, Any] = {
            "id": "repo",
            "status": "error",
            "blocking": True,
            "title": tr("automation.preflight_repo_title"),
            "detail": detail,
            "fix": "connect_github",
            "fix_label": tr("cloud.signin_device"),
            "full_name": target_full,
        }
    elif not repo_chk["valid"]:
        has_blocking = True
        detail = str(repo_chk.get("error") or tr("common.warning"))
        blocking_reasons.append(detail)
        repo_check = {
            "id": "repo",
            "status": "error",
            "blocking": True,
            "title": tr("automation.preflight_repo_title"),
            "detail": detail,
            "fix": None,
            "fix_label": "",
            "full_name": target_full,
        }
    elif (
        not sync_only
        and target_full_low
        and target_full_low in all_repos_low
        and bot_match is None
    ):
        if allow_existing_non_bot:
            repo_check = {
                "id": "repo",
                "status": "pass",
                "blocking": False,
                "title": tr("automation.preflight_repo_title"),
                "detail": tr("automation.preflight_repo_allowed", repo=target_full),
                "fix": None,
                "fix_label": "",
                "full_name": target_full,
            }
        else:
            detail = tr("automation.preflight_repo_non_bot", repo=target_full)
            repo_check = {
                "id": "repo",
                "status": "warn",
                "blocking": False,
                "title": tr("automation.preflight_repo_title"),
                "detail": detail,
                "fix": "allow_existing",
                "fix_label": tr("automation.preflight_allow_existing_btn"),
                "full_name": target_full,
            }
            warnings.append(
                {
                    "id": "non_bot_repo",
                    "text": detail,
                    "repo": target_full,
                }
            )
    else:
        if bot_match is not None:
            detail = (
                tr("automation.preflight_repo_active_bot")
                if bot_match.get("active")
                else tr("automation.preflight_repo_paused_bot")
            )
        else:
            detail = tr("automation.preflight_repo_new")
        repo_check = {
            "id": "repo",
            "status": "pass",
            "blocking": False,
            "title": tr("automation.preflight_repo_title"),
            "detail": detail,
            "fix": None,
            "fix_label": "",
            "full_name": target_full,
        }

    # 3. Duplicate Active Schedules check
    other_active = [
        str(r.get("full_name") or "").strip()
        for r in bot_repos
        if r.get("active")
        and str(r.get("full_name") or "").strip()
        and str(r.get("full_name") or "").strip().lower() != target_full_low
    ]
    if other_active:
        short_names = ", ".join(name.split("/")[-1] for name in other_active[:2])
        if len(other_active) > 2:
            short_names += f" +{len(other_active) - 2}"
        detail = tr("automation.preflight_dups_warn", repos=short_names)
        dups_check: dict[str, Any] = {
            "id": "dups",
            "status": "warn",
            "blocking": False,
            "title": tr("automation.preflight_dups_title"),
            "detail": detail,
            "fix": "pause_others",
            "fix_label": tr("automation.preflight_pause_old_btn"),
            "other_active": list(other_active),
        }
        warnings.append(
            {
                "id": "duplicate_bots",
                "text": tr("automation.preflight_dups_modal", repos=", ".join(other_active)),
                "other_active": list(other_active),
            }
        )
    else:
        dups_check = {
            "id": "dups",
            "status": "pass",
            "blocking": False,
            "title": tr("automation.preflight_dups_title"),
            "detail": tr("automation.preflight_dups_ok"),
            "fix": None,
            "fix_label": "",
            "other_active": [],
        }

    # Video URL sanity check (hard blocker if enabled and malformed)
    video_error = ""
    if state.video_enabled:
        vchk = validate_video_url(state.video_url, resolve_short=False)
        if not vchk.get("valid"):
            has_blocking = True
            video_error = str(vchk.get("error") or tr("common.warning"))
            blocking_reasons.append(video_error)

    checks = [session_check, repo_check, dups_check]
    issue_count = sum(1 for c in checks if c["status"] != "pass")

    return {
        "ready": not has_blocking and issue_count == 0,
        "has_blocking": has_blocking,
        "blocking_reasons": blocking_reasons,
        "video_error": video_error,
        "issue_count": issue_count,
        "checks": checks,
        "warnings": warnings,
        "other_active": other_active,
        "repo_parsed": repo_chk,
    }


__all__ = [
    "_build_cloud_secrets",
    "_deploy_github_task",
    "_discover_bot_repos_task",
    "_parse_repo_input",
    "_pause_multiple_bot_repos_task",
    "_toggle_bot_repo_workflow_task",
    "_validate_pat_task",
    "evaluate_deploy_preflight",
]
