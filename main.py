import sys, time
from dotenv import load_dotenv
from utils import (
    init_browser,
    login_tiktok,
    auto_send_message,
    notify_telegram,
    logger,
    TikTokError,
)

load_dotenv()

if __name__ == "__main__":
    start_time = time.time()
    logger.info("=== Starting TikTok Streak Auto Send ===")
    browser = None
    try:
        browser, wait = init_browser()
        login_tiktok(browser, wait)
        sent_count, total_friends, failures = auto_send_message(browser, wait)

        elapsed = round(time.time() - start_time)

        if failures:
            failure_summary = "\n".join(f"• {f}" for f in failures)
            action_tip = "Check error list; ensure mutual follow and correct handles."
            notify_telegram(
                title=f"TikTok Streak Partial/Failed ({sent_count}/{total_friends})",
                details=f"Targets: {sent_count}/{total_friends} sent in {elapsed}s\n{failure_summary}",
                action_tip=action_tip,
                is_error=True,
            )
            if sent_count == 0:
                sys.exit(1)
        else:
            notify_telegram(
                title="TikTok Streak Sent",
                details=f"All {sent_count} messages sent successfully in {elapsed}s.",
                action_tip=None,
                is_error=False,
            )

    except Exception as e:
        elapsed = round(time.time() - start_time)
        logger.error("Run failed: %s", e)
        action_tip = getattr(e, "action_tip", "Check GitHub Actions artifacts or local logs.")
        notify_telegram(
            title="TikTok Streak Failed",
            details=f"Error after {elapsed}s: {e}",
            action_tip=action_tip,
            is_error=True,
        )
        sys.exit(1)

    finally:
        if browser:
            browser.quit()
        logger.info("=== Process Finished. Browser closed. ===")
