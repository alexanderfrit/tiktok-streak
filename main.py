import argparse
from dataclasses import replace
import logging, os, random, sys, time
from src.browser import init_browser, authenticate_session
from src.config import load_accounts, clean_handle
from src.actions import send_streak_message
from src.exceptions import RateLimitError
from src.share import install_ws_hook, install_media_guard, parse_aweme_id, send_share_card, send_photo_card
from src.notifier import notify_streak_summary, notify_telegram

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")


def _emit_progress(progress_cb, **payload) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(payload)
    except Exception as e:
        logger.debug("progress_cb raised: %s", e)


def _is_cancelled(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sleep_or_stop(seconds: float, stop_event=None) -> None:
    if stop_event is None:
        time.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        chunk = min(0.25, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _filter_accounts(accounts: list, only_account: str | None, only_friend: str | None) -> tuple[list, str | None]:
    """Filter accounts by optional account name and/or friend handle."""
    if only_account:
        target_acc = only_account.strip()
        exact = [a for a in accounts if a.name == target_acc]
        accounts = exact if exact else [a for a in accounts if a.name.strip().lower() == target_acc.lower()]
        if not accounts:
            return [], f"No configured account matching {only_account!r}."

    if only_friend:
        target_friend = clean_handle(only_friend).lower()
        filtered = []
        for a in accounts:
            matched = [f for f in a.friends if clean_handle(f).lower() == target_friend]
            if matched:
                filtered.append(replace(a, friends=matched))
        accounts = filtered
        if not accounts:
            return [], f"No configured friend matching {only_friend!r}."

    return accounts, None


def run(
    accounts_override=None,
    headless: bool | None = None,
    only_account: str | None = None,
    only_friend: str | None = None,
    stop_event=None,
    progress_cb=None,
) -> dict:
    """Execute the TikTok streak automation and return a structured result dict."""
    start_time = time.time()
    logger.info("=== Starting TikTok Streak Automation ===")

    accounts = load_accounts() if accounts_override is None else list(accounts_override)
    if not accounts:
        logger.error("No TikTok accounts configured. Please check .env or accounts.json.")
        notify_telegram(
            title="TikTok Streak Configuration Missing",
            details="No accounts or sessionid found.",
            action_tip="Create .env or accounts.json with valid session_id.",
            is_error=True,
        )
        _emit_progress(progress_cb, stage="error", message="No TikTok accounts configured.")
        return {
            "ok": False,
            "results": [],
            "elapsed": int(round(time.time() - start_time)),
            "critical_failure": True,
        }

    accounts, filter_err = _filter_accounts(accounts, only_account, only_friend)
    if filter_err:
        logger.error("%s", filter_err)
        _emit_progress(progress_cb, stage="error", message=filter_err)
        return {
            "ok": False,
            "results": [],
            "elapsed": int(round(time.time() - start_time)),
            "critical_failure": True,
        }

    if _is_cancelled(stop_event):
        logger.info("Run cancelled before browser startup.")
        _emit_progress(progress_cb, stage="cancelled", message="Cancelled before start.")
        return {
            "ok": False,
            "results": [],
            "elapsed": int(round(time.time() - start_time)),
            "critical_failure": False,
        }

    logger.info("Found %d account(s) to process.", len(accounts))
    _emit_progress(progress_cb, stage="init", accounts_total=len(accounts), message=f"Found {len(accounts)} account(s).")

    browser = None
    all_results = []
    has_critical_failure = False
    rate_limited = False

    try:
        browser, _wait = init_browser(headless=headless)

        # Hook WebSockets before any navigation so the IM client's sockets are captured.
        install_ws_hook(browser)
        # Guard the DM file input before navigation so photo sends work too.
        install_media_guard(browser)

        try:
            cap = max(0, int((os.getenv("DAILY_CAP") or "0").strip() or "0"))
        except ValueError:
            logger.warning("Invalid DAILY_CAP=%r; ignoring cap.", os.getenv("DAILY_CAP"))
            cap = 0

        for a_idx, account in enumerate(accounts, start=1):
            if _is_cancelled(stop_event) or rate_limited:
                break

            logger.info("--- [%d/%d] Processing Account: %s ---", a_idx, len(accounts), account.name)
            _emit_progress(
                progress_cb,
                stage="account_start",
                account=account.name,
                account_index=a_idx,
                accounts_total=len(accounts),
                message=f"Authenticating {account.name}...",
            )
            acc_result = {
                "name": account.name,
                "friends_total": len(account.friends),
                "sent": 0,
                "shares_sent": 0,
                "photos_sent": 0,
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
                _emit_progress(
                    progress_cb,
                    stage="account_done",
                    account=account.name,
                    account_index=a_idx,
                    accounts_total=len(accounts),
                    ok=False,
                    message=err,
                )
                continue

            if not account.friends:
                logger.warning("[%s] No target friends configured. Skipping.", account.name)
                acc_result["failures"].append("No friends specified in friends list")
                all_results.append(acc_result)
                _emit_progress(
                    progress_cb,
                    stage="account_done",
                    account=account.name,
                    account_index=a_idx,
                    accounts_total=len(accounts),
                    ok=False,
                    message="No target friends configured.",
                )
                continue

            # Shuffle order + cap so runs don't hit friends in a fixed pattern.
            friends = list(account.friends)
            random.shuffle(friends)
            item_id = parse_aweme_id(account.video_url) if account.video_url else None
            if account.video_url and not item_id:
                logger.warning("[%s] Could not parse video id from video_url=%r.", account.name, account.video_url)

            for f_idx, friend in enumerate(friends, start=1):
                if _is_cancelled(stop_event):
                    break

                logger.info("[%s] [%d/%d] Sending streak message to @%s...", account.name, f_idx, len(friends), friend)
                _emit_progress(
                    progress_cb,
                    stage="friend_start",
                    account=account.name,
                    account_index=a_idx,
                    accounts_total=len(accounts),
                    friend=friend,
                    friend_index=f_idx,
                    friends_total=len(friends),
                    message=f"[{account.name}] Messaging @{friend} ({f_idx}/{len(friends)})...",
                )

                try:
                    if stop_event is not None:
                        res = send_streak_message(
                            browser=browser,
                            friend=friend,
                            message_text=account.message,
                            stop_event=stop_event,
                        )
                    else:
                        res = send_streak_message(
                            browser=browser,
                            friend=friend,
                            message_text=account.message,
                        )
                except RateLimitError as e:
                    err = f"Rate limit at @{friend}: {e}"
                    logger.error("[%s] %s. Stopping run.", account.name, err)
                    acc_result["failures"].append(err)
                    has_critical_failure = True
                    rate_limited = True
                    notify_telegram(
                        title="TikTok Rate Limit Reached",
                        details=f"[{account.name}] {err}",
                        action_tip=getattr(e, "action_tip", RateLimitError.action_tip),
                        is_error=True,
                    )
                    _emit_progress(
                        progress_cb,
                        stage="rate_limit",
                        account=account.name,
                        friend=friend,
                        message=err,
                    )
                    break

                if res["sent"]:
                    acc_result["sent"] += 1
                    logger.info("[%s] Streak message delivered to @%s.", account.name, friend)
                elif _is_cancelled(stop_event) and res.get("error") == "Cancelled":
                    logger.info("[%s] Send to @%s cancelled.", account.name, friend)
                    break
                else:
                    err = f"@{friend}: {res['error']}"
                    acc_result["failures"].append(err)
                    logger.error("[%s] Text to @%s FAILED: %s", account.name, friend, res["error"])

                if _is_cancelled(stop_event):
                    break

                # Send N video-share cards (streak progression needs more than one).
                shares_here = 0
                if item_id and account.share_times > 0 and not _is_cancelled(stop_event):
                    for s_idx in range(account.share_times):
                        if _is_cancelled(stop_event):
                            break
                        sres = send_share_card(browser, friend, item_id)
                        if sres["sent"]:
                            shares_here += 1
                            acc_result["shares_sent"] += 1
                            logger.info("[%s] Share card %d/%d delivered to @%s.", account.name, s_idx + 1, account.share_times, friend)
                        else:
                            acc_result["failures"].append(f"@{friend} share {s_idx + 1}: {sres['error']}")
                            logger.error("[%s] Share %d/%d to @%s FAILED: %s",
                                         account.name, s_idx + 1, account.share_times, friend, sres["error"])
                        if s_idx + 1 < account.share_times:
                            _sleep_or_stop(random.uniform(1.5, 3.0), stop_event)

                # Send 1 photo (streak progression also needs a media message).
                photo_ok = None
                if account.photo_path and not _is_cancelled(stop_event):
                    pres = send_photo_card(browser, friend, account.photo_path)
                    if pres["sent"]:
                        photo_ok = True
                        acc_result["photos_sent"] += 1
                        logger.info("[%s] Photo delivered to @%s.", account.name, friend)
                    else:
                        photo_ok = False
                        acc_result["failures"].append(f"@{friend} photo: {pres['error']}")
                        logger.error("[%s] Photo to @%s FAILED: %s", account.name, friend, pres["error"])
                elif not account.photo_path:
                    logger.info("[%s] Photo disabled for @%s (no photo_path).", account.name, friend)

                logger.info(
                    "[%s] @%s summary -> text=%s share=%d/%d photo=%s",
                    account.name, friend,
                    "ok" if res["sent"] else "FAIL",
                    shares_here, account.share_times if item_id else 0,
                    "n/a" if photo_ok is None else ("ok" if photo_ok else "FAIL"),
                )
                _emit_progress(
                    progress_cb,
                    stage="friend_done",
                    account=account.name,
                    friend=friend,
                    friend_index=f_idx,
                    friends_total=len(friends),
                    ok=bool(res["sent"]),
                    sent=acc_result["sent"],
                    shares_sent=acc_result["shares_sent"],
                    photos_sent=acc_result["photos_sent"],
                    message=f"[{account.name}] @{friend} -> text={'ok' if res['sent'] else 'FAIL'}",
                )

                if cap and acc_result["sent"] >= cap:
                    logger.info("[%s] Daily cap of %d reached; stopping.", account.name, cap)
                    break

                if f_idx < len(friends) and not _is_cancelled(stop_event):
                    _sleep_or_stop(random.uniform(3.0, 8.0), stop_event)

            all_results.append(acc_result)
            _emit_progress(
                progress_cb,
                stage="account_done",
                account=account.name,
                account_index=a_idx,
                accounts_total=len(accounts),
                sent=acc_result["sent"],
                ok=not acc_result["failures"],
                message=f"[{account.name}] finished: {acc_result['sent']}/{acc_result['friends_total']} sent.",
            )
            if rate_limited or _is_cancelled(stop_event):
                break

    except RateLimitError as e:
        logger.error("Rate limit encountered: %s", e)
        notify_telegram(
            title="TikTok Rate Limit Reached",
            details=f"Rate limit triggered: {e}",
            action_tip=getattr(e, "action_tip", RateLimitError.action_tip),
            is_error=True,
        )
        has_critical_failure = True

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
            try:
                browser.quit()
            except Exception as qe:
                logger.debug("browser.quit() failed: %s", qe)

        elapsed = int(round(time.time() - start_time))
        logger.info("=== Process Finished. Total Runtime: %ds ===", elapsed)

        if all_results:
            notify_streak_summary(all_results, elapsed)

    cancelled = _is_cancelled(stop_event)
    total_sent = sum(r.get("sent", 0) for r in all_results)
    ok = (not has_critical_failure) and (not cancelled) and (total_sent > 0)
    _emit_progress(
        progress_cb,
        stage="done",
        ok=ok,
        elapsed=elapsed,
        critical_failure=has_critical_failure,
        results=all_results,
        message=f"Finished in {elapsed}s (sent={total_sent}).",
    )
    return {
        "ok": ok,
        "results": all_results,
        "elapsed": elapsed,
        "critical_failure": has_critical_failure,
    }


def cli_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="TikTok Daily Streak Automation")
    parser.add_argument(
        "--headful", "--visible",
        action="store_true",
        dest="headful",
        help="Show the Chrome window instead of running headless.",
    )
    parser.add_argument(
        "--only-account",
        default=None,
        help="Process only the configured account with this name.",
    )
    parser.add_argument(
        "--only-friend",
        default=None,
        help="Send only to this friend handle.",
    )
    args = parser.parse_args(argv)

    headless = False if args.headful else None
    result = run(
        headless=headless,
        only_account=args.only_account,
        only_friend=args.only_friend,
    )
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
