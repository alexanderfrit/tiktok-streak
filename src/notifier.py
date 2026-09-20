from datetime import datetime, timezone, timedelta
import logging, os, urllib.parse, urllib.request

logger = logging.getLogger("tiktok-streak")


def _post_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.debug("Telegram credentials not set; skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Failed sending Telegram alert: %s", e)
        return False


def get_wib_timestamp() -> str:
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S WIB")


def notify_telegram(title: str, details: str, action_tip: str = None, is_error: bool = False) -> bool:
    status_icon = "❌" if is_error else "✅"
    wib_time = get_wib_timestamp()

    msg_lines = [
        f"{status_icon} *{title}*",
        f"🕒 `{wib_time}`",
        f"📝 {details}",
    ]
    if action_tip:
        msg_lines.append(f"💡 *Action:* `{action_tip}`")

    return _post_telegram("\n".join(msg_lines))


def notify_streak_summary(results: list, total_duration: int) -> bool:
    has_failures = any(r.get("failures") for r in results)
    status_icon = "⚠️" if has_failures else "✅"
    wib_time = get_wib_timestamp()

    lines = [
        f"{status_icon} *TikTok Streak Report*",
        f"🕒 `{wib_time}`",
        "",
    ]

    for acc in results:
        name = acc.get("name", "Account")
        friends_total = acc.get("friends_total", 0)
        sent = acc.get("sent", 0)
        shares = acc.get("shares_sent", 0)
        photos = acc.get("photos_sent", 0)
        failures = acc.get("failures", [])

        status_flag = "✓" if not failures else "⚠️"
        lines.append(f"👤 *{name}* {status_flag}")
        lines.append(f"   💬 text: {sent}/{friends_total}")
        lines.append(f"   🎬 video share: {shares}")
        lines.append(f"   🖼️ photo: {photos}")

        # Every failure, in full - not a truncated sample. Failures are the whole
        # point of the alert, so nothing is dropped.
        if failures:
            lines.append(f"   ❌ {len(failures)} failure(s):")
            for fail in failures:
                lines.append(f"      • {fail}")
        lines.append("")

    lines.append(f"⏱️ Total Duration: `{total_duration}s`")
    return _post_telegram("\n".join(lines))
