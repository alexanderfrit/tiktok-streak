class TikTokError(Exception):
    action_tip = "Check configuration and log details."


class SessionExpiredError(TikTokError):
    action_tip = "Extract a fresh sessionid cookie from browser and update configuration."


class UserNotFoundError(TikTokError):
    action_tip = "Verify TikTok username handle exists and is spelled correctly."


class DMBlockedError(TikTokError):
    action_tip = "Ensure mutual follow on TikTok or check recipient's Direct Message privacy settings."


class RateLimitError(TikTokError):
    action_tip = "TikTok rate limit active. Stop bot runs for 24 hours to clear cooldown."


class MediaUploadError(TikTokError):
    action_tip = "Check that photo file exists, is under 10MB, and format is supported (PNG/JPG)."


class VideoShareError(TikTokError):
    action_tip = "Check that video URL is public and recipient username is valid in friend list."
