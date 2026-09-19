import logging, os, random, sys, time
from src.browser import init_browser, authenticate_session
from src.config import load_accounts
from src.actions import send_streak_message
from src.share import install_ws_hook, parse_aweme_id, find_template, send_share_card
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

        # Hook WebSockets before any navigation so the IM client's sockets are captured.
        install_ws_hook(browser)

        for a_idx, account in enumerate(accounts, start=1):
            logger.info("--- [%d/%d] Processing Account: %s ---", a_idx, len(accounts), account.name)
            acc_result = {
                "name": account.name,
                "friends_total": len(account.friends),
                "sent": 0,
                "shares_sent": 0,
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

            # Shuffle order + cap so runs don't hit friends in a fixed pattern.
            friends = list(account.friends)
            random.shuffle(friends)
            cap = int(os.getenv("DAILY_CAP") or 0)
            item_id = parse_aweme_id(account.video_url) if account.video_url else None
            if account.video_url and not item_id:
                logger.warning("[%s] Could not parse video id from video_url=%r.", account.name, account.video_url)

            for f_idx, friend in enumerate(friends, start=1):
                logger.info("[%s] [%d/%d] Sending streak message to @%s...", account.name, f_idx, len(friends), friend)
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

                # Send N video-share cards (streak progression needs more than one).
                if item_id and account.share_times > 0:
                    template = find_template(browser)
                    if not template:
                        acc_result["failures"].append(f"@{friend}: no share template frame")
                    else:
                        sent_here = 0
                        for s_idx in range(account.share_times):
                            sres = send_share_card(browser, friend, item_id, template=template)
                            if sres["sent"]:
                                sent_here += 1
                                acc_result["shares_sent"] += 1
                                logger.info("[%s] Share card %d/%d delivered to @%s.", account.name, s_idx + 1, account.share_times, friend)
                            else:
                                acc_result["failures"].append(f"@{friend} share {s_idx + 1}: {sres['error']}")
                            if s_idx + 1 < account.share_times:
                                time.sleep(random.uniform(1.5, 3.0))
                        if sent_here == 0:
                            logger.warning("[%s] No share cards delivered to @%s.", account.name, friend)

                if cap and acc_result["sent"] >= cap:
                    logger.info("[%s] Daily cap of %d reached; stopping.", account.name, cap)
                    break

                if f_idx < len(friends):
                    time.sleep(random.uniform(3.0, 8.0))

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
