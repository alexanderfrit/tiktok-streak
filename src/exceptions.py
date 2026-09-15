class TikTokError(Exception):
    action_tip = "Check configuration and log details."


class SessionExpiredError(TikTokError):
    action_tip = "Extract a fresh sessionid cookie from browser and update configuration."


class UserNotFoundError(TikTokError):
    action_tip = "Verify TikTok username handle exists in your inbox or friends list."


class DMBlockedError(TikTokError):
    action_tip = "Ensure mutual follow on TikTok or check recipient's Direct Message privacy settings."


class RateLimitError(TikTokError):
    action_tip = "TikTok rate limit active. Stop bot runs for 24 hours to clear cooldown."
