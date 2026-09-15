import logging, sys, time
from src.browser import init_browser, authenticate_session
from src.config import load_accounts
from src.actions import send_streak_message
from src.notifier import notify_streak_summary, notify_telegram

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")


def run() -> None:
    start_time = time.time()
    logger.info("=== Starting TikTok Streak Automation ===")

    # Support CLI flag: python main.py --headful
    force_headful = "--headful" in sys.argv or "--visible" in sys.argv
    headless = False if force_headful else None

    accounts = load_accounts()
    if not accounts:
        logger.error("No TikTok accounts configured. Please check .env or accounts.json.")
        notify_telegram(
            title="TikTok Streak Configuration Missing",
            details="No accounts or sessionid found.",
            action_tip="Create .env or accounts.json with valid session_id.",
            is_error=True,
        )
        sys.exit(1)

    logger.info("Found %d account(s) to process.", len(accounts))

    browser = None
    all_results = []
    has_critical_failure = False

    try:
        browser, wait = init_browser(headless=headless)

        for a_idx, account in enumerate(accounts, start=1):
            logger.info("--- [%d/%d] Processing Account: %s ---", a_idx, len(accounts), account.name)
            acc_result = {
                "name": account.name,
                "friends_total": len(account.friends),
                "sent": 0,
                "failures": [],
            }

            try:
                authenticate_session(browser, account.session_id, account.name)
            except Exception as e:
                err = f"Authentication failed: {e}"
                logger.error("[%s] %s", account.name, err)
                acc_result["failures"].append(err)
                all_results.append(acc_result)
                has_critical_failure = True
                continue

            if not account.friends:
                logger.warning("[%s] No target friends configured. Skipping.", account.name)
                acc_result["failures"].append("No friends specified in friends list")
                all_results.append(acc_result)
                continue

            for f_idx, friend in enumerate(account.friends, start=1):
                logger.info("[%s] [%d/%d] Sending streak message to @%s...", account.name, f_idx, len(account.friends), friend)
                res = send_streak_message(
                    browser=browser,
                    friend=friend,
                    message_text=account.message,
                )

                if res["sent"]:
                    acc_result["sent"] += 1
                    logger.info("[%s] Streak message delivered to @%s.", account.name, friend)
                else:
                    err = f"@{friend}: {res['error']}"
                    acc_result["failures"].append(err)

            all_results.append(acc_result)

    except Exception as e:
        logger.exception("Fatal engine failure: %s", e)
        notify_telegram(
            title="TikTok Streak Fatal Error",
            details=f"Fatal exception during execution: {e}",
            action_tip="Check logs or GitHub Actions run artifacts.",
            is_error=True,
        )
        has_critical_failure = True

    finally:
        if browser:
            browser.quit()

        elapsed = round(time.time() - start_time)
        logger.info("=== Process Finished. Total Runtime: %ds ===", elapsed)

        if all_results:
            notify_streak_summary(all_results, elapsed)

        total_sent = sum(r.get("sent", 0) for r in all_results)
        if (has_critical_failure or total_sent == 0) and accounts:
            sys.exit(1)


if __name__ == "__main__":
    run()
