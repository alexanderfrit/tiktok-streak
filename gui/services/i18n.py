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
        "nav.dashboard": "Dashboard",
        "nav.accounts": "Accounts",
        "nav.content": "Content & Friends",
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

        # Dashboard
        "dashboard.welcome": "Overview",
        "dashboard.status_ready": "Setup is ready to run.",
        "dashboard.status_incomplete": "Complete the checklist below to start sending streaks.",
        "dashboard.accounts_count": "{count} account(s) configured",
        "dashboard.friends_count": "{count} friend(s) total",
        "dashboard.mode_cloud": "Cloud (GitHub Actions)",
        "dashboard.mode_local": "Local PC (Windows Task Scheduler)",
        "dashboard.mode_both": "Hybrid (Local PC + Cloud)",
        "dashboard.run_local_now": "Run Local Streak Now",
        "dashboard.run_cloud_now": "Run Cloud Workflow Now",
        "dashboard.smoke_test": "Run 1-Friend Smoke Test",
        "dashboard.last_test_ok": "Last test passed",
        "dashboard.last_test_pending": "Not tested yet",

        # Accounts / TikTok Auth
        "accounts.title": "TikTok Accounts",
        "accounts.multi_toggle": "Multi-account mode",
        "accounts.add_account": "Add Account",
        "accounts.account_name": "Account Label",
        "accounts.scan_browsers": "Scan Installed Browsers",
        "accounts.read_browser": "Import from Selected Browser",
        "accounts.guided_login": "1-Click Guided Browser Login",
        "accounts.guided_login_hint": "Opens a clean browser window so you can log into TikTok directly — works even when Chrome/Edge uses v20 cookie encryption.",
        "accounts.manual_paste": "Paste Cookie / Session ID",
        "accounts.manual_paste_hint": "Paste Cookie header, JSON cookie export, or raw sessionid.",
        "accounts.verify_inbox": "Verify",
        "accounts.verifying": "Verifying...",
        "accounts.verified": "Verified (@{username})",
        "accounts.inbox_ok": "Web inbox accessible",
        "accounts.inbox_blocked": "Business Suite redirect detected",
        "accounts.not_verified": "Not verified",
        "accounts.local_only_badge": "Local Only",
        "accounts.verify_cloud_warn": "Inbox opened locally, but regional routing cookies for US GitHub Cloud runners are missing. Use Sign in via Browser if Cloud runs fail.",
        "accounts.verify_captcha_err": "TikTok showed a security verification challenge in headless mode. Use Sign in via Browser to verify interactively.",
        "accounts.cookies_cloud_ready": "{count} cookies (Cloud ready)",
        "accounts.cookies_local_only": "{count} cookies (Local only)",
        "accounts.browser_locked_badge": "Browser open (locked)",
        "accounts.v20_badge": "v20 protected — use Guided Login or Paste",
        "accounts.no_tiktok_badge": "No TikTok login found",
        "accounts.has_tiktok_badge": "TikTok session found",

        # Content & Friends
        "content.title": "Message, Media & Friends",
        "content.friends_label": "Target Friends (@usernames)",
        "content.friends_placeholder": "Enter TikTok @usernames separated by commas or new lines (or paste profile URLs)",
        "content.import_csv": "Import friends.csv",
        "content.export_csv": "Export friends.csv",
        "content.message_label": "Streak Message",
        "content.video_toggle": "Send Video Share Card",
        "content.video_url_label": "TikTok Video URL",
        "content.video_resolve": "Verify Link",
        "content.share_times_label": "Share Cards per Friend",
        "content.photo_toggle": "Send Photo Card",
        "content.photo_select": "Choose Custom Photo",
        "content.photo_default": "Using built-in default photo",
        "content.photo_custom": "Custom photo: {path}",
        "content.daily_cap_label": "Daily Friend Cap (0 = unlimited)",
        "content.overrides_title": "Per-Account Overrides",

        # Schedule & Execution
        "schedule.title": "Schedule & Execution",
        "schedule.mode_label": "Execution Mode",
        "schedule.local_headless": "Run local browser silently in background (headless)",
        "schedule.local_times": "Daily Run Times (WIB / UTC+7)",
        "schedule.local_times_tz": "Daily Run Times ({tz})",
        "schedule.timezone": "Schedule Timezone",
        "schedule.cloud_times": "Cloud Daily Run Times ({tz})",
        "schedule.quick_presets": "Quick fill:",
        "schedule.preset_offpeak_1": "1x (08:15)",
        "schedule.preset_offpeak_2": "2x (08:15 & 20:15)",
        "schedule.preset_offpeak_3": "3x (08:15, 14:15 & 20:15)",
        "schedule.raw_cron_toggle": "Advanced: Raw UTC Cron",
        "schedule.cloud_delay_note": "GitHub Actions runs on a shared best-effort queue: schedules at :00 often delay 15 min to 1–4+ hours (for example, 08:00 firing around 11:00–12:00). Pick off-peak minutes like :15 or :17 and leave buffer before midnight, or use Local PC for exact timing.",
        "schedule.sync_windows": "Sync Windows Task Scheduler",
        "schedule.remove_windows": "Remove Windows Tasks",
        "schedule.cloud_cron": "GitHub Actions Cron (UTC)",
        "schedule.cloud_summary": "Cloud schedule: {summary}",
        "schedule.local_tasks_count": "{count} Windows task(s) scheduled",
        "schedule.stop_run": "Stop Run",
        "schedule.test_friend_prompt": "Select friend for 1-friend smoke test",

        # Cloud Deploy & GitHub
        "cloud.title": "GitHub Cloud Deployment",
        "cloud.signin_device": "Sign in with GitHub (1-Click)",
        "cloud.signin_pat": "Or use Personal Access Token (PAT)",
        "cloud.signout": "Sign Out",
        "cloud.signed_in_as": "Signed in as @{login}",
        "cloud.device_code_prompt": "Enter code {code} on GitHub to authorize:",
        "cloud.copy_code": "Copy Code",
        "cloud.open_github": "Open GitHub Authorization",
        "cloud.repo_name": "Repository Name",
        "cloud.private_repo": "Create Private Repository (Recommended — hides your workflow & config)",
        "cloud.public_fork": "Public Fork (from {source})",
        "cloud.deploy": "Deploy & Sync Secrets",
        "cloud.sync_schedule": "Update Cloud Schedule",
        "cloud.check_status": "Check Latest Run",
        "cloud.download_diagnostics": "Download Debug Artifacts",
        "cloud.view_logs": "View Job Logs",
        "cloud.telegram_title": "Telegram Notifications",
        "cloud.telegram_enable": "Enable Telegram reports",
        "cloud.telegram_token": "Bot Token",
        "cloud.telegram_chat_id": "Chat ID",
        "cloud.telegram_detect": "Detect Chat ID",
        "cloud.telegram_test": "Send Test Message",

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
        "nav.content_schedule": "Content & Schedule",
        "nav.run_logs": "Run & Logs",
        "home.checklist_heading": "Setup Checklist",
        "home.go_step": "Go to step",
        "home.step1_title": "1. TikTok Account",
        "home.step1_desc": "Connect at least one TikTok account via Guided Browser Login or cookie paste.",
        "home.step2_title": "2. Friends & Message",
        "home.step2_desc": "Add target @usernames and customize your streak message, video, and photo.",
        "home.step3_title": "3. Delivery (Local / Cloud)",
        "home.step3_desc": "Choose Local PC schedule, GitHub Cloud automation, or both.",
        "home.step4_title": "4. Safety Test & Alerts",
        "home.step4_desc": "Run a visible 1-friend smoke test and optionally connect Telegram alerts.",
        "home.needs_attention": "Needs setup",
        "home.optional_pending": "Optional — ready",
        "accounts.single_mode": "Single Account",
        "accounts.multi_mode": "Multi-Account",
        "accounts.browser_profile": "Browser Profile (Optional Import)",
        "accounts.sign_in_browser": "Sign in via Browser (Recommended)",
        "accounts.import_browser": "Import from Browser",
        "accounts.paste_cookie": "Paste Cookie Manually",
        "accounts.inbox_verified": "Verified",
        "accounts.inbox_blocked": "Business Suite Blocked",
        "accounts.inbox_expired": "Expired",
        "accounts.inbox_unchecked": "Not Checked",
        "accounts.no_session": "No cookie saved",
        "accounts.friends_count": "{count} friend(s)",
        "accounts.scan_not_run": "Click Refresh to scan browser profiles",
        "accounts.multi_hidden_hint": "Single-account mode uses Account #1 only. Your other {extra} saved account(s) stay preserved if you switch back.",
        "accounts.override_hint": "Leave checkboxes unchecked to use the global settings from Content & Schedule.",
        "accounts.override_toggle": "Advanced: Custom per-account overrides (optional)",
        "accounts.override_shares": "Shares:",
        "accounts.override_message": "Override streak message",
        "accounts.override_video": "Override video URL & shares",
        "accounts.override_photo": "Override photo path",
        "accounts.disable_for_account": "Disable for this account",
        "accounts.grab_session_now": "I'm Logged In — Grab Now",
        "cookie_dlg.title": "Paste TikTok Session Cookie",
        "cookie_dlg.steps": "How to copy your cookie safely:\n1. Open https://www.tiktok.com in your browser and make sure you are logged in.\n2. Press F12 to open DevTools, go to Application -> Cookies -> https://www.tiktok.com.\n3. Copy the Value of 'sessionid' (or copy the full Cookie header from the Network tab) and paste it below.",
        "cookie_dlg.placeholder": "Paste sessionid value, Cookie: sessionid=...; ttwid=..., or JSON cookie export here...",
        "cookie_dlg.preview_valid": "Detected sessionid + {count} cookie(s) ({preview})",
        "cookie_dlg.preview_empty": "Waiting for a valid sessionid or cookie payload...",
        "cookie_dlg.invalid_msg": "Please paste a non-empty TikTok sessionid or cookie string.",
        "cookie_dlg.bare_warning": "Bare sessionid detected. On save or Verify, the app checks TikTok's API to fetch regional Cloud cookies; if TikTok WAF blocks it, use Sign in via Browser or paste the full Cookie header.",
        "cloud.bare_deploy_title": "Cookies May Not Work in GitHub Cloud",
        "cloud.bare_deploy_confirm": "These account(s) are missing TikTok regional routing cookies for US GitHub runners: {accounts}.\n\nClick Verify on each account or use Sign in via Browser first. Continue deploying anyway?",
        "device_dlg.title": "Sign in to GitHub",
        "device_dlg.subtitle": "Copy this one-time code, click 'Copy Code & Open GitHub', and paste the code into the GitHub browser page to authorize.",
        "device_dlg.copy_open": "Copy Code & Open GitHub",
        "device_dlg.copied_hint": "Code {code} copied to clipboard! Paste it on the GitHub verification page.",
        "device_dlg.waiting": "Waiting for you to authorize in GitHub...",
        "device_dlg.authorized": "Authorized as @{login}!",
        "content.video_valid": "Video ID {aweme_id}",
        "content.video_disabled": "Video sharing disabled",
        "content.video_hint": "Paste a full tiktok.com/@user/video/... URL, or paste a short vt.tiktok.com link from the phone app and click Verify Link.",
        "content.photo_use_default": "Use Default Photo",
        "content.photo_reset": "Clear / Reset",
        "content.photo_disabled": "Photo sending disabled",
        "content.repo_valid": "Valid repository name",
        "cloud.fetch_repos": "Fetch My Repos",
        "cloud.select_existing_repo": "Choose existing repo...",
        "cloud.repo_private_tag": "Private",
        "cloud.repo_public_tag": "Public",
        "cloud.repo_name_hint": "tiktok-streak or owner/repo-name",
        "cloud.sync_only": "Sync secrets & schedule only",
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
        "schedule.custom_cron": "Custom 5-field UTC Cron",
        "schedule.add_time": "Add Time",
        "schedule.deploy_btn": "Save & Deploy to GitHub",
        "schedule.not_signed_in": "Not signed in to GitHub",
        "schedule.pat_placeholder": "ghp_... or github_pat_... (needs repo + workflow scopes)",
        "schedule.save_pat": "Save PAT",
        "run.local_title": "Local Test & Run",
        "run.quick_test": "Quick Test (1 Friend)",
        "run.full_run": "Run Full Streak Locally",
        "run.show_browser": "Show browser window",
        "run.cloud_title": "GitHub Cloud Monitor",
        "run.cloud_delay_hint": "Scheduled GitHub runs use a shared queue and may start 15m–4h+ after the target minute (especially at :00).",
        "run.run_cloud_btn": "Run on GitHub Now",
        "run.open_run": "Open Run URL",
        "run.open_folder": "Open Folder",
        "run.no_runs": "No workflow runs loaded yet.",
        "run.empty_runs": "No workflow runs loaded yet. Click Refresh or Run on GitHub Now.",
        "run.select_run_hint": "Select a run above to inspect job logs or download debug screenshots.",
        "run.col_status": "Status",
        "run.col_conclusion": "Conclusion",
        "run.col_event": "Trigger",
        "run.col_created": "Created",
        "diag.copy_report": "Copy Diagnostic Report",
        "settings.appearance_group": "Appearance & Language",
        "settings.accent_mode": "Accent Color",
        "settings.accent_system": "Match Windows Personalization",
        "settings.accent_tiktok": "TikTok Coral (#fe2c55)",
        "settings.telegram_group": "Telegram Alerts",
        "settings.workspace_group": "Account, Privacy & Data",
        "settings.open_botfather": "Open @BotFather",
        "settings.cli_export_title": "Developer / CLI Config Export (.env, accounts.json)",
        "settings.cli_export_desc": "Optional: only needed if you also run python main.py manually from source. Normal app users do not need this.",
        "settings.workspace_dir": "CLI Output Folder",
        "settings.workspace_dir_desc": "Destination folder for exported .env, accounts.json & friends.csv",
        "settings.sync_now": "Export CLI Files",
        "settings.import_now": "Import CLI Folder",
        "settings.open_config_dir": "Open App Config Folder",
        "settings.reset_all": "Reset All Settings & Secrets",
        "settings.reset_all_confirm": "This will erase your saved configuration and all stored GitHub tokens, Telegram tokens, and TikTok cookies. Continue?",
        "settings.security_card_title": "Security & Data Privacy",
        "settings.security_card_desc": "How your cookies, tokens, and GitHub secrets are encrypted and stored",

        # Friend Chips & Schedule Section UX
        "content.friend_input_placeholder": "Type @username or paste TikTok profile link, then press Enter...",
        "content.add_friend_btn": "Add",
        "content.friends_empty": "No friends added yet. Type a @username above or click Import friends.csv.",
        "schedule.times_empty": "No run times configured.",
        "cloud.section_account_repo": "Target Repository",
        "cloud.section_schedule": "Daily Cloud Schedule ({tz})",
        "cloud.repo_advanced_toggle": "Advanced Options & PAT",

        # Security & Privacy Trust Transparency
        "security.banner_title": "Local-First & Self-Hosted Privacy",
        "security.banner_desc": "Your TikTok cookies and tokens are encrypted locally via Windows DPAPI and only deployed to your own private GitHub repository — never to any third-party server.",
        "security.how_protected_btn": "Security & Privacy",
        "security.dialog_title": "Security, Privacy & Data Handling",
        "security.dialog_subtitle": "This application is 100% open-source and operates without any developer or middleman server. Here is how your sensitive credentials are handled:",
        "security.point1_title": "1. Encrypted on Your PC (Windows DPAPI & Credential Manager)",
        "security.point1_desc": "Saved TikTok session cookies, GitHub tokens, and Telegram bot tokens are encrypted using Windows Credential Manager or Windows DPAPI (CryptProtectData in %APPDATA%\\TikTokStreak\\secrets.dpapi), bound to your Windows user login. They are never saved in plaintext inside config.json.",
        "security.point2_title": "2. Strictly Scoped Network Connections (Zero Telemetry)",
        "security.point2_desc": "The app only communicates with: (a) *.tiktok.com to verify sessions and send DMs, (b) github.com / api.github.com when you sign in or deploy your workflow, and (c) api.telegram.org if you enable Telegram alerts. No telemetry, analytics, or external servers exist.",
        "security.point3_title": "3. Client-Side GitHub Secret Sealing (NaCl SealedBox)",
        "security.point3_desc": "When you click Save & Deploy to GitHub, your secrets are encrypted on your PC using PyNaCl (Libsodium SealedBox) with your own repository's public key before upload. Only GitHub Actions inside your repository can decrypt them during a scheduled run.",
        "security.point4_title": "4. Read-Only Browser Import & Automatic Log Redaction",
        "security.point4_desc": "Browser cookie import queries only tiktok.com rows in read-only mode (?mode=ro). All UI logs, diagnostic reports, and scheduled_run.log files automatically redact sessionid, Cookie headers, and API tokens.",
        "security.open_config_btn": "Open Local Config Folder",
        "security.open_repo_btn": "View Source Code on GitHub",

        # Headful Guard, Cloud Control & Auto-Updater
        "run.show_browser_hint": "Visible browser: inbox list clicks are automatically locked while sending to prevent misdirected messages.",
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
        "cloud.repo_status_existing_bot": "Existing bot repo ({state}) — Deploy will update it",
        "cloud.repo_status_non_bot": "Repository already exists without bot workflow — pick another name or confirm overwrite",
        "cloud.repo_status_new": "New repository — Deploy will create it",
        "cloud.dup_deploy_title": "Other Active Bot Repository Found",
        "cloud.dup_deploy_body": "You already have active bot schedule(s) on:\n{repos}\n\nDeploying to '{target}' will leave multiple schedules running at the same time unless the old one is paused.",
        "cloud.dup_deploy_pause_btn": "Pause Old & Deploy",
        "cloud.dup_deploy_keep_btn": "Deploy Anyway",
        "cloud.non_bot_confirm_title": "Existing Repository Detected",
        "cloud.non_bot_confirm_body": "Repository '{repo}' already exists on your GitHub account and does not have .github/workflows/streak.yml yet. Push TikTok Streak files into it?",
        "settings.updates_group": "About & Updates",
        "settings.app_version_title": "TikTok Streak Setup v{version}",
        "settings.app_version_uptodate": "You are using the latest version.",
        "settings.app_version_available": "New version v{version} is available!",
        "settings.check_updates_btn": "Check for Updates",
        "settings.checking_updates": "Checking for updates...",
        "settings.auto_check_updates": "Check for updates automatically on startup",
        "update.banner_title": "Update Available: v{version}",
        "update.banner_desc": "Includes important bug fixes and stability improvements. Update now to stay protected.",
        "update.btn_now": "Update Now",
        "update.btn_later": "Remind Later",
        "update.btn_skip": "Skip Version",
        "update.dlg_title": "Software Update — v{latest}",
        "update.dlg_subtitle": "Installed: v{current}  →  Latest: v{latest}",
        "update.dlg_no_notes": "A new release is available on GitHub Releases.",
        "update.dlg_download_install": "Download & Install",
        "update.dlg_open_release": "Open GitHub Release",
        "update.dlg_downloading": "Downloading update ({downloaded} / {total})...",
        "update.dlg_verifying": "Verifying integrity (SHA-256) and restarting...",
        "update.dlg_source_note": "Running from Python source — pull the latest repository commits or download the EXE from GitHub Releases.",
        "update.cloud_sync_title": "Cloud Bot Needs Re-Deploy",
        "update.cloud_sync_desc": "Your app is v{app_ver}, while your last GitHub deploy used {deployed_ver}. Click 'Save & Deploy' in Content & Schedule so GitHub Actions gets the latest fixes.",
        "update.cloud_sync_btn": "Go to Deploy",

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
        "nav.content": "Konten & Teman",
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

        # Dashboard
        "dashboard.welcome": "Ringkasan",
        "dashboard.status_ready": "Konfigurasi siap dijalankan.",
        "dashboard.status_incomplete": "Lengkapi daftar di bawah untuk mulai mengirim streak.",
        "dashboard.accounts_count": "{count} akun terkonfigurasi",
        "dashboard.friends_count": "{count} total teman",
        "dashboard.mode_cloud": "Cloud (GitHub Actions)",
        "dashboard.mode_local": "PC Lokal (Windows Task Scheduler)",
        "dashboard.mode_both": "Hybrid (PC Lokal + Cloud)",
        "dashboard.run_local_now": "Jalankan Streak Lokal",
        "dashboard.run_cloud_now": "Jalankan Workflow Cloud",
        "dashboard.smoke_test": "Uji Kirim ke 1 Teman",
        "dashboard.last_test_ok": "Uji terakhir berhasil",
        "dashboard.last_test_pending": "Belum pernah diuji",

        # Accounts / TikTok Auth
        "accounts.title": "Akun TikTok",
        "accounts.multi_toggle": "Mode banyak akun",
        "accounts.add_account": "Tambah Akun",
        "accounts.account_name": "Label Akun",
        "accounts.scan_browsers": "Pindai Browser Terpasang",
        "accounts.read_browser": "Ambil dari Browser Terpilih",
        "accounts.guided_login": "Login Browser Terpandu (1-Klik)",
        "accounts.guided_login_hint": "Membuka jendela browser terisolasi untuk login TikTok langsung — tetap berhasil meski Chrome/Edge memakai enkripsi v20.",
        "accounts.manual_paste": "Tempel Cookie / Session ID",
        "accounts.manual_paste_hint": "Tempel header Cookie, JSON cookie, atau sessionid langsung.",
        "accounts.verify_inbox": "Verifikasi",
        "accounts.verifying": "Memverifikasi...",
        "accounts.verified": "Terverifikasi (@{username})",
        "accounts.inbox_ok": "Inbox web dapat diakses",
        "accounts.inbox_blocked": "Dialihkan ke Business Suite",
        "accounts.not_verified": "Belum diverifikasi",
        "accounts.local_only_badge": "Lokal Saja",
        "accounts.verify_cloud_warn": "Inbox terbuka secara lokal, tetapi cookie rute regional untuk runner GitHub Cloud AS belum lengkap. Gunakan Masuk lewat Browser jika run Cloud gagal.",
        "accounts.verify_captcha_err": "TikTok menampilkan tantangan verifikasi keamanan pada mode headless. Gunakan Masuk lewat Browser untuk verifikasi langsung.",
        "accounts.cookies_cloud_ready": "{count} cookie (Siap Cloud)",
        "accounts.cookies_local_only": "{count} cookie (Lokal saja)",
        "accounts.browser_locked_badge": "Browser terbuka (terkunci)",
        "accounts.v20_badge": "Terproteksi v20 — gunakan Login Terpandu atau Tempel",
        "accounts.no_tiktok_badge": "Belum ada login TikTok",
        "accounts.has_tiktok_badge": "Sesi TikTok ditemukan",

        # Content & Friends
        "content.title": "Pesan, Media & Teman",
        "content.friends_label": "Daftar Teman (@username)",
        "content.friends_placeholder": "Masukkan @username TikTok dipisahkan koma atau baris baru (atau tempel URL profil)",
        "content.import_csv": "Impor friends.csv",
        "content.export_csv": "Ekspor friends.csv",
        "content.message_label": "Pesan Streak",
        "content.video_toggle": "Kirim Kartu Share Video",
        "content.video_url_label": "URL Video TikTok",
        "content.video_resolve": "Verifikasi Link",
        "content.share_times_label": "Jumlah Share per Teman",
        "content.photo_toggle": "Kirim Kartu Foto",
        "content.photo_select": "Pilih Foto Kustom",
        "content.photo_default": "Menggunakan foto bawaan aplikasi",
        "content.photo_custom": "Foto kustom: {path}",
        "content.daily_cap_label": "Batas Teman Harian (0 = tanpa batas)",
        "content.overrides_title": "Pengaturan Khusus Akun",

        # Schedule & Execution
        "schedule.title": "Jadwal & Eksekusi",
        "schedule.mode_label": "Mode Eksekusi",
        "schedule.local_headless": "Jalankan browser lokal di latar belakang (headless)",
        "schedule.local_times": "Jam Jalan Harian (WIB / UTC+7)",
        "schedule.local_times_tz": "Jam Jalan Harian ({tz})",
        "schedule.timezone": "Zona Waktu Jadwal",
        "schedule.cloud_times": "Jam Jalan Cloud Harian ({tz})",
        "schedule.quick_presets": "Isi cepat:",
        "schedule.preset_offpeak_1": "1x (08:15)",
        "schedule.preset_offpeak_2": "2x (08:15 & 20:15)",
        "schedule.preset_offpeak_3": "3x (08:15, 14:15 & 20:15)",
        "schedule.raw_cron_toggle": "Lanjutan: Cron UTC Mentah",
        "schedule.cloud_delay_note": "GitHub Actions memakai antrean bersama (best-effort): jadwal tepat jam :00 sering terlambat 15 menit hingga 1–4+ jam (mis. 08:00 baru berjalan sekitar 11:00–12:00). Pilih menit sepi seperti :15 atau :17 dan beri jarak sebelum tengah malam, atau gunakan PC Lokal untuk waktu tepat.",
        "schedule.sync_windows": "Sinkronkan Windows Task Scheduler",
        "schedule.remove_windows": "Hapus Tugas Windows",
        "schedule.cloud_cron": "Cron GitHub Actions (UTC)",
        "schedule.cloud_summary": "Jadwal cloud: {summary}",
        "schedule.local_tasks_count": "{count} tugas Windows terjadwal",
        "schedule.stop_run": "Hentikan",
        "schedule.test_friend_prompt": "Pilih teman untuk uji kirim 1 teman",

        # Cloud Deploy & GitHub
        "cloud.title": "Deploy Cloud GitHub",
        "cloud.signin_device": "Masuk dengan GitHub (1-Klik)",
        "cloud.signin_pat": "Atau gunakan Personal Access Token (PAT)",
        "cloud.signout": "Keluar",
        "cloud.signed_in_as": "Masuk sebagai @{login}",
        "cloud.device_code_prompt": "Masukkan kode {code} di GitHub untuk menyetujui:",
        "cloud.copy_code": "Salin Kode",
        "cloud.open_github": "Buka Halaman Otorisasi GitHub",
        "cloud.repo_name": "Nama Repositori",
        "cloud.private_repo": "Buat Repositori Privat (Disarankan — menyembunyikan workflow & konfigurasi)",
        "cloud.public_fork": "Fork Publik (dari {source})",
        "cloud.deploy": "Deploy & Sinkronkan Secret",
        "cloud.sync_schedule": "Perbarui Jadwal Cloud",
        "cloud.check_status": "Cek Status Run Terbaru",
        "cloud.download_diagnostics": "Unduh Artefak Debug",
        "cloud.view_logs": "Lihat Log Job",
        "cloud.telegram_title": "Notifikasi Telegram",
        "cloud.telegram_enable": "Aktifkan laporan Telegram",
        "cloud.telegram_token": "Token Bot",
        "cloud.telegram_chat_id": "Chat ID",
        "cloud.telegram_detect": "Deteksi Chat ID",
        "cloud.telegram_test": "Kirim Pesan Uji",

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
        "nav.content_schedule": "Konten & Jadwal",
        "nav.run_logs": "Jalankan & Log",
        "home.checklist_heading": "Daftar Persiapan",
        "home.go_step": "Buka langkah",
        "home.step1_title": "1. Akun TikTok",
        "home.step1_desc": "Hubungkan minimal satu akun TikTok lewat Login Browser Terpandu atau tempel cookie.",
        "home.step2_title": "2. Teman & Pesan",
        "home.step2_desc": "Tambahkan target @username dan atur pesan streak, video, serta foto.",
        "home.step3_title": "3. Pengiriman (Lokal / Cloud)",
        "home.step3_desc": "Pilih jadwal PC Lokal, otomatisasi Cloud GitHub, atau keduanya.",
        "home.step4_title": "4. Uji Aman & Notifikasi",
        "home.step4_desc": "Jalankan uji kirim terlihat ke 1 teman dan aktifkan laporan Telegram bila perlu.",
        "home.needs_attention": "Perlu diatur",
        "home.optional_pending": "Opsional — siap",
        "accounts.single_mode": "1 Akun",
        "accounts.multi_mode": "Multi-Akun",
        "accounts.browser_profile": "Profil Browser (Impor Opsional)",
        "accounts.sign_in_browser": "Masuk lewat Browser (Disarankan)",
        "accounts.import_browser": "Impor dari Browser",
        "accounts.paste_cookie": "Tempel Cookie Manual",
        "accounts.inbox_verified": "Terverifikasi",
        "accounts.inbox_blocked": "Diblokir Business Suite",
        "accounts.inbox_expired": "Kedaluwarsa",
        "accounts.inbox_unchecked": "Belum Dicek",
        "accounts.no_session": "Belum ada cookie",
        "accounts.friends_count": "{count} teman",
        "accounts.scan_not_run": "Klik Muat Ulang untuk memindai profil browser",
        "accounts.multi_hidden_hint": "Mode 1 akun hanya memakai Akun #1. {extra} akun tersimpan lainnya tetap aman jika Anda kembali ke mode multi-akun.",
        "accounts.override_hint": "Biarkan tidak dicentang untuk mengikuti pengaturan global di Konten & Jadwal.",
        "accounts.override_toggle": "Lanjutan: Pengaturan khusus akun ini (opsional)",
        "accounts.override_shares": "Share:",
        "accounts.override_message": "Ubah pesan streak khusus akun ini",
        "accounts.override_video": "Ubah URL video & jumlah share khusus akun ini",
        "accounts.override_photo": "Ubah foto khusus akun ini",
        "accounts.disable_for_account": "Nonaktifkan untuk akun ini",
        "accounts.grab_session_now": "Sudah Login — Ambil Sekarang",
        "cookie_dlg.title": "Tempel Cookie Sesi TikTok",
        "cookie_dlg.steps": "Cara menyalin cookie dengan aman:\n1. Buka https://www.tiktok.com di browser dan pastikan sudah login.\n2. Tekan F12 untuk membuka DevTools -> Application -> Cookies -> https://www.tiktok.com.\n3. Salin Value dari 'sessionid' (atau salin seluruh header Cookie dari tab Network) lalu tempel di bawah.",
        "cookie_dlg.placeholder": "Tempel nilai sessionid, Cookie: sessionid=...; ttwid=..., atau JSON cookie di sini...",
        "cookie_dlg.preview_valid": "Terdeteksi sessionid + {count} cookie ({preview})",
        "cookie_dlg.preview_empty": "Menunggu sessionid atau data cookie yang valid...",
        "cookie_dlg.invalid_msg": "Silakan tempel sessionid atau string cookie TikTok yang valid.",
        "cookie_dlg.bare_warning": "Hanya sessionid yang terdeteksi. Saat disimpan atau Verifikasi, aplikasi mencoba mengambil cookie Cloud regional lewat API TikTok; jika diblokir WAF, gunakan Masuk lewat Browser atau tempel header Cookie lengkap.",
        "cloud.bare_deploy_title": "Cookie Mungkin Belum Siap untuk Cloud GitHub",
        "cloud.bare_deploy_confirm": "Akun berikut belum memiliki cookie rute regional TikTok untuk runner GitHub AS: {accounts}.\n\nKlik Verifikasi pada tiap akun atau gunakan Masuk lewat Browser terlebih dahulu. Tetap lanjutkan deploy?",
        "device_dlg.title": "Masuk ke GitHub",
        "device_dlg.subtitle": "Salin kode sekali pakai ini, klik 'Salin Kode & Buka GitHub', lalu tempel kode di halaman GitHub untuk menyetujui.",
        "device_dlg.copy_open": "Salin Kode & Buka GitHub",
        "device_dlg.copied_hint": "Kode {code} disalin ke clipboard! Tempel kode tersebut di halaman verifikasi GitHub.",
        "device_dlg.waiting": "Menunggu Anda menyetujui di halaman GitHub...",
        "device_dlg.authorized": "Berhasil masuk sebagai @{login}!",
        "content.video_valid": "ID Video {aweme_id}",
        "content.video_disabled": "Pengiriman video dinonaktifkan",
        "content.video_hint": "Tempel URL lengkap tiktok.com/@user/video/..., atau tempel link pendek vt.tiktok.com dari HP lalu klik Verifikasi Link.",
        "content.photo_use_default": "Pakai Foto Bawaan",
        "content.photo_reset": "Hapus / Reset",
        "content.photo_disabled": "Pengiriman foto dinonaktifkan",
        "content.repo_valid": "Nama repositori valid",
        "cloud.fetch_repos": "Muat Repo Saya",
        "cloud.select_existing_repo": "Pilih repo yang ada...",
        "cloud.repo_private_tag": "Privat",
        "cloud.repo_public_tag": "Publik",
        "cloud.repo_name_hint": "tiktok-streak atau owner/nama-repo",
        "cloud.sync_only": "Sinkronkan secret & jadwal saja",
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
        "schedule.custom_cron": "Cron UTC 5-kolom Kustom",
        "schedule.add_time": "Tambah Jam",
        "schedule.deploy_btn": "Simpan & Deploy ke GitHub",
        "schedule.not_signed_in": "Belum masuk ke GitHub",
        "schedule.pat_placeholder": "ghp_... atau github_pat_... (butuh scope repo + workflow)",
        "schedule.save_pat": "Simpan PAT",
        "run.local_title": "Uji & Jalankan di PC Lokal",
        "run.quick_test": "Tes Cepat (1 Teman)",
        "run.full_run": "Jalankan Streak Penuh Lokal",
        "run.show_browser": "Tampilkan jendela browser",
        "run.cloud_title": "Monitor Cloud GitHub",
        "run.cloud_delay_hint": "Run terjadwal GitHub memakai antrean bersama dan bisa mulai 15m–4j+ setelah menit target (terutama pada :00).",
        "run.run_cloud_btn": "Jalankan di GitHub Sekarang",
        "run.open_run": "Buka URL Run",
        "run.open_folder": "Buka Folder",
        "run.no_runs": "Belum ada riwayat workflow yang dimuat.",
        "run.empty_runs": "Belum ada riwayat proses. Klik Muat Ulang atau Jalankan di GitHub.",
        "run.select_run_hint": "Pilih salah satu proses di atas untuk melihat log atau screenshot debug.",
        "run.col_status": "Status",
        "run.col_conclusion": "Hasil",
        "run.col_event": "Pemicu",
        "run.col_created": "Waktu Dibuat",
        "diag.copy_report": "Salin Laporan Diagnostik",
        "settings.appearance_group": "Tampilan & Bahasa",
        "settings.accent_mode": "Warna Aksen",
        "settings.accent_system": "Ikuti Personalisasi Windows",
        "settings.accent_tiktok": "TikTok Coral (#fe2c55)",
        "settings.telegram_group": "Notifikasi Telegram",
        "settings.workspace_group": "Akun, Privasi & Data",
        "settings.open_botfather": "Buka @BotFather",
        "settings.cli_export_title": "Ekspor Konfigurasi CLI Developer (.env, accounts.json)",
        "settings.cli_export_desc": "Opsional: hanya untuk developer yang menjalankan python main.py manual dari source. Pengguna biasa tidak memerlukan ini.",
        "settings.workspace_dir": "Folder Output CLI",
        "settings.workspace_dir_desc": "Folder tujuan untuk ekspor .env, accounts.json & friends.csv",
        "settings.sync_now": "Ekspor File CLI",
        "settings.import_now": "Impor Folder CLI",
        "settings.open_config_dir": "Buka Folder Konfigurasi Aplikasi",
        "settings.reset_all": "Reset Semua Pengaturan & Rahasia",
        "settings.reset_all_confirm": "Ini akan menghapus seluruh konfigurasi tersimpan beserta token GitHub, token Telegram, dan cookie TikTok. Lanjutkan?",
        "settings.security_card_title": "Keamanan & Privasi Data",
        "settings.security_card_desc": "Cara cookie, token, dan secret GitHub Anda dienkripsi serta disimpan",

        # Friend Chips & Schedule Section UX
        "content.friend_input_placeholder": "Ketik @username atau tempel link profil TikTok, lalu tekan Enter...",
        "content.add_friend_btn": "Tambah",
        "content.friends_empty": "Belum ada teman. Ketik @username di atas atau klik Impor friends.csv.",
        "schedule.times_empty": "Belum ada jam jalan terjadwal.",
        "cloud.section_account_repo": "Repositori Target",
        "cloud.section_schedule": "Jadwal Cloud Harian ({tz})",
        "cloud.repo_advanced_toggle": "Opsi Lanjutan & PAT",

        # Security & Privacy Trust Transparency
        "security.banner_title": "Privasi Lokal & Self-Hosted",
        "security.banner_desc": "Cookie TikTok dan token Anda dienkripsi di PC menggunakan Windows DPAPI dan hanya dikirim ke repositori GitHub privat milik Anda sendiri — tanpa server perantara.",
        "security.how_protected_btn": "Keamanan & Privasi",
        "security.dialog_title": "Keamanan, Privasi & Penanganan Data",
        "security.dialog_subtitle": "Aplikasi ini 100% open-source dan berjalan tanpa server pengembang atau perantara. Berikut cara data sensitif Anda dilindungi:",
        "security.point1_title": "1. Dienkripsi di PC Anda (Windows DPAPI & Credential Manager)",
        "security.point1_desc": "Cookie sesi TikTok, token GitHub, dan token Telegram disimpan terenkripsi di Windows Credential Manager atau Windows DPAPI (CryptProtectData di %APPDATA%\\TikTokStreak\\secrets.dpapi) yang terikat pada akun login Windows Anda. Kredensial tidak pernah disimpan dalam teks biasa di config.json.",
        "security.point2_title": "2. Koneksi Jaringan Terbatas (Tanpa Telemetri)",
        "security.point2_desc": "Aplikasi hanya terhubung ke: (a) *.tiktok.com untuk verifikasi sesi dan kirim DM, (b) github.com / api.github.com saat login atau deploy workflow Anda, serta (c) api.telegram.org bila notifikasi Telegram diaktifkan. Tidak ada telemetri, analitik, atau server pihak ketiga.",
        "security.point3_title": "3. Enkripsi Secret GitHub di Sisi Klien (NaCl SealedBox)",
        "security.point3_desc": "Saat Anda menekan Simpan & Deploy ke GitHub, secret dienkripsi langsung di PC Anda memakai PyNaCl (Libsodium SealedBox) dengan kunci publik repositori Anda sebelum diunggah. Hanya GitHub Actions di repositori Anda yang dapat membukanya.",
        "security.point4_title": "4. Impor Browser Read-Only & Sensor Log Otomatis",
        "security.point4_desc": "Impor cookie browser hanya membaca domain tiktok.com dalam mode baca-saja (?mode=ro). Seluruh log tampilan, laporan diagnostik, dan file scheduled_run.log otomatis menyensor nilai sessionid, header Cookie, serta token API.",
        "security.open_config_btn": "Buka Folder Konfigurasi Lokal",
        "security.open_repo_btn": "Lihat Kode Sumber di GitHub",

        # Headful Guard, Cloud Control & Auto-Updater
        "run.show_browser_hint": "Browser terlihat: klik daftar inbox dikunci otomatis saat mengirim agar pesan tidak salah kirim.",
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
        "cloud.repo_status_existing_bot": "Repo bot sudah ada ({state}) — Deploy akan memperbaruinya",
        "cloud.repo_status_non_bot": "Repo sudah ada tetapi bukan repo bot — pilih nama lain atau konfirmasi",
        "cloud.repo_status_new": "Repo baru — Deploy akan membuatnya",
        "cloud.dup_deploy_title": "Ada Repo Bot Lain yang Masih Aktif",
        "cloud.dup_deploy_body": "Anda sudah memiliki jadwal bot yang aktif di:\n{repos}\n\nJika lanjut deploy ke '{target}', beberapa jadwal akan berjalan bersamaan kecuali repo lama dijeda.",
        "cloud.dup_deploy_pause_btn": "Jeda Repo Lama & Deploy",
        "cloud.dup_deploy_keep_btn": "Tetap Deploy",
        "cloud.non_bot_confirm_title": "Repositori Sudah Ada",
        "cloud.non_bot_confirm_body": "Repositori '{repo}' sudah ada di akun GitHub Anda dan belum memiliki .github/workflows/streak.yml. Tetap kirim file bot TikTok Streak ke dalamnya?",
        "settings.updates_group": "Tentang & Pembaruan",
        "settings.app_version_title": "TikTok Streak Setup v{version}",
        "settings.app_version_uptodate": "Anda sudah memakai versi terbaru.",
        "settings.app_version_available": "Versi baru v{version} tersedia!",
        "settings.check_updates_btn": "Cek Pembaruan",
        "settings.checking_updates": "Memeriksa pembaruan...",
        "settings.auto_check_updates": "Cek pembaruan otomatis saat aplikasi dibuka",
        "update.banner_title": "Pembaruan Tersedia: v{version}",
        "update.banner_desc": "Berisi perbaikan bug penting dan peningkatan stabilitas. Segera perbarui aplikasi.",
        "update.btn_now": "Perbarui Sekarang",
        "update.btn_later": "Ingatkan Nanti",
        "update.btn_skip": "Lewati Versi",
        "update.dlg_title": "Pembaruan Aplikasi — v{latest}",
        "update.dlg_subtitle": "Terpasang: v{current}  →  Terbaru: v{latest}",
        "update.dlg_no_notes": "Rilis baru sudah tersedia di GitHub Releases.",
        "update.dlg_download_install": "Unduh & Pasang",
        "update.dlg_open_release": "Buka Halaman Rilis",
        "update.dlg_downloading": "Mengunduh pembaruan ({downloaded} / {total})...",
        "update.dlg_verifying": "Memverifikasi integritas (SHA-256) & memulai ulang...",
        "update.dlg_source_note": "Berjalan dari kode Python — tarik commit terbaru atau unduh EXE dari GitHub Releases.",
        "update.cloud_sync_title": "Bot GitHub Cloud Perlu Deploy Ulang",
        "update.cloud_sync_desc": "Aplikasi Anda sudah v{app_ver}, sedangkan deploy GitHub terakhir masih versi {deployed_ver}. Klik 'Simpan & Deploy' di Konten & Jadwal agar GitHub Actions memakai perbaikan terbaru.",
        "update.cloud_sync_btn": "Ke Halaman Deploy",

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
