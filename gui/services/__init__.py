"""Pure-Python backend services for the TikTok Streak desktop GUI.

All modules in this package have zero Qt UI dependencies and avoid top-level
heavy imports (Selenium, Pillow, PyNaCl) so unit tests and QThread workers
can import them quickly and safely.
"""
from gui.services.browsers import (
    BrowserLocked,
    TIKTOK_DOMAIN,
    V20EncryptedError,
    grab_session,
    list_sources,
    read_source,
)
from gui.services.github_api import (
    DEFAULT_SOURCE_REPO,
    OAUTH_CLIENT_ID,
    SECRET_NAMES,
    WORKFLOW_FILE,
    GitHub,
    GitHubError,
    device_flow_poll,
    device_flow_start,
    has_required_scopes,
    resolve_source_dir,
    telegram_find_chat_id,
    telegram_test,
)
from gui.services.i18n import (
    I18nManager,
    SUPPORTED_LANGUAGES,
    friendly_error,
    get_language,
    i18n,
    set_language,
    subscribe,
    tr,
    unsubscribe,
)
from gui.services.local_runner import LocalRunner
from gui.services.scheduler import (
    SchedulerError,
    TASK_PREFIX,
    WindowsScheduler,
    build_bot_command,
    list_tasks,
    remove_all_tasks,
    sync_daily_tasks,
)
from gui.services.storage import (
    AccountProfile,
    AppState,
    ConfigStore,
    DEFAULT_PHOTO_PATH,
    DEFAULT_VIDEO_URL,
    SecretStore,
    default_config_dir,
    export_local_workspace,
    import_local_workspace,
    redact_secrets,
)
from gui.services.tiktok_auth import (
    check_session_api,
    extract_session_summary,
    guided_browser_login,
    parse_cookie_payload,
    verify_inbox_headless,
)
from gui.services.validators import (
    cron_to_wib_summary,
    export_friends_csv,
    import_friends_csv,
    normalize_friends,
    parse_aweme_id,
    prepare_photo_secret,
    times_to_utc_cron,
    validate_repo_name,
    validate_telegram_token,
    validate_video_url,
)

__all__ = [
    # i18n
    "I18nManager",
    "SUPPORTED_LANGUAGES",
    "friendly_error",
    "get_language",
    "i18n",
    "set_language",
    "subscribe",
    "tr",
    "unsubscribe",
    # storage
    "AccountProfile",
    "AppState",
    "ConfigStore",
    "DEFAULT_PHOTO_PATH",
    "DEFAULT_VIDEO_URL",
    "SecretStore",
    "default_config_dir",
    "export_local_workspace",
    "import_local_workspace",
    "redact_secrets",
    # validators
    "cron_to_wib_summary",
    "export_friends_csv",
    "import_friends_csv",
    "normalize_friends",
    "parse_aweme_id",
    "prepare_photo_secret",
    "times_to_utc_cron",
    "validate_repo_name",
    "validate_telegram_token",
    "validate_video_url",
    # browsers
    "BrowserLocked",
    "TIKTOK_DOMAIN",
    "V20EncryptedError",
    "grab_session",
    "list_sources",
    "read_source",
    # tiktok_auth
    "check_session_api",
    "extract_session_summary",
    "guided_browser_login",
    "parse_cookie_payload",
    "verify_inbox_headless",
    # github_api
    "DEFAULT_SOURCE_REPO",
    "GitHub",
    "GitHubError",
    "OAUTH_CLIENT_ID",
    "SECRET_NAMES",
    "WORKFLOW_FILE",
    "device_flow_poll",
    "device_flow_start",
    "has_required_scopes",
    "resolve_source_dir",
    "telegram_find_chat_id",
    "telegram_test",
    # local_runner
    "LocalRunner",
    # scheduler
    "SchedulerError",
    "TASK_PREFIX",
    "WindowsScheduler",
    "build_bot_command",
    "list_tasks",
    "remove_all_tasks",
    "sync_daily_tasks",
]
