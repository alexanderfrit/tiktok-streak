"""Bilingual internationalization (EN/ID) and user-friendly error translator.

Pure Python with zero Qt UI dependencies so it works across services, workers,
and unit tests.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable

logger = logging.getLogger("tiktok-streak")

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "id")

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})\b"), "<REDACTED_TOKEN>"),
    (re.compile(r"(bot)\d{6,15}:[A-Za-z0-9_-]{20,}"), r"\1<REDACTED_TELEGRAM_TOKEN>"),
    (re.compile(r"\b\d{6,15}:[A-Za-z0-9_-]{20,}\b"), "<REDACTED_TELEGRAM_TOKEN>"),
    (re.compile(r"(?i)(Authorization\s*:\s*(?:Bearer|Token|Basic)\s+)[^\s\r\n]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(Cookie\s*:\s*)[^\r\n]+"), r"\1<REDACTED>"),
    (
        re.compile(r"(?i)\b(sessionid|sessionid_ss|sid_tt|sid_guard|uid_tt|ttwid|passport_csrf_token)\s*=\s*[^;\s\r\n]+"),
        r"\1=<REDACTED>",
    ),
)


def _sanitize_error_text(text: str) -> str:
    cleaned = str(text or "").strip()
    for pat, repl in _REDACT_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    return cleaned


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    low = str(lang).strip().lower().replace("_", "-")
    if low == "id" or low.startswith("id-") or low in ("indo", "indonesia", "bahasa", "bahasa indonesia"):
        return "id"
    if low == "en" or low.startswith("en-") or low == "english":
        return "en"
    return "en"


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # App & Navigation
        "app.title": "TikTok Streak Setup",
        "app.subtitle": "Automated Daily TikTok DM Streaks",
        "nav.dashboard": "Home",
        "nav.accounts": "Accounts",
        "nav.content": "Message & Media",
        "nav.automation": "Automation",
        "nav.schedule": "Schedule & Run",
        "nav.cloud": "Cloud Deploy",
        "nav.diagnostics": "Diagnostics",
        "nav.settings": "Settings",

        # Common actions & status
        "common.save": "Save",
        "common.cancel": "Cancel",
        "common.close": "Close",
        "common.continue": "Continue",
        "common.back": "Back",
        "common.refresh": "Refresh",
        "common.browse": "Browse...",
        "common.clear": "Clear",
        "common.delete": "Delete",
        "common.add": "Add",
        "common.remove": "Remove",
        "common.test": "Test",
        "common.verify": "Verify",
        "common.loading": "Loading...",
        "common.ready": "Ready",
        "common.enabled": "Enabled",
        "common.disabled": "Disabled",
        "common.optional": "Optional",
        "common.unknown": "Unknown",
        "common.success": "Success",
        "common.warning": "Warning",
        "common.error": "Error",
        "common.copied": "Copied!",

        # Dashboard / Home
        "dashboard.welcome": "Home",
        "dashboard.subtitle": "Daily TikTok DM streak automation",
        "dashboard.status_ready": "All Systems Ready",
        "dashboard.status_incomplete": "Complete setup to begin",
        "dashboard.accounts_count": "{count} account(s) configured",
        "dashboard.friends_count": "{count} friend(s) total",
        "dashboard.mode_cloud": "GitHub Cloud",
        "dashboard.mode_local": "Local PC",
        "dashboard.mode_both": "Hybrid (Local + Cloud)",
        "dashboard.run_local_now": "Run All Locally",
        "dashboard.run_cloud_now": "Run on GitHub",
        "dashboard.smoke_test": "Test 1 Friend",
        "dashboard.last_test_ok": "Passed",
        "dashboard.last_test_pending": "Not tested yet",

        # Accounts / TikTok Auth
        "accounts.title": "Accounts",
        "accounts.subtitle": "Connect TikTok and manage each account's target friends.",
        "accounts.multi_toggle": "Multi-account mode",
        "accounts.add_account": "Add Account",
        "accounts.account_name": "Account Name",
        "accounts.scan_browsers": "Scan Installed Browsers",
        "accounts.read_browser": "Import from Selected Browser",
        "accounts.guided_login": "Sign in via Browser",
        "accounts.guided_login_hint": "Connect TikTok and manage each account's target friends.",
        "accounts.guided_login_active": "Sign into TikTok in the opened window, then confirm.",
        "accounts.manual_paste": "Paste Cookie",
        "accounts.manual_paste_hint": "Paste Cookie header, JSON cookie export, or raw sessionid.",
        "accounts.verify_inbox": "Verify Session",
        "accounts.verifying": "Verifying...",
        "accounts.verified": "Verified (@{username})",
        "accounts.inbox_ok": "Web inbox accessible",
        "accounts.inbox_blocked": "Business Suite redirect detected",
        "accounts.not_verified": "Not verified",
        "accounts.local_only_badge": "Local Only",
        "accounts.cloud_ready_badge": "Cloud Ready",
        "accounts.verify_cloud_warn": "Inbox opened locally, but regional routing cookies for US GitHub Cloud runners are missing. Use Sign in via Browser if Cloud runs fail.",
        "accounts.verify_captcha_err": "TikTok showed a security verification challenge in headless mode. Use Sign in via Browser to verify interactively.",
        "accounts.cookies_cloud_ready": "{count} cookies (Cloud ready)",
        "accounts.cookies_local_only": "{count} cookies (Local only)",
        "accounts.browser_locked_badge": "Browser open (locked)",
        "accounts.v20_badge": "v20 protected — use Browser Login or Paste",
        "accounts.no_tiktok_badge": "No TikTok login found",
        "accounts.has_tiktok_badge": "TikTok session found",
        "accounts.browser_disclosure_title": "Browser profile for direct import (Chrome / Edge / Firefox)",
        "accounts.browser_disclosure_tag": "Optional fallback",
        "accounts.saved_count": "{count} account(s) saved",
        "accounts.import_dlg_title": "Import from Browser",
        "accounts.import_dlg_subtitle": "Choose a detected browser profile to import your TikTok session for {account}.",
        "accounts.import_dlg_btn": "Import Session",
        "accounts.import_dlg_no_profiles": "No browser profiles detected. Click Refresh or use Sign in via Browser.",

        # Content / Message & Media
        "content.title": "Message & Media",
        "content.subtitle": "Choose what gets sent to each friend daily.",
        "content.always_sent": "Always sent",
        "content.message_hint": "Sent first to keep your daily flame active.",
        "content.friends_label": "Target Friends",
        "content.friends_placeholder": "Enter TikTok @usernames separated by commas or new lines (or paste profile URLs)",
        "content.import_csv": "Import CSV",
        "content.export_csv": "Export CSV",
        "content.message_label": "Streak Message",
        "content.video_toggle": "Video Share Card",
        "content.video_url_label": "TikTok Video URL",
        "content.video_resolve": "Check Link",
        "content.share_times_label": "Shares per friend",
        "content.photo_toggle": "Photo Card",
        "content.photo_select": "Choose Photo",
        "content.photo_default": "Using built-in default photo",
        "content.photo_custom": "Custom photo: {path}",
        "content.daily_cap_label": "Daily friend cap",
        "content.overrides_title": "Per-Account Overrides",
        "content.next_automation": "Next: Automation",
        "content.dm_preview_title": "DM Preview",
        "content.dm_preview_video": "Video Share ×{count}",
        "content.dm_preview_video_off": "Video share disabled",
        "content.dm_preview_photo_default": "Default image attached",
        "content.dm_preview_photo_custom": "Custom photo: {name}",
        "content.dm_preview_photo_off": "Photo card disabled",
        "content.dm_composer_placeholder": "Send a message...",
        "content.pet_tasks_title": "Streak Pet Tasks",
        "content.pet_pts_badge": "Pet growth: +{pts} pts / day",
        "content.pet_pts_short": "+{pts} pts",
        "content.pet_task_msg": "Text message",
        "content.pet_task_video": "2 shared videos",
        "content.pet_task_photo": "Photo card",
        "content.pet_pt_1": "+1 pt",
        "content.pet_pt_2": "+2 pts",
        "content.pet_pt_4": "+4 pts",

        # Schedule & Execution / Automation
        "schedule.title": "Automation",
        "schedule.mode_label": "Execution Mode",
        "schedule.local_headless": "Run local browser silently in background (headless)",
        "schedule.local_times": "Daily Run Times (WIB / UTC+7)",
        "schedule.local_times_tz": "Daily Run Times ({tz})",
        "schedule.timezone": "Schedule Timezone",
        "schedule.cloud_times": "Cloud Daily Run Times ({tz})",
        "schedule.quick_presets": "Quick presets:",
        "schedule.preset_offpeak_1": "1x (08:15)",
        "schedule.preset_offpeak_2": "2x (08:15, 20:15)",
        "schedule.preset_offpeak_3": "3x",
        "schedule.raw_cron_toggle": "Raw UTC Cron",
        "schedule.cloud_delay_note": "Use :15 or :17 minutes to avoid :00 GitHub queue delays.",
        "schedule.sync_windows": "Sync Tasks",
        "schedule.remove_windows": "Remove",
        "schedule.cloud_cron": "GitHub Actions Cron (UTC)",
        "schedule.cloud_summary": "Cloud schedule: {summary}",
        "schedule.local_tasks_count": "{count} task(s) active",
        "schedule.stop_run": "Stop",
        "schedule.test_friend_prompt": "Select friend for 1-friend test",

        "automation.title": "Automation",
        "automation.subtitle": "Choose where and when daily streaks run.",
        "automation.tz_change": "Change in Settings",
        "automation.mode_desc_local": "Runs daily via Windows Task Scheduler while your PC is on.",
        "automation.mode_desc_cloud": "Runs daily on your private GitHub repo even when your PC is off.",
        "automation.mode_desc_both": "Runs on both Windows Task Scheduler and GitHub Actions.",
        "automation.repo_card_title": "GitHub Repository",
        "automation.local_card_title": "Local PC Schedule (Windows Task Scheduler)",
        "automation.schedule_card_title": "Daily Schedule ({tz})",
        "automation.utc_equivalent": "UTC equivalent: {times}",
        "automation.advanced_toggle": "Advanced (PAT token, secrets-only sync, raw UTC cron)",
        "automation.matches_run_logs": "Matches Run & Logs",
        "automation.preflight_heading": "Pre-Deploy Readiness",
        "automation.preflight_issues_badge": "{count} item(s) to fix",
        "automation.preflight_resolve_hint": "Resolve issues inline above — zero surprise popups on deploy.",
        "automation.preflight_session_title": "TikTok Session",
        "automation.preflight_session_ok_one": "{account} · Cloud ready",
        "automation.preflight_session_ok_multi": "{count} accounts · Cloud ready",
        "automation.preflight_session_missing": "{accounts} missing TikTok session cookie",
        "automation.preflight_friends_missing": "{accounts} has no target friends",
        "automation.preflight_session_bare": "{accounts} missing Cloud routing cookies (bare sessionid)",
        "automation.preflight_repo_title": "Target Repo",
        "automation.preflight_repo_active_bot": "Updates active bot",
        "automation.preflight_repo_paused_bot": "Updates paused bot",
        "automation.preflight_repo_new": "Creates new bot repo",
        "automation.preflight_repo_non_bot": "{repo} exists without bot files",
        "automation.preflight_repo_allowed": "Overwrite allowed ({repo})",
        "automation.preflight_allow_existing_btn": "Allow Existing",
        "automation.preflight_dups_title": "Active Schedules",
        "automation.preflight_dups_ok": "No duplicate bots",
        "automation.preflight_dups_warn": "{repos} is also scheduled (duplicate DMs)",
        "automation.preflight_dups_modal": "{repos} is still active. Keeping both enabled will send duplicate daily DMs.",
        "automation.preflight_pause_old_btn": "Pause Old",
        "automation.deploy_warn_title": "Deploy with {count} Active Warning(s)?",
        "automation.deploy_ready_hint": "Ready to sync encrypted secrets and schedule.",

        # Cloud Deploy & GitHub
        "cloud.title": "GitHub Repository",
        "cloud.signin_device": "Connect GitHub",
        "cloud.signin_pat": "PAT",
        "cloud.signout": "Sign Out",
        "cloud.signed_in_as": "Signed in as @{login}",
        "cloud.device_code_prompt": "Enter code {code} on GitHub to authorize:",
        "cloud.copy_code": "Copy Code",
        "cloud.open_github": "Open GitHub Authorization",
        "cloud.repo_name": "Repository Name",
        "cloud.private_repo": "Private",
        "cloud.public_fork": "Public Fork (from {source})",
        "cloud.deploy": "Deploy to GitHub",
        "cloud.sync_schedule": "Update Cloud Schedule",
        "cloud.check_status": "Check Latest Run",
        "cloud.download_diagnostics": "Screenshots",
        "cloud.view_logs": "View Logs",
        "cloud.telegram_title": "Telegram Alerts",
        "cloud.telegram_enable": "Daily run reports",
        "cloud.telegram_token": "Bot Token",
        "cloud.telegram_chat_id": "Chat ID",
        "cloud.telegram_detect": "Detect",
        "cloud.telegram_test": "Send Test",

        # Diagnostics & Settings
        "diag.title": "Logs & Diagnostics",
        "diag.empty": "No logs recorded yet.",
        "diag.copy_logs": "Copy Redacted Logs",
        "settings.title": "Settings",
        "settings.language": "Language",
        "settings.timezone": "Timezone",
        "settings.theme": "Appearance Theme",
        "settings.theme_system": "System",
        "settings.theme_light": "Light",
        "settings.theme_dark": "Dark",
        "settings.export_workspace": "Export / Import CLI Config",
        "settings.export_workspace_desc": "Writes .env & accounts.json (contains unencrypted cookies for CLI) or imports a CLI folder",
        "settings.import_workspace": "Import Local Workspace",
        "settings.clear_secrets": "Clear Saved Tokens & Cookies",
        "settings.clear_secrets_confirm": "Remove all saved GitHub tokens, Telegram tokens, and TikTok session cookies from secure storage?",

        # Extended Fluent UI keys
        "nav.content_schedule": "Message & Media",
        "nav.run_logs": "Run & Logs",
        "home.checklist_heading": "Setup Checklist",
        "home.checklist_progress": "{done} / {total} complete",
        "home.checklist_hint": "First incomplete step highlights as Current",
        "home.kpi_account_title": "Account & Friends",
        "home.kpi_automation_title": "Automation",
        "home.kpi_lastrun_title": "Last Run",
        "home.go_step": "Go to step",
        "home.step1_title": "1. Account",
        "home.step1_desc": "Connect at least one TikTok account and add target friends.",
        "home.step2_title": "2. Payload",
        "home.step2_desc": "Customize your daily streak message, video, and photo.",
        "home.step3_title": "3. Automation",
        "home.step3_desc": "Choose Local PC schedule, GitHub Cloud, or Hybrid mode.",
        "home.step4_title": "4. Test Run",
        "home.step4_desc": "Run a 1-friend test and optionally enable Telegram alerts.",
        "home.telegram_settings_btn": "Telegram Settings",
        "home.privacy_details_btn": "Privacy Details",
        "home.needs_attention": "Needs setup",
        "home.optional_pending": "Optional — ready",
        "accounts.single_mode": "Single Account",
        "accounts.multi_mode": "Multi-Account",
        "accounts.browser_profile": "Browser Profile (Optional Import)",
        "accounts.sign_in_browser": "Sign in via Browser",
        "accounts.import_browser": "Import from Browser",
        "accounts.paste_cookie": "Paste Cookie",
        "accounts.inbox_verified": "Verified",
        "accounts.inbox_blocked": "Business Suite Blocked",
        "accounts.inbox_expired": "Expired",
        "accounts.inbox_unchecked": "Not Checked",
        "accounts.no_session": "No cookie saved",
        "accounts.friends_count": "{count} friend(s)",
        "accounts.scan_not_run": "Click Refresh to scan browser profiles",
        "accounts.multi_hidden_hint": "Single-account mode uses Account #1 only. Your other {extra} saved account(s) stay preserved if you switch back.",
        "accounts.override_hint": "Unchecked fields inherit global Message & Media settings.",
        "accounts.override_toggle": "Custom message & media overrides",
        "accounts.override_shares": "Shares:",
        "accounts.override_message": "Custom streak message",
        "accounts.override_video": "Custom video link & share count",
        "accounts.override_photo": "Custom photo",
        "accounts.disable_for_account": "Disable for this account",
        "accounts.grab_session_now": "I'm Signed In",
        "cookie_dlg.title": "Paste TikTok Session Cookie",
        "cookie_dlg.step_1": "1. Open tiktok.com",
        "cookie_dlg.step_2": "2. F12 → Cookies",
        "cookie_dlg.step_3": "3. Copy sessionid",
        "cookie_dlg.steps": "1. Open tiktok.com  ·  2. F12 → Cookies  ·  3. Copy sessionid",
        "cookie_dlg.placeholder": "Paste sessionid, full Cookie header, or JSON export...",
        "cookie_dlg.preview_valid": "{count} cookie(s) ({preview})",
        "cookie_dlg.preview_empty": "Waiting for a valid sessionid or cookie payload...",
        "cookie_dlg.invalid_msg": "Please paste a non-empty TikTok sessionid or cookie string.",
        "cookie_dlg.bare_warning": "Bare sessionid only? Cloud runs may need full cookies.",
        "cookie_dlg.use_browser_btn": "Use Browser Login",
        "cookie_dlg.save_btn": "Save Cookie",
        "cloud.bare_deploy_title": "Cookies May Not Work in GitHub Cloud",
        "cloud.bare_deploy_confirm": "These account(s) are missing TikTok regional routing cookies for US GitHub runners: {accounts}.\n\nClick Verify Session on each account or use Sign in via Browser first. Continue deploying anyway?",
        "device_dlg.title": "Connect GitHub Account",
        "device_dlg.step_1": "1. Copy code",
        "device_dlg.step_2": "2. Paste on GitHub",
        "device_dlg.step_3": "3. Done",
        "device_dlg.subtitle": "Copy this one-time code, open GitHub, and paste it to authorize.",
        "device_dlg.copy_open": "Copy Code & Open GitHub",
        "device_dlg.copied_hint": "Code {code} copied! Paste it on the GitHub page.",
        "device_dlg.waiting": "Waiting for GitHub authorization...",
        "device_dlg.authorized": "Authorized as @{login}!",
        "content.video_valid": "ID {aweme_id}",
        "content.video_unverified": "Unverified — click Check Link",
        "content.video_verified_author": "Verified (@{author})",
        "content.video_unavailable": "Unavailable",
        "content.video_verify_offline": "Could not verify online",
        "content.video_disabled": "Video sharing disabled",
        "content.video_hint": "Supports full URLs or vt.tiktok.com links.",
        "content.photo_use_default": "Use Default Photo",
        "content.photo_reset": "Reset to Default",
        "content.photo_disabled": "Photo sending disabled",
        "content.repo_valid": "Valid repository name",
        "cloud.fetch_repos": "Fetch My Repos",
        "cloud.select_existing_repo": "Choose existing repo...",
        "cloud.repo_private_tag": "Private",
        "cloud.repo_public_tag": "Public",
        "cloud.repo_name_hint": "tiktok-streak or owner/repo-name",
        "cloud.sync_only": "Sync secrets & schedule only (keep repo commits)",
        "cloud.sync_only_hint": "Keep existing repository code without overwriting commits.",
        "cloud.pat_toggle": "Use Personal Access Token (PAT)",
        "cloud.reconnect_github": "Switch Account",
        "schedule.mode_local": "Local PC",
        "schedule.mode_cloud": "GitHub Cloud",
        "schedule.mode_both": "Hybrid",
        "schedule.preset_1": "08:00 WIB (Once daily)",
        "schedule.preset_2": "08:00 & 20:00 WIB (Twice daily)",
        "schedule.preset_3": "08:00, 14:00 & 20:00 WIB (3x daily)",
        "schedule.preset_custom": "Custom UTC Cron",
        "schedule.custom_cron": "UTC Cron",
        "schedule.add_time": "Add Time",
        "schedule.deploy_btn": "Deploy to GitHub",
        "schedule.not_signed_in": "Not signed in to GitHub",
        "schedule.pat_placeholder": "ghp_... (repo + workflow scopes)",
        "schedule.save_pat": "Save PAT",
        "run.title": "Run & Logs",
        "run.subtitle": "Test instantly, monitor Cloud runs, and inspect live logs side by side.",
        "run.local_title": "Local Run",
        "run.quick_test": "Test 1 Friend",
        "run.full_run": "Run All",
        "run.stop_short": "Stop",
        "run.show_browser": "Show browser window",
        "run.cloud_title": "GitHub Cloud",
        "run.cloud_delay_hint": "Use :15 or :17 minutes to avoid :00 GitHub queue delays.",
        "run.cloud_schedule_line": "Daily · {times} {tz}",
        "run.cloud_schedule_none": "No cloud schedule set",
        "run.cloud_last_run": "Last run: {result}",
        "run.run_cloud_btn": "Run on GitHub",
        "run.open_run": "Open URL",
        "run.open_folder": "Open Folder",
        "run.no_runs": "No Cloud runs yet",
        "run.empty_runs": "Trigger a manual run now or wait for your next scheduled time.",
        "run.select_run_hint": "Select a run above to inspect job logs or screenshots.",
        "run.recent_runs_title": "Recent Cloud Runs",
        "run.live_logs_title": "Live Logs",
        "run.redacted_badge": "Redacted",
        "run.result_passed": "Passed",
        "run.result_failed": "Failed",
        "run.result_cancelled": "Cancelled",
        "run.result_running": "Running",
        "run.result_queued": "Queued",
        "run.trigger_scheduled": "Scheduled",
        "run.trigger_manual": "Manual",
        "run.diag_preview_title": "Failure Screenshot Preview",
        "run.col_status": "Status",
        "run.col_conclusion": "Result",
        "run.col_event": "Trigger",
        "run.col_created": "Created",
        "diag.copy_report": "Copy Report",
        "settings.subtitle": "Appearance, timezone, Telegram alerts, connected accounts, and updates.",
        "settings.tab_general": "General & Alerts",
        "settings.tab_accounts": "Accounts, Privacy & Updates",
        "settings.general_group": "General",
        "settings.appearance_group": "General",
        "settings.theme_desc": "Switch between Dark, Light, or Windows system theme",
        "settings.accent_mode": "Accent Color",
        "settings.accent_desc": "Highlight color for active tabs and primary buttons",
        "settings.accent_system": "Match Windows System",
        "settings.accent_tiktok": "TikTok Coral (#fe2c55)",
        "settings.language_desc": "Interface copy language (English / Bahasa Indonesia)",
        "settings.timezone_desc": "Applies across Local PC tasks, Cloud cron, and logs",
        "settings.telegram_group": "Telegram Alerts",
        "settings.tg_enable_title": "Daily run reports",
        "settings.tg_enable_desc": "Send streak success or error alerts to Telegram",
        "settings.tg_token_desc": "Create a bot in Telegram and paste its API token",
        "settings.tg_chat_desc": "Target Telegram chat for daily delivery reports",
        "settings.workspace_group": "Accounts, Privacy & Data",
        "settings.github_account_title": "GitHub Account",
        "settings.open_botfather": "@BotFather",
        "settings.cli_export_title": "Developer CLI Export",
        "settings.cli_export_desc": "Export .env & accounts.json for manual CLI runs",
        "settings.cli_plaintext_warn": "Contains unencrypted cookies — local CLI use only",
        "settings.workspace_dir": "Output Folder",
        "settings.workspace_dir_desc": "Destination folder for exported .env, accounts.json & friends.csv",
        "settings.sync_now": "Export CLI Files",
        "settings.import_now": "Import CLI Folder",
        "settings.open_config_dir": "Open Folder",
        "settings.config_folder_title": "App Config Folder",
        "settings.view_details_btn": "View Details",
        "settings.danger_zone": "Danger Zone",
        "settings.reset_all": "Reset Local App Data",
        "settings.reset_local_desc": "Clears local cookies, tokens & preferences. Your GitHub repo stays untouched.",
        "settings.reset_btn_short": "Reset...",
        "settings.reset_modal_title": "Reset Local App Data?",
        "settings.reset_scope_1": "Removes saved TikTok cookies & accounts",
        "settings.reset_scope_2": "Removes GitHub & Telegram tokens",
        "settings.reset_scope_3": "Resets local app preferences",
        "settings.reset_scope_safe": "Your GitHub repo & Cloud workflows are NOT deleted",
        "settings.reset_confirm_btn": "Clear Local Data",
        "settings.reset_all_confirm": "This will erase your saved configuration and all stored GitHub tokens, Telegram tokens, and TikTok cookies. Your GitHub repo stays untouched. Continue?",
        "settings.security_card_title": "Privacy & Security",
        "settings.security_card_desc": "Local encryption, direct connections, and zero tracking",

        # Friend Chips & Schedule Section UX
        "content.friend_input_placeholder": "@username or TikTok profile URL...",
        "content.add_friend_btn": "Add Friend",
        "content.friends_empty": "No friends added yet. Type a @username above or click Import CSV.",
        "schedule.times_empty": "No run times configured.",
        "cloud.section_account_repo": "Target Repository",
        "cloud.section_schedule": "Daily Schedule ({tz})",
        "cloud.repo_advanced_toggle": "Advanced Options & PAT",

        # Security & Privacy Trust Transparency
        "security.banner_title": "Local-First Privacy",
        "security.banner_desc": "Encrypted on your PC · Your own private GitHub · Zero tracking.",
        "security.how_protected_btn": "Privacy Details",
        "security.dialog_title": "Your Privacy & Security",
        "security.dialog_subtitle": "100% open-source with zero developer servers. Your data stays on your PC or your own GitHub.",
        "security.point1_title": "Encrypted on your PC",
        "security.point1_desc": "Cookies and tokens are locked to your Windows login.",
        "security.point2_title": "Direct connections only",
        "security.point2_desc": "Talks only to TikTok, GitHub, and Telegram. Zero tracking.",
        "security.point3_title": "Sealed before Cloud upload",
        "security.point3_desc": "Secrets encrypt locally; only your private repo can read them.",
        "security.point4_title": "Safe logs & read-only import",
        "security.point4_desc": "Browser import never alters data; logs automatically hide secrets.",
        "security.tech_details_toggle": "Technical & audit details (DPAPI, NaCl, endpoints)",
        "security.tech_details_body": "• Storage: Windows Credential Manager / DPAPI (%APPDATA%\\TikTokStreak\\secrets.dpapi)\n• Endpoints: *.tiktok.com, github.com, api.github.com, api.telegram.org\n• Sealing: Client-side PyNaCl Libsodium SealedBox with repo public key\n• Browser DB: Read-only SQLite (?mode=ro), tiktok.com rows only",
        "security.open_config_btn": "Open Folder",
        "security.open_repo_btn": "View Source",

        # Headful Guard, Cloud Control & Auto-Updater
        "run.show_browser_hint": "Locks inbox clicks while sending when On",
        "cloud.bot_repos_title": "Detected Bot Repositories",
        "cloud.bot_repos_scanning": "Scanning GitHub repositories...",
        "cloud.bot_repos_none": "No TikTok Streak bot repositories found on your GitHub account yet.",
        "cloud.bot_repos_summary_one": "1 active bot",
        "cloud.bot_repos_summary_multi": "{count} active — duplicate!",
        "cloud.bot_repos_summary_paused": "{count} paused",
        "cloud.duplicate_warning": "{count} active bots detected — pause extra schedules to prevent duplicate DMs.",
        "cloud.repo_state_active": "Active",
        "cloud.repo_state_paused": "Paused",
        "cloud.repo_selected_tag": "Selected",
        "cloud.use_repo_btn": "Select",
        "cloud.pause_short": "Pause",
        "cloud.resume_short": "Resume",
        "cloud.pause_others_btn": "Pause Others",
        "cloud.paused_others_ok": "Paused {count} other bot schedule(s)",
        "cloud.open_repo_tooltip": "Open repository on GitHub",
        "cloud.scan_bot_repos_tooltip": "Refresh detected bot repositories",
        "cloud.pause_schedule": "Pause Schedule",
        "cloud.resume_schedule": "Resume Schedule",
        "cloud.stop_run": "Stop Run",
        "cloud.schedule_paused_ok": "Paused cloud schedule for {repo}",
        "cloud.schedule_resumed_ok": "Resumed cloud schedule for {repo}",
        "cloud.run_cancelled_ok": "Cancellation requested for run #{run_id}",
        "cloud.repo_status_existing_bot": "Updates {state} bot repo",
        "cloud.repo_status_non_bot": "Repository exists without bot workflow",
        "cloud.repo_status_new": "New repository — Deploy will create it",
        "cloud.dup_deploy_title": "Other Active Bot Repository Found",
        "cloud.dup_deploy_body": "You already have active bot schedule(s) on:\n{repos}\n\nDeploying to '{target}' will leave multiple schedules running at the same time unless the old one is paused.",
        "cloud.dup_deploy_pause_btn": "Pause Old & Deploy",
        "cloud.dup_deploy_keep_btn": "Deploy Anyway",
        "cloud.non_bot_confirm_title": "Existing Repository Detected",
        "cloud.non_bot_confirm_body": "Repository '{repo}' already exists on your GitHub account and does not have .github/workflows/streak.yml yet. Push TikTok Streak files into it?",
        "settings.updates_group": "Updates",
        "settings.app_version_title": "TikTok Streak Setup v{version}",
        "settings.app_version_uptodate": "You are using the latest version",
        "settings.app_version_available": "New version v{version} is available!",
        "settings.check_updates_btn": "Check Now",
        "settings.checking_updates": "Checking for updates...",
        "settings.auto_check_updates": "Auto-check on startup",
        "nav.update_available": "Update v{version}",
        "nav.update_downloading": "Downloading",
        "nav.update_downloading_ind": "Downloading...",
        "nav.update_installing": "Restarting...",
        "nav.update_badge_new": "New",
        "update.banner_title": "Update Available: v{version}",
        "update.banner_desc": "Includes important bug fixes and stability improvements. Update now to stay protected.",
        "update.btn_now": "Update Now",
        "update.btn_later": "Later",
        "update.btn_skip": "Skip Version",
        "update.btn_skip_ver": "Skip v{version}",
        "update.btn_background": "Continue in Background",
        "update.btn_cancel_download": "Cancel",
        "update.dlg_title": "Update Available · v{latest}",
        "update.dlg_downloading_title": "Downloading v{latest}",
        "update.dlg_bg_hint": "You can keep using the app while the download finishes.",
        "update.dlg_subtitle": "Installed: v{current}  →  Latest: v{latest}",
        "update.dlg_no_notes": "A new release is available on GitHub Releases.",
        "update.dlg_download_install": "Install Update",
        "update.dlg_open_release": "Open GitHub Release",
        "update.dlg_downloading": "{downloaded} of {total}",
        "update.dlg_verifying": "Verifying integrity (SHA-256) and restarting...",
        "update.dlg_source_note": "Running from Python source — pull the latest repository commits or download the EXE from GitHub Releases.",
        "update.cloud_sync_title": "Cloud bot update available",
        "update.cloud_sync_desc": "App is v{app_ver}; Cloud runs {deployed_ver}. Re-deploy to apply latest fixes.",
        "update.cloud_sync_btn": "Update Cloud",

        # Errors
        "err.generic": "Operation failed: {detail}",
        "err.v20": "Chrome/Edge uses App-Bound Encryption (v20), which blocks reading cookies from outside the browser. Click 'Sign in via Browser (Recommended)' (then click 'I'm Logged In — Grab Now' once signed in) or paste your cookie manually.",
        "err.browser_locked": "That browser is currently open and locking its cookie database. Close all windows of that browser and try again (or use Guided Browser Login).",
        "err.inbox_unavailable": "This TikTok account redirects /messages to Business Suite, where the web DM inbox is unavailable. Please use a standard personal TikTok account whose /messages inbox loads normally.",
        "err.session_expired": "Your TikTok session cookie is invalid or expired. Please sign in again via Guided Browser Login or paste a fresh session cookie.",
        "err.too_many_attempts": "TikTok temporarily blocked login attempts ('Maximum number of attempts reached'). Wait 15–30 minutes, or log in via QR code / your normal browser and paste the session cookie manually.",
        "err.rate_limit": "TikTok anti-spam rate limit detected. Stop running the bot for 24 hours to let the cooldown clear.",
        "err.dm_blocked": "Could not open DM chat for this friend. Make sure you follow each other mutually and DMs are allowed.",
        "err.recipient_mismatch": "Active chat recipient did not match the target @username. Send aborted safely to prevent misdelivery.",
        "err.telegram_409": "Telegram error 409: this bot already has an active webhook configured, so getUpdates is blocked. Create a new bot via @BotFather or delete its webhook first.",
        "err.telegram_401": "Invalid Telegram bot token (401 Unauthorized). Double-check the token from @BotFather.",
        "err.http_401": "GitHub authentication failed (401). Your token may be expired or revoked — please sign in again.",
        "err.http_403": "GitHub denied permission (403). Make sure your token has both 'repo' and 'workflow' scopes, or wait if rate-limited.",
        "err.http_404": "GitHub resource not found (404). Verify the repository name, branch, or workflow file (.github/workflows/streak.yml) exists.",
        "err.http_409": "GitHub conflict (409). The repository state changed or has a merge conflict — refresh and retry.",
        "err.http_422": "GitHub rejected the request (422). The repository may already exist, workflow is disabled, or a secret/input is invalid.",
        "err.http_429": "Too many requests (429). Please wait a moment and try again.",
        "err.http_5xx": "Server error ({code}). GitHub or TikTok is temporarily unavailable — try again shortly.",
        "err.network": "Network connection failed or timed out. Check your internet connection and firewall/proxy settings.",
        "err.device_expired": "GitHub sign-in code expired before authorization completed. Start sign-in again.",
        "err.device_denied": "GitHub sign-in was cancelled or denied in the browser.",
        "err.missing_dep": "Required Python dependency is missing ({detail}). Reinstall GUI requirements.",
        "err.scheduler": "Windows Task Scheduler error: {detail}",
        "err.waf": "TikTok returned a security verification / non-JSON response. Use Guided Browser Login to authenticate cleanly.",
    },
    "id": {
        # App & Navigation
        "app.title": "Pengaturan TikTok Streak",
        "app.subtitle": "Otomatisasi Streak DM TikTok Harian",
        "nav.dashboard": "Beranda",
        "nav.accounts": "Akun",
        "nav.content": "Pesan & Media",
        "nav.automation": "Otomatisasi",
        "nav.schedule": "Jadwal & Jalankan",
        "nav.cloud": "Deploy Cloud",
        "nav.diagnostics": "Diagnostik",
        "nav.settings": "Pengaturan",

        # Common actions & status
        "common.save": "Simpan",
        "common.cancel": "Batal",
        "common.close": "Tutup",
        "common.continue": "Lanjut",
        "common.back": "Kembali",
        "common.refresh": "Muat Ulang",
        "common.browse": "Pilih...",
        "common.clear": "Bersihkan",
        "common.delete": "Hapus",
        "common.add": "Tambah",
        "common.remove": "Hapus",
        "common.test": "Uji",
        "common.verify": "Verifikasi",
        "common.loading": "Memuat...",
        "common.ready": "Siap",
        "common.enabled": "Aktif",
        "common.disabled": "Nonaktif",
        "common.optional": "Opsional",
        "common.unknown": "Tidak diketahui",
        "common.success": "Berhasil",
        "common.warning": "Peringatan",
        "common.error": "Kesalahan",
        "common.copied": "Tersalin!",

        # Dashboard / Home
        "dashboard.welcome": "Beranda",
        "dashboard.subtitle": "Otomatisasi streak DM TikTok harian",
        "dashboard.status_ready": "Semua Sistem Siap",
        "dashboard.status_incomplete": "Lengkapi persiapan untuk mulai",
        "dashboard.accounts_count": "{count} akun terkonfigurasi",
        "dashboard.friends_count": "{count} total teman",
        "dashboard.mode_cloud": "Cloud GitHub",
        "dashboard.mode_local": "PC Lokal",
        "dashboard.mode_both": "Hybrid (Lokal + Cloud)",
        "dashboard.run_local_now": "Jalankan Semua Lokal",
        "dashboard.run_cloud_now": "Jalankan di GitHub",
        "dashboard.smoke_test": "Tes 1 Teman",
        "dashboard.last_test_ok": "Berhasil",
        "dashboard.last_test_pending": "Belum pernah diuji",

        # Accounts / TikTok Auth
        "accounts.title": "Akun",
        "accounts.subtitle": "Hubungkan TikTok dan kelola daftar teman tiap akun.",
        "accounts.multi_toggle": "Mode banyak akun",
        "accounts.add_account": "Tambah Akun",
        "accounts.account_name": "Nama Akun",
        "accounts.scan_browsers": "Pindai Browser Terpasang",
        "accounts.read_browser": "Ambil dari Browser Terpilih",
        "accounts.guided_login": "Masuk lewat Browser",
        "accounts.guided_login_hint": "Hubungkan TikTok dan kelola daftar teman tiap akun.",
        "accounts.guided_login_active": "Masuk ke TikTok di jendela browser yang terbuka, lalu konfirmasi.",
        "accounts.manual_paste": "Tempel Cookie",
        "accounts.manual_paste_hint": "Tempel header Cookie, JSON cookie, atau sessionid langsung.",
        "accounts.verify_inbox": "Verifikasi Sesi",
        "accounts.verifying": "Memverifikasi...",
        "accounts.verified": "Terverifikasi (@{username})",
        "accounts.inbox_ok": "Inbox web dapat diakses",
        "accounts.inbox_blocked": "Dialihkan ke Business Suite",
        "accounts.not_verified": "Belum diverifikasi",
        "accounts.local_only_badge": "Lokal Saja",
        "accounts.cloud_ready_badge": "Siap Cloud",
        "accounts.verify_cloud_warn": "Inbox terbuka secara lokal, tetapi cookie rute regional untuk runner GitHub Cloud AS belum lengkap. Gunakan Masuk lewat Browser jika run Cloud gagal.",
        "accounts.verify_captcha_err": "TikTok menampilkan tantangan verifikasi keamanan pada mode headless. Gunakan Masuk lewat Browser untuk verifikasi langsung.",
        "accounts.cookies_cloud_ready": "{count} cookie (Siap Cloud)",
        "accounts.cookies_local_only": "{count} cookie (Lokal saja)",
        "accounts.browser_locked_badge": "Browser terbuka (terkunci)",
        "accounts.v20_badge": "Terproteksi v20 — gunakan Login Browser atau Tempel",
        "accounts.no_tiktok_badge": "Belum ada login TikTok",
        "accounts.has_tiktok_badge": "Sesi TikTok ditemukan",
        "accounts.browser_disclosure_title": "Profil browser untuk impor langsung (Chrome / Edge / Firefox)",
        "accounts.browser_disclosure_tag": "Alternatif opsional",
        "accounts.saved_count": "{count} akun tersimpan",
        "accounts.import_dlg_title": "Impor dari Browser",
        "accounts.import_dlg_subtitle": "Pilih profil browser terdeteksi untuk mengimpor sesi TikTok bagi {account}.",
        "accounts.import_dlg_btn": "Impor Sesi",
        "accounts.import_dlg_no_profiles": "Tidak ada profil browser terdeteksi. Klik Muat Ulang atau gunakan Masuk lewat Browser.",

        # Content / Message & Media
        "content.title": "Pesan & Media",
        "content.subtitle": "Pilih isi yang dikirim ke setiap teman setiap hari.",
        "content.always_sent": "Selalu dikirim",
        "content.message_hint": "Dikirim pertama untuk menjaga api streak harian tetap aktif.",
        "content.friends_label": "Daftar Teman",
        "content.friends_placeholder": "Masukkan @username TikTok dipisahkan koma atau baris baru (atau tempel URL profil)",
        "content.import_csv": "Impor CSV",
        "content.export_csv": "Ekspor CSV",
        "content.message_label": "Pesan Streak",
        "content.video_toggle": "Kartu Share Video",
        "content.video_url_label": "URL Video TikTok",
        "content.video_resolve": "Cek Link",
        "content.share_times_label": "Share per teman",
        "content.photo_toggle": "Kartu Foto",
        "content.photo_select": "Pilih Foto",
        "content.photo_default": "Menggunakan foto bawaan aplikasi",
        "content.photo_custom": "Foto kustom: {path}",
        "content.daily_cap_label": "Batas teman harian",
        "content.overrides_title": "Pengaturan Khusus Akun",
        "content.next_automation": "Lanjut: Otomatisasi",
        "content.dm_preview_title": "Pratinjau DM",
        "content.dm_preview_video": "Share Video ×{count}",
        "content.dm_preview_video_off": "Share video nonaktif",
        "content.dm_preview_photo_default": "Foto bawaan dilampirkan",
        "content.dm_preview_photo_custom": "Foto kustom: {name}",
        "content.dm_preview_photo_off": "Kartu foto nonaktif",
        "content.dm_composer_placeholder": "Kirim pesan...",
        "content.pet_tasks_title": "Tugas Streak Pet",
        "content.pet_pts_badge": "Pertumbuhan pet: +{pts} poin / hari",
        "content.pet_pts_short": "+{pts} poin",
        "content.pet_task_msg": "Pesan teks",
        "content.pet_task_video": "2 share video",
        "content.pet_task_photo": "Kartu foto",
        "content.pet_pt_1": "+1 poin",
        "content.pet_pt_2": "+2 poin",
        "content.pet_pt_4": "+4 poin",

        # Schedule & Execution / Automation
        "schedule.title": "Otomatisasi",
        "schedule.mode_label": "Mode Eksekusi",
        "schedule.local_headless": "Jalankan browser lokal di latar belakang (headless)",
        "schedule.local_times": "Jam Jalan Harian (WIB / UTC+7)",
        "schedule.local_times_tz": "Jam Jalan Harian ({tz})",
        "schedule.timezone": "Zona Waktu Jadwal",
        "schedule.cloud_times": "Jam Jalan Cloud Harian ({tz})",
        "schedule.quick_presets": "Preset cepat:",
        "schedule.preset_offpeak_1": "1x (08:15)",
        "schedule.preset_offpeak_2": "2x (08:15, 20:15)",
        "schedule.preset_offpeak_3": "3x",
        "schedule.raw_cron_toggle": "Cron UTC Mentah",
        "schedule.cloud_delay_note": "Gunakan menit :15 atau :17 agar terhindar dari antrean :00 GitHub.",
        "schedule.sync_windows": "Sinkron Tugas",
        "schedule.remove_windows": "Hapus",
        "schedule.cloud_cron": "Cron GitHub Actions (UTC)",
        "schedule.cloud_summary": "Jadwal cloud: {summary}",
        "schedule.local_tasks_count": "{count} tugas aktif",
        "schedule.stop_run": "Henti",
        "schedule.test_friend_prompt": "Pilih teman untuk tes 1 teman",

        "automation.title": "Otomatisasi",
        "automation.subtitle": "Pilih lokasi dan jadwal pengiriman streak harian.",
        "automation.tz_change": "Ubah di Pengaturan",
        "automation.mode_desc_local": "Berjalan harian via Windows Task Scheduler saat PC menyala.",
        "automation.mode_desc_cloud": "Berjalan harian di repo GitHub privat Anda meski PC mati.",
        "automation.mode_desc_both": "Berjalan di Windows Task Scheduler sekaligus GitHub Actions.",
        "automation.repo_card_title": "Repositori GitHub",
        "automation.local_card_title": "Jadwal PC Lokal (Windows Task Scheduler)",
        "automation.schedule_card_title": "Jadwal Harian ({tz})",
        "automation.utc_equivalent": "Setara UTC: {times}",
        "automation.advanced_toggle": "Lanjutan (token PAT, sinkron secret saja, cron UTC mentah)",
        "automation.matches_run_logs": "Sinkron dengan Jalankan & Log",
        "automation.preflight_heading": "Kesiapan Sebelum Deploy",
        "automation.preflight_issues_badge": "{count} hal perlu diperiksa",
        "automation.preflight_resolve_hint": "Selesaikan masalah di atas — tanpa popup kejutan saat deploy.",
        "automation.preflight_session_title": "Sesi TikTok",
        "automation.preflight_session_ok_one": "{account} · Siap Cloud",
        "automation.preflight_session_ok_multi": "{count} akun · Siap Cloud",
        "automation.preflight_session_missing": "{accounts} belum memiliki cookie sesi TikTok",
        "automation.preflight_friends_missing": "{accounts} belum memiliki daftar teman",
        "automation.preflight_session_bare": "{accounts} belum punya cookie rute Cloud (hanya sessionid)",
        "automation.preflight_repo_title": "Repo Target",
        "automation.preflight_repo_active_bot": "Perbarui bot aktif",
        "automation.preflight_repo_paused_bot": "Perbarui bot terjeda",
        "automation.preflight_repo_new": "Buat repo bot baru",
        "automation.preflight_repo_non_bot": "{repo} sudah ada tanpa file bot",
        "automation.preflight_repo_allowed": "Timpa diizinkan ({repo})",
        "automation.preflight_allow_existing_btn": "Izinkan Repo",
        "automation.preflight_dups_title": "Jadwal Aktif",
        "automation.preflight_dups_ok": "Tidak ada bot ganda",
        "automation.preflight_dups_warn": "{repos} juga terjadwal (risiko DM ganda)",
        "automation.preflight_dups_modal": "{repos} masih aktif. Membiarkan keduanya aktif akan mengirim DM harian ganda.",
        "automation.preflight_pause_old_btn": "Jeda Lama",
        "automation.deploy_warn_title": "Deploy dengan {count} Peringatan Aktif?",
        "automation.deploy_ready_hint": "Siap menyinkronkan secret terenkripsi dan jadwal.",

        # Cloud Deploy & GitHub
        "cloud.title": "Repositori GitHub",
        "cloud.signin_device": "Hubungkan GitHub",
        "cloud.signin_pat": "PAT",
        "cloud.signout": "Keluar",
        "cloud.signed_in_as": "Masuk sebagai @{login}",
        "cloud.device_code_prompt": "Masukkan kode {code} di GitHub untuk menyetujui:",
        "cloud.copy_code": "Salin Kode",
        "cloud.open_github": "Buka Halaman Otorisasi GitHub",
        "cloud.repo_name": "Nama Repositori",
        "cloud.private_repo": "Privat",
        "cloud.public_fork": "Fork Publik (dari {source})",
        "cloud.deploy": "Deploy ke GitHub",
        "cloud.sync_schedule": "Perbarui Jadwal Cloud",
        "cloud.check_status": "Cek Status Run Terbaru",
        "cloud.download_diagnostics": "Screenshot",
        "cloud.view_logs": "Lihat Log",
        "cloud.telegram_title": "Notifikasi Telegram",
        "cloud.telegram_enable": "Laporan run harian",
        "cloud.telegram_token": "Token Bot",
        "cloud.telegram_chat_id": "Chat ID",
        "cloud.telegram_detect": "Deteksi",
        "cloud.telegram_test": "Kirim Tes",

        # Diagnostics & Settings
        "diag.title": "Log & Diagnostik",
        "diag.empty": "Belum ada log tercatat.",
        "diag.copy_logs": "Salin Log (Aman/Tersensor)",
        "settings.title": "Pengaturan",
        "settings.language": "Bahasa",
        "settings.timezone": "Zona Waktu",
        "settings.theme": "Tema Tampilan",
        "settings.theme_system": "Sistem",
        "settings.theme_light": "Terang",
        "settings.theme_dark": "Gelap",
        "settings.export_workspace": "Ekspor / Impor Konfigurasi CLI",
        "settings.export_workspace_desc": "Menulis .env & accounts.json (berisi cookie tanpa enkripsi untuk CLI) atau mengimpor folder CLI",
        "settings.import_workspace": "Impor Workspace Lokal",
        "settings.clear_secrets": "Hapus Semua Token & Cookie Tersimpan",
        "settings.clear_secrets_confirm": "Hapus semua token GitHub, token Telegram, dan cookie sesi TikTok dari penyimpanan aman?",

        # Extended Fluent UI keys
        "nav.content_schedule": "Pesan & Media",
        "nav.run_logs": "Jalankan & Log",
        "home.checklist_heading": "Daftar Persiapan",
        "home.checklist_progress": "{done} / {total} selesai",
        "home.checklist_hint": "Langkah pertama yang belum selesai disorot",
        "home.kpi_account_title": "Akun & Teman",
        "home.kpi_automation_title": "Otomatisasi",
        "home.kpi_lastrun_title": "Run Terakhir",
        "home.go_step": "Buka langkah",
        "home.step1_title": "1. Akun",
        "home.step1_desc": "Hubungkan minimal satu akun TikTok dan tambahkan daftar teman.",
        "home.step2_title": "2. Pesan",
        "home.step2_desc": "Atur pesan streak harian, video, dan kartu foto.",
        "home.step3_title": "3. Otomatisasi",
        "home.step3_desc": "Pilih jadwal PC Lokal, Cloud GitHub, atau mode Hybrid.",
        "home.step4_title": "4. Uji Kirim",
        "home.step4_desc": "Jalankan tes 1 teman dan aktifkan notifikasi Telegram bila perlu.",
        "home.telegram_settings_btn": "Pengaturan Telegram",
        "home.privacy_details_btn": "Detail Privasi",
        "home.needs_attention": "Perlu diatur",
        "home.optional_pending": "Opsional — siap",
        "accounts.single_mode": "1 Akun",
        "accounts.multi_mode": "Multi-Akun",
        "accounts.browser_profile": "Profil Browser (Impor Opsional)",
        "accounts.sign_in_browser": "Masuk lewat Browser",
        "accounts.import_browser": "Impor dari Browser",
        "accounts.paste_cookie": "Tempel Cookie",
        "accounts.inbox_verified": "Terverifikasi",
        "accounts.inbox_blocked": "Diblokir Business Suite",
        "accounts.inbox_expired": "Kedaluwarsa",
        "accounts.inbox_unchecked": "Belum Dicek",
        "accounts.no_session": "Belum ada cookie",
        "accounts.friends_count": "{count} teman",
        "accounts.scan_not_run": "Klik Muat Ulang untuk memindai profil browser",
        "accounts.multi_hidden_hint": "Mode 1 akun hanya memakai Akun #1. {extra} akun tersimpan lainnya tetap aman jika Anda kembali ke mode multi-akun.",
        "accounts.override_hint": "Kolom yang tidak dicentang mengikuti pengaturan global di Pesan & Media.",
        "accounts.override_toggle": "Pengaturan khusus pesan & media",
        "accounts.override_shares": "Share:",
        "accounts.override_message": "Pesan streak khusus",
        "accounts.override_video": "Link video & jumlah share khusus",
        "accounts.override_photo": "Foto khusus",
        "accounts.disable_for_account": "Nonaktifkan untuk akun ini",
        "accounts.grab_session_now": "Saya Sudah Masuk",
        "cookie_dlg.title": "Tempel Cookie Sesi TikTok",
        "cookie_dlg.step_1": "1. Buka tiktok.com",
        "cookie_dlg.step_2": "2. F12 → Cookies",
        "cookie_dlg.step_3": "3. Salin sessionid",
        "cookie_dlg.steps": "1. Buka tiktok.com  ·  2. F12 → Cookies  ·  3. Salin sessionid",
        "cookie_dlg.placeholder": "Tempel sessionid, header Cookie lengkap, atau ekspor JSON...",
        "cookie_dlg.preview_valid": "{count} cookie ({preview})",
        "cookie_dlg.preview_empty": "Menunggu sessionid atau data cookie yang valid...",
        "cookie_dlg.invalid_msg": "Silakan tempel sessionid atau string cookie TikTok yang valid.",
        "cookie_dlg.bare_warning": "Hanya sessionid? Run Cloud mungkin butuh cookie lengkap.",
        "cookie_dlg.use_browser_btn": "Pakai Login Browser",
        "cookie_dlg.save_btn": "Simpan Cookie",
        "cloud.bare_deploy_title": "Cookie Mungkin Belum Siap untuk Cloud GitHub",
        "cloud.bare_deploy_confirm": "Akun berikut belum memiliki cookie rute regional TikTok untuk runner GitHub AS: {accounts}.\n\nKlik Verifikasi Sesi pada tiap akun atau gunakan Masuk lewat Browser terlebih dahulu. Tetap lanjutkan deploy?",
        "device_dlg.title": "Hubungkan Akun GitHub",
        "device_dlg.step_1": "1. Salin kode",
        "device_dlg.step_2": "2. Tempel di GitHub",
        "device_dlg.step_3": "3. Selesai",
        "device_dlg.subtitle": "Salin kode sekali pakai ini, buka GitHub, lalu tempel untuk menyetujui.",
        "device_dlg.copy_open": "Salin Kode & Buka GitHub",
        "device_dlg.copied_hint": "Kode {code} disalin! Tempel di halaman GitHub.",
        "device_dlg.waiting": "Menunggu otorisasi GitHub...",
        "device_dlg.authorized": "Berhasil masuk sebagai @{login}!",
        "content.video_valid": "ID {aweme_id}",
        "content.video_unverified": "Belum diverifikasi — klik Cek Link",
        "content.video_verified_author": "Terverifikasi (@{author})",
        "content.video_unavailable": "Tidak tersedia",
        "content.video_verify_offline": "Gagal verifikasi online",
        "content.video_disabled": "Pengiriman video dinonaktifkan",
        "content.video_hint": "Mendukung URL lengkap atau link pendek vt.tiktok.com.",
        "content.photo_use_default": "Pakai Foto Bawaan",
        "content.photo_reset": "Reset ke Bawaan",
        "content.photo_disabled": "Pengiriman foto dinonaktifkan",
        "content.repo_valid": "Nama repositori valid",
        "cloud.fetch_repos": "Muat Repo Saya",
        "cloud.select_existing_repo": "Pilih repo yang ada...",
        "cloud.repo_private_tag": "Privat",
        "cloud.repo_public_tag": "Publik",
        "cloud.repo_name_hint": "tiktok-streak atau owner/nama-repo",
        "cloud.sync_only": "Sinkronkan secret & jadwal saja (pertahankan commit repo)",
        "cloud.sync_only_hint": "Pertahankan kode repositori yang ada tanpa menimpa commit.",
        "cloud.pat_toggle": "Gunakan Personal Access Token (PAT)",
        "cloud.reconnect_github": "Ganti Akun",
        "schedule.mode_local": "PC Lokal",
        "schedule.mode_cloud": "Cloud GitHub",
        "schedule.mode_both": "Hybrid",
        "schedule.preset_1": "08:00 WIB (1x sehari)",
        "schedule.preset_2": "08:00 & 20:00 WIB (2x sehari)",
        "schedule.preset_3": "08:00, 14:00 & 20:00 WIB (3x sehari)",
        "schedule.preset_custom": "Cron UTC Kustom",
        "schedule.custom_cron": "Cron UTC",
        "schedule.add_time": "Tambah Jam",
        "schedule.deploy_btn": "Deploy ke GitHub",
        "schedule.not_signed_in": "Belum masuk ke GitHub",
        "schedule.pat_placeholder": "ghp_... (butuh scope repo + workflow)",
        "schedule.save_pat": "Simpan PAT",
        "run.title": "Jalankan & Log",
        "run.subtitle": "Uji cepat, pantau run Cloud, dan periksa log langsung berdampingan.",
        "run.local_title": "Jalankan Lokal",
        "run.quick_test": "Tes 1 Teman",
        "run.full_run": "Jalankan Semua",
        "run.stop_short": "Henti",
        "run.show_browser": "Tampilkan jendela browser",
        "run.cloud_title": "Cloud GitHub",
        "run.cloud_delay_hint": "Gunakan menit :15 atau :17 agar terhindar dari antrean :00 GitHub.",
        "run.cloud_schedule_line": "Harian · {times} {tz}",
        "run.cloud_schedule_none": "Jadwal cloud belum diatur",
        "run.cloud_last_run": "Terakhir: {result}",
        "run.run_cloud_btn": "Jalankan di GitHub",
        "run.open_run": "Buka URL",
        "run.open_folder": "Buka Folder",
        "run.no_runs": "Belum ada run Cloud",
        "run.empty_runs": "Jalankan manual sekarang atau tunggu jadwal berikutnya.",
        "run.select_run_hint": "Pilih salah satu run di atas untuk melihat log atau screenshot.",
        "run.recent_runs_title": "Riwayat Run Cloud",
        "run.live_logs_title": "Log Langsung",
        "run.redacted_badge": "Tersensor",
        "run.result_passed": "Berhasil",
        "run.result_failed": "Gagal",
        "run.result_cancelled": "Dibatalkan",
        "run.result_running": "Berjalan",
        "run.result_queued": "Antre",
        "run.trigger_scheduled": "Terjadwal",
        "run.trigger_manual": "Manual",
        "run.diag_preview_title": "Pratinjau Screenshot Kegagalan",
        "run.col_status": "Status",
        "run.col_conclusion": "Hasil",
        "run.col_event": "Pemicu",
        "run.col_created": "Waktu Dibuat",
        "diag.copy_report": "Salin Laporan",
        "settings.subtitle": "Tampilan, zona waktu, notifikasi Telegram, akun terhubung, dan pembaruan.",
        "settings.tab_general": "Umum & Notifikasi",
        "settings.tab_accounts": "Akun, Privasi & Pembaruan",
        "settings.general_group": "Umum",
        "settings.appearance_group": "Umum",
        "settings.theme_desc": "Pilih tema Gelap, Terang, atau ikuti sistem Windows",
        "settings.accent_mode": "Warna Aksen",
        "settings.accent_desc": "Warna sorotan untuk tab aktif dan tombol utama",
        "settings.accent_system": "Ikuti Sistem Windows",
        "settings.accent_tiktok": "TikTok Coral (#fe2c55)",
        "settings.language_desc": "Bahasa antarmuka aplikasi (English / Bahasa Indonesia)",
        "settings.timezone_desc": "Berlaku untuk tugas PC Lokal, cron Cloud, dan log",
        "settings.telegram_group": "Notifikasi Telegram",
        "settings.tg_enable_title": "Laporan run harian",
        "settings.tg_enable_desc": "Kirim laporan keberhasilan atau error streak ke Telegram",
        "settings.tg_token_desc": "Buat bot di Telegram lalu tempel token API-nya",
        "settings.tg_chat_desc": "Chat Telegram tujuan untuk laporan harian",
        "settings.workspace_group": "Akun, Privasi & Data",
        "settings.github_account_title": "Akun GitHub",
        "settings.open_botfather": "@BotFather",
        "settings.cli_export_title": "Ekspor CLI Developer",
        "settings.cli_export_desc": "Ekspor .env & accounts.json untuk menjalankan CLI manual",
        "settings.cli_plaintext_warn": "Berisi cookie tanpa enkripsi — khusus penggunaan CLI lokal",
        "settings.workspace_dir": "Folder Output",
        "settings.workspace_dir_desc": "Folder tujuan untuk ekspor .env, accounts.json & friends.csv",
        "settings.sync_now": "Ekspor File CLI",
        "settings.import_now": "Impor Folder CLI",
        "settings.open_config_dir": "Buka Folder",
        "settings.config_folder_title": "Folder Konfigurasi Aplikasi",
        "settings.view_details_btn": "Lihat Detail",
        "settings.danger_zone": "Zona Berbahaya",
        "settings.reset_all": "Reset Data Lokal Aplikasi",
        "settings.reset_local_desc": "Menghapus cookie, token & preferensi lokal. Repo GitHub Anda tetap aman.",
        "settings.reset_btn_short": "Reset...",
        "settings.reset_modal_title": "Reset Data Lokal Aplikasi?",
        "settings.reset_scope_1": "Menghapus cookie & akun TikTok tersimpan",
        "settings.reset_scope_2": "Menghapus token GitHub & Telegram",
        "settings.reset_scope_3": "Mengatur ulang preferensi lokal aplikasi",
        "settings.reset_scope_safe": "Repo GitHub & workflow Cloud Anda TIDAK dihapus",
        "settings.reset_confirm_btn": "Hapus Data Lokal",
        "settings.reset_all_confirm": "Ini akan menghapus konfigurasi tersimpan beserta token GitHub, token Telegram, dan cookie TikTok lokal. Repo GitHub Anda tetap aman. Lanjutkan?",
        "settings.security_card_title": "Privasi & Keamanan",
        "settings.security_card_desc": "Enkripsi lokal, koneksi langsung, dan tanpa pelacakan",

        # Friend Chips & Schedule Section UX
        "content.friend_input_placeholder": "@username atau link profil TikTok...",
        "content.add_friend_btn": "Tambah Teman",
        "content.friends_empty": "Belum ada teman. Ketik @username di atas atau klik Impor CSV.",
        "schedule.times_empty": "Belum ada jam jalan terjadwal.",
        "cloud.section_account_repo": "Repositori Target",
        "cloud.section_schedule": "Jadwal Harian ({tz})",
        "cloud.repo_advanced_toggle": "Opsi Lanjutan & PAT",

        # Security & Privacy Trust Transparency
        "security.banner_title": "Privasi Lokal",
        "security.banner_desc": "Terenkripsi di PC Anda · Repo GitHub privat Anda · Tanpa pelacakan.",
        "security.how_protected_btn": "Detail Privasi",
        "security.dialog_title": "Privasi & Keamanan Anda",
        "security.dialog_subtitle": "100% open-source tanpa server pengembang. Data Anda tetap di PC atau GitHub Anda sendiri.",
        "security.point1_title": "Terenkripsi di PC Anda",
        "security.point1_desc": "Cookie dan token terkunci pada akun login Windows Anda.",
        "security.point2_title": "Hanya koneksi langsung",
        "security.point2_desc": "Hanya terhubung ke TikTok, GitHub, dan Telegram. Tanpa pelacakan.",
        "security.point3_title": "Disegel sebelum diunggah ke Cloud",
        "security.point3_desc": "Secret dienkripsi secara lokal; hanya repo privat Anda yang bisa membacanya.",
        "security.point4_title": "Log aman & impor baca-saja",
        "security.point4_desc": "Impor browser tidak mengubah data; log otomatis menyembunyikan rahasia.",
        "security.tech_details_toggle": "Detail teknis & audit (DPAPI, NaCl, endpoint)",
        "security.tech_details_body": "• Penyimpanan: Windows Credential Manager / DPAPI (%APPDATA%\\TikTokStreak\\secrets.dpapi)\n• Endpoint: *.tiktok.com, github.com, api.github.com, api.telegram.org\n• Enkripsi Cloud: PyNaCl Libsodium SealedBox di sisi klien dengan kunci publik repo\n• DB Browser: SQLite baca-saja (?mode=ro), hanya baris tiktok.com",
        "security.open_config_btn": "Buka Folder",
        "security.open_repo_btn": "Lihat Sumber",

        # Headful Guard, Cloud Control & Auto-Updater
        "run.show_browser_hint": "Mengunci klik inbox saat mengirim bila aktif",
        "cloud.bot_repos_title": "Repositori Bot Terdeteksi",
        "cloud.bot_repos_scanning": "Memindai repositori GitHub...",
        "cloud.bot_repos_none": "Belum ada repositori bot TikTok Streak di akun GitHub Anda.",
        "cloud.bot_repos_summary_one": "1 bot aktif",
        "cloud.bot_repos_summary_multi": "{count} aktif — duplikat!",
        "cloud.bot_repos_summary_paused": "{count} dijeda",
        "cloud.duplicate_warning": "{count} bot aktif bersamaan — jeda yang lain agar teman tidak terima DM ganda.",
        "cloud.repo_state_active": "Aktif",
        "cloud.repo_state_paused": "Dijeda",
        "cloud.repo_selected_tag": "Dipilih",
        "cloud.use_repo_btn": "Pilih",
        "cloud.pause_short": "Jeda",
        "cloud.resume_short": "Aktifkan",
        "cloud.pause_others_btn": "Jeda Lainnya",
        "cloud.paused_others_ok": "{count} jadwal bot lain berhasil dijeda",
        "cloud.open_repo_tooltip": "Buka repositori di GitHub",
        "cloud.scan_bot_repos_tooltip": "Pindai ulang repositori bot",
        "cloud.pause_schedule": "Jeda Jadwal",
        "cloud.resume_schedule": "Aktifkan Jadwal",
        "cloud.stop_run": "Hentikan Run",
        "cloud.schedule_paused_ok": "Jadwal cloud dijeda untuk {repo}",
        "cloud.schedule_resumed_ok": "Jadwal cloud diaktifkan untuk {repo}",
        "cloud.run_cancelled_ok": "Permintaan penghentian dikirim untuk run #{run_id}",
        "cloud.repo_status_existing_bot": "Memperbarui repo bot ({state})",
        "cloud.repo_status_non_bot": "Repo sudah ada tanpa workflow bot",
        "cloud.repo_status_new": "Repo baru — Deploy akan membuatnya",
        "cloud.dup_deploy_title": "Ada Repo Bot Lain yang Masih Aktif",
        "cloud.dup_deploy_body": "Anda sudah memiliki jadwal bot yang aktif di:\n{repos}\n\nJika lanjut deploy ke '{target}', beberapa jadwal akan berjalan bersamaan kecuali repo lama dijeda.",
        "cloud.dup_deploy_pause_btn": "Jeda Repo Lama & Deploy",
        "cloud.dup_deploy_keep_btn": "Tetap Deploy",
        "cloud.non_bot_confirm_title": "Repositori Sudah Ada",
        "cloud.non_bot_confirm_body": "Repositori '{repo}' sudah ada di akun GitHub Anda dan belum memiliki .github/workflows/streak.yml. Tetap kirim file bot TikTok Streak ke dalamnya?",
        "settings.updates_group": "Pembaruan",
        "settings.app_version_title": "TikTok Streak Setup v{version}",
        "settings.app_version_uptodate": "Anda sudah memakai versi terbaru",
        "settings.app_version_available": "Versi baru v{version} tersedia!",
        "settings.check_updates_btn": "Cek Sekarang",
        "settings.checking_updates": "Memeriksa pembaruan...",
        "settings.auto_check_updates": "Cek otomatis saat dibuka",
        "nav.update_available": "Update v{version}",
        "nav.update_downloading": "Mengunduh",
        "nav.update_downloading_ind": "Mengunduh...",
        "nav.update_installing": "Memulai ulang...",
        "nav.update_badge_new": "Baru",
        "update.banner_title": "Pembaruan Tersedia: v{version}",
        "update.banner_desc": "Berisi perbaikan bug penting dan peningkatan stabilitas. Segera perbarui aplikasi.",
        "update.btn_now": "Perbarui Sekarang",
        "update.btn_later": "Nanti",
        "update.btn_skip": "Lewati Versi",
        "update.btn_skip_ver": "Lewati v{version}",
        "update.btn_background": "Lanjut di Latar Belakang",
        "update.btn_cancel_download": "Batal",
        "update.dlg_title": "Pembaruan Tersedia · v{latest}",
        "update.dlg_downloading_title": "Mengunduh v{latest}",
        "update.dlg_bg_hint": "Anda tetap dapat memakai aplikasi selama unduhan berlangsung.",
        "update.dlg_subtitle": "Terpasang: v{current}  →  Terbaru: v{latest}",
        "update.dlg_no_notes": "Rilis baru sudah tersedia di GitHub Releases.",
        "update.dlg_download_install": "Pasang Pembaruan",
        "update.dlg_open_release": "Buka Halaman Rilis",
        "update.dlg_downloading": "{downloaded} dari {total}",
        "update.dlg_verifying": "Memverifikasi integritas (SHA-256) & memulai ulang...",
        "update.dlg_source_note": "Berjalan dari kode Python — tarik commit terbaru atau unduh EXE dari GitHub Releases.",
        "update.cloud_sync_title": "Pembaruan bot Cloud tersedia",
        "update.cloud_sync_desc": "Aplikasi v{app_ver}; Cloud memakai {deployed_ver}. Deploy ulang untuk perbaikan terbaru.",
        "update.cloud_sync_btn": "Perbarui Cloud",

        # Errors
        "err.generic": "Operasi gagal: {detail}",
        "err.v20": "Chrome/Edge memakai enkripsi App-Bound (v20) sehingga cookie tidak bisa dibaca dari luar browser. Klik 'Masuk lewat Browser (Disarankan)' (lalu klik 'Sudah Login — Ambil Sekarang' setelah masuk) atau tempel cookie secara manual.",
        "err.browser_locked": "Browser tersebut sedang terbuka dan mengunci database cookie. Tutup semua jendela browser itu lalu coba lagi (atau gunakan Login Browser Terpandu).",
        "err.inbox_unavailable": "Akun TikTok ini mengalihkan /messages ke Business Suite sehingga inbox DM web tidak tersedia. Gunakan akun TikTok personal yang dapat membuka tiktok.com/messages secara normal.",
        "err.session_expired": "Cookie sesi TikTok tidak valid atau sudah kedaluwarsa. Silakan masuk ulang melalui Login Browser Terpandu atau tempel cookie sesi yang baru.",
        "err.too_many_attempts": "TikTok membatasi percobaan login sementara ('Maximum number of attempts reached'). Tunggu 15–30 menit, atau masuk lewat kode QR / browser biasa lalu tempel cookie sesi secara manual.",
        "err.rate_limit": "Batas anti-spam TikTok terpicu. Hentikan bot selama 24 jam hingga cooldown pulih.",
        "err.dm_blocked": "Tidak dapat membuka obrolan DM untuk teman ini. Pastikan sudah saling follow dan pengaturan DM tidak dibatasi.",
        "err.recipient_mismatch": "Penerima chat aktif tidak cocok dengan @username target. Pengiriman dibatalkan demi mencegah salah kirim.",
        "err.telegram_409": "Error Telegram 409: bot ini masih memiliki webhook aktif sehingga getUpdates diblokir. Buat bot baru lewat @BotFather atau hapus webhook terlebih dahulu.",
        "err.telegram_401": "Token bot Telegram tidak valid (401 Unauthorized). Periksa kembali token dari @BotFather.",
        "err.http_401": "Autentikasi GitHub gagal (401). Token mungkin kedaluwarsa atau dicabut — silakan login ulang.",
        "err.http_403": "Akses GitHub ditolak (403). Pastikan token memiliki scope 'repo' dan 'workflow', atau tunggu jika terkena rate limit.",
        "err.http_404": "Resource GitHub tidak ditemukan (404). Pastikan nama repo, branch, atau file workflow (.github/workflows/streak.yml) tersedia.",
        "err.http_409": "Konflik GitHub (409). Kondisi repositori berubah atau bentrok — muat ulang lalu coba lagi.",
        "err.http_422": "Permintaan ditolak GitHub (422). Repositori mungkin sudah ada, workflow belum aktif, atau nilai secret/input tidak valid.",
        "err.http_429": "Terlalu banyak permintaan (429). Tunggu beberapa saat lalu coba lagi.",
        "err.http_5xx": "Gangguan server ({code}). Server GitHub atau TikTok sedang bermasalah — coba lagi sebentar lagi.",
        "err.network": "Koneksi jaringan gagal atau waktu habis. Periksa koneksi internet serta pengaturan firewall/proxy Anda.",
        "err.device_expired": "Kode login GitHub kedaluwarsa sebelum disetujui. Mulai ulang proses masuk.",
        "err.device_denied": "Otorisasi login GitHub dibatalkan atau ditolak di browser.",
        "err.missing_dep": "Dependensi Python yang dibutuhkan belum terpasang ({detail}). Pasang ulang requirements GUI.",
        "err.scheduler": "Gagal mengatur Windows Task Scheduler: {detail}",
        "err.waf": "TikTok mengembalikan respons verifikasi keamanan / non-JSON. Gunakan Login Browser Terpandu untuk masuk dengan aman.",
    },
}


class I18nManager:
    """Thread-safe bilingual localization manager with observer callbacks."""

    def __init__(self, default_lang: str = "en") -> None:
        self._lock = threading.RLock()
        self._lang: str = _normalize_lang(default_lang)
        self._subscribers: list[Callable[[str], Any]] = []

    def get_language(self) -> str:
        with self._lock:
            return self._lang

    def set_language(self, lang: str) -> str:
        normalized = _normalize_lang(lang)
        callbacks: list[Callable[[str], Any]] = []
        changed = False
        with self._lock:
            if normalized != self._lang:
                self._lang = normalized
                changed = True
                callbacks = list(self._subscribers)
        if changed:
            for fn in callbacks:
                try:
                    fn(normalized)
                except Exception as exc:
                    logger.debug("i18n subscriber failed: %s", exc)
        return normalized

    def available_languages(self) -> tuple[str, ...]:
        return SUPPORTED_LANGUAGES

    def subscribe(self, fn: Callable[[str], Any]) -> None:
        if not callable(fn):
            raise TypeError("i18n subscriber must be callable")
        with self._lock:
            if fn not in self._subscribers:
                self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[str], Any]) -> None:
        with self._lock:
            self._subscribers = [cb for cb in self._subscribers if cb != fn]

    def tr(self, key: str, lang: str | None = None, **kwargs: Any) -> str:
        target = _normalize_lang(lang) if lang is not None else self.get_language()
        template = (
            TRANSLATIONS.get(target, {}).get(key)
            or TRANSLATIONS["en"].get(key)
            or key
        )
        if kwargs:
            try:
                return template.format(**kwargs)
            except Exception:
                return template
        return template

    def friendly_error(self, err_or_msg: Any, lang: str | None = None) -> str:
        """Translate technical exceptions, HTTP statuses, and TikTok errors into user-friendly text."""
        target = _normalize_lang(lang) if lang is not None else self.get_language()
        if err_or_msg is None:
            return self.tr("err.generic", lang=target, detail=self.tr("common.unknown", lang=target))

        cls_name = type(err_or_msg).__name__ if isinstance(err_or_msg, BaseException) else ""
        raw_msg = str(err_or_msg)
        safe_msg = _sanitize_error_text(raw_msg)
        low = safe_msg.lower()

        # Explicit exception classes / domain markers
        if "v20encrypted" in cls_name.lower() or "app-bound" in low or "v20" in re.findall(r"\bv20\b", low):
            return self.tr("err.v20", lang=target)

        if "browserlocked" in cls_name.lower() or "database is locked" in low or "is locked (browser open)" in low or "winerror 32" in low or ("locked" in low and "browser" in low):
            return self.tr("err.browser_locked", lang=target)

        if "inboxunavailable" in cls_name.lower() or "business-suite" in low or "business suite" in low:
            return self.tr("err.inbox_unavailable", lang=target)

        if "captchaverificationrequired" in cls_name.lower():
            return self.tr("accounts.verify_captcha_err", lang=target)

        if "sessionexpired" in cls_name.lower() or "session invalid" in low or "session expired" in low or "session_id is empty" in low or "sessionid is empty" in low or "redirected to login" in low:
            return self.tr("err.session_expired", lang=target)

        if "too many attempts" in low or "maximum number" in low:
            return self.tr("err.too_many_attempts", lang=target)

        if "ratelimit" in cls_name.lower() or "too fast" in low or "action blocked" in low or "quá nhanh" in low or "rate limit" in low:
            return self.tr("err.rate_limit", lang=target)

        if "dmblocked" in cls_name.lower() or "mutual follow" in low or "could not open @" in low or "could not open verified chat" in low:
            return self.tr("err.dm_blocked", lang=target)

        if "recipientmismatch" in cls_name.lower() or "recipient mismatch" in low:
            return self.tr("err.recipient_mismatch", lang=target)

        if "schedulererror" in cls_name.lower() or "schtasks" in low:
            return self.tr("err.scheduler", lang=target, detail=safe_msg)

        # Telegram errors (especially 409 webhook conflict)
        if ("409" in low and ("webhook" in low or "telegram" in low or "getupdates" in low)) or "can't use getupdates method while webhook is active" in low:
            return self.tr("err.telegram_409", lang=target)

        if ("401" in low and "telegram" in low) or ("unauthorized" in low and "api.telegram.org" in low):
            return self.tr("err.telegram_401", lang=target)

        # Device flow
        if "expired_token" in low or "device flow timed out" in low:
            return self.tr("err.device_expired", lang=target)
        if "access_denied" in low:
            return self.tr("err.device_denied", lang=target)

        # Missing Python packages
        if "no module named" in low or isinstance(err_or_msg, ImportError):
            return self.tr("err.missing_dep", lang=target, detail=safe_msg)

        # Non-JSON / WAF
        if "waf" in low or "expecting value" in low or "non-json" in low:
            return self.tr("err.waf", lang=target)

        # HTTP status codes
        status_code = getattr(err_or_msg, "status_code", None) or getattr(err_or_msg, "code", None)
        if status_code is None:
            m = re.search(r"(?:->|http\s*(?:error)?|status)\s*(401|403|404|409|422|429|500|502|503|504)\b", low)
            if m:
                status_code = int(m.group(1))
        if status_code is not None:
            try:
                code = int(status_code)
            except (TypeError, ValueError):
                code = 0
            if code == 401:
                return self.tr("err.http_401", lang=target)
            if code == 403:
                return self.tr("err.http_403", lang=target)
            if code == 404:
                return self.tr("err.http_404", lang=target)
            if code == 409:
                return self.tr("err.http_409", lang=target)
            if code == 422:
                return self.tr("err.http_422", lang=target)
            if code == 429:
                return self.tr("err.http_429", lang=target)
            if 500 <= code <= 599:
                return self.tr("err.http_5xx", lang=target, code=code)

        # Network / timeout
        if any(tok in low for tok in ("timed out", "timeout", "urlerror", "name resolution", "getaddrinfo", "connection refused", "connection reset", "winerror 100")):
            return self.tr("err.network", lang=target)

        return self.tr("err.generic", lang=target, detail=safe_msg or cls_name or "unknown error")


i18n = I18nManager()


def get_language() -> str:
    return i18n.get_language()


def set_language(lang: str) -> str:
    return i18n.set_language(lang)


def tr(key: str, lang: str | None = None, **kwargs: Any) -> str:
    return i18n.tr(key, lang=lang, **kwargs)


def subscribe(fn: Callable[[str], Any]) -> None:
    i18n.subscribe(fn)


def unsubscribe(fn: Callable[[str], Any]) -> None:
    i18n.unsubscribe(fn)


def friendly_error(err_or_msg: Any, lang: str | None = None) -> str:
    return i18n.friendly_error(err_or_msg, lang=lang)


__all__ = [
    "I18nManager",
    "SUPPORTED_LANGUAGES",
    "TRANSLATIONS",
    "friendly_error",
    "get_language",
    "i18n",
    "set_language",
    "subscribe",
    "tr",
    "unsubscribe",
]
