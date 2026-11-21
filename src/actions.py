from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import logging, random, time
from .browser import dump_debug_diagnostics, MESSAGES_URL, set_headful_guard_status
from .config import clean_handle
from .exceptions import (
    DMBlockedError,
    RateLimitError,
    RecipientMismatchError,
)
from .share import (
    CLICK_INBOX_ROW_JS,
    INSPECT_INBOX_ROWS_JS,
    assert_active_chat_handle,
    get_active_chat_handle,
    reset_friend_send_capture,
)

logger = logging.getLogger("tiktok-streak")

CHAT_ITEM_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="dm-new-conversation-item"]'),
    (By.CSS_SELECTOR, '[data-e2e="chat-item"]'),
    (By.CSS_SELECTOR, 'div[class*="PInfoNickname"]'),
    (By.XPATH, "//*[contains(@class, 'Nickname') or contains(@class, 'InfoNickname')]"),
    (By.CSS_SELECTOR, 'div[role="listitem"]'),
    (By.CSS_SELECTOR, 'div[class*="ConversationItem"]'),
]

INBOX_SEARCH_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="dm-new-conversation-list"] input'),
    (By.CSS_SELECTOR, 'input[placeholder*="Search" i]'),
    (By.CSS_SELECTOR, 'input[placeholder*="Tìm" i]'),
    (By.CSS_SELECTOR, 'div[class*="SearchBar"] input'),
]

MESSAGE_INPUT_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="dm-new-chatbox"] [contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"]'),
    (By.CSS_SELECTOR, 'div[class*="public-DraftStyleDefault-block"]'),
    (By.CSS_SELECTOR, 'textarea'),
]


def find_elements_by_candidates(
    browser,
    candidates: list,
    description: str,
    timeout: float = 8.0,
    dump_on_timeout: bool = True,
) -> list:
    end_time = time.time() + timeout
    while time.time() < end_time:
        for by_type, selector in candidates:
            try:
                elements = browser.find_elements(by_type, selector)
                visible = [el for el in elements if el.is_displayed()]
                if visible:
                    return visible
            except Exception:
                continue
        time.sleep(0.4)

    if dump_on_timeout:
        dump_debug_diagnostics(browser, "selector_timeout")
    raise TimeoutError(f"Could not locate {description} using candidate selectors.")


def find_element_by_candidates(
    browser,
    candidates: list,
    description: str,
    timeout: float = 8.0,
    dump_on_timeout: bool = True,
):
    return find_elements_by_candidates(
        browser,
        candidates,
        description,
        timeout=timeout,
        dump_on_timeout=dump_on_timeout,
    )[0]


def _is_cancelled(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sleep_interruptible(seconds: float, stop_event=None) -> None:
    if stop_event is None:
        time.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        chunk = min(0.25, remaining)
        time.sleep(chunk)
        remaining -= chunk


def type_human_like(element, text: str, stop_event=None) -> None:
    for ch in text:
        if _is_cancelled(stop_event):
            return
        element.send_keys(ch)
        _sleep_interruptible(random.uniform(0.04, 0.12), stop_event)


def dismiss_modals(browser) -> None:
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(browser).send_keys(Keys.ESCAPE).perform()
    except Exception:
        pass
    close_selectors = [
        '[data-e2e="modal-close-inner-button"]',
        'button[aria-label="Close"]',
        'button[aria-label="Đóng"]',
        '.TUXModal-close',
        'div[class*="ModalClose"]',
    ]
    for sel in close_selectors:
        try:
            for btn in browser.find_elements(By.CSS_SELECTOR, sel):
                if btn.is_displayed():
                    btn.click()
                    time.sleep(0.4)
        except Exception:
            pass


def safe_click(browser, element) -> None:
    dismiss_modals(browser)
    try:
        element.click()
    except Exception:
        browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.3)
        browser.execute_script("arguments[0].click();", element)


def check_rate_limits(browser) -> None:
    toast_selectors = [
        (By.CSS_SELECTOR, '[data-e2e="toast"]'),
        (By.CSS_SELECTOR, 'div[role="alert"]'),
    ]
    for by_type, selector in toast_selectors:
        try:
            elems = browser.find_elements(by_type, selector)
            for el in elems:
                if el.is_displayed():
                    txt = el.text.lower()
                    if "too fast" in txt or "action blocked" in txt or "quá nhanh" in txt:
                        dump_debug_diagnostics(browser, "rate_limit")
                        raise RateLimitError(f"TikTok anti-spam rate limit triggered: {el.text}")
        except RateLimitError:
            raise
        except Exception:
            continue


def _thread_list_present(browser) -> bool:
    """True once the inbox conversation list has rendered at least one thread."""
    try:
        return bool(browser.execute_script(
            "return !!document.querySelector('p[class*=\"PInfoNickname\"], "
            "[data-e2e=\"dm-new-conversation-item\"]');"
        ))
    except Exception:
        return False


def _row_scan_priority(nickname: str, target: str) -> int:
    """Rank uninspected READ rows by safe display-name plausibility.

    Single/two-character display names (e.g. 'M') are NEVER boosted ahead of
    real multi-character token matches to prevent short-nickname collisions.
    """
    import re as _re
    nick = (nickname or "").strip().lower()
    tgt = clean_handle(target).lower()
    if not nick or not tgt:
        return 99
    if nick == tgt or nick == f"@{tgt}":
        return 0
    if len(nick) >= 3 and (tgt.startswith(nick) or nick.startswith(tgt[:3])):
        return 1
    tokens = [tok for tok in _re.split(r"[^a-z0-9_.]+", nick) if len(tok) >= 3]
    for tok in tokens:
        if tgt.startswith(tok) or tok in tgt or (len(tgt) >= 4 and tgt[:4] in tok):
            return 2
    return 3


def _wait_for_active_handle(
    browser,
    expected_target: str,
    timeout: float = 3.5,
    stop_event=None,
) -> tuple[bool, str | None]:
    """Poll right-pane chat header until `expected_target` appears or timeout."""
    deadline = time.time() + timeout
    last_seen: str | None = None
    while time.time() < deadline:
        if _is_cancelled(stop_event):
            return False, last_seen
        last_seen = get_active_chat_handle(browser)
        if last_seen == expected_target:
            return True, last_seen
        _sleep_interruptible(0.25, stop_event)
    last_seen = get_active_chat_handle(browser)
    return last_seen == expected_target, last_seen


def _inspect_rows(browser) -> list[dict]:
    try:
        raw = browser.execute_script(INSPECT_INBOX_ROWS_JS)
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    except Exception:
        return []


def _click_row(browser, idx: int) -> bool:
    try:
        return bool(browser.execute_script(CLICK_INBOX_ROW_JS, int(idx)))
    except Exception:
        return False


def _cache_verified_thread(browser, handle: str | None, nickname: str) -> None:
    if not handle:
        return
    try:
        browser.execute_script(
            "window.__verifiedThreads = window.__verifiedThreads || {};"
            "window.__verifiedThreads[arguments[0]] = arguments[1];",
            clean_handle(handle).lower(),
            str(nickname or ""),
        )
    except Exception:
        pass


def open_inbox_chat(browser, friend: str, stop_event=None) -> str:
    """Open @friend's DM thread strictly within /messages and verify right-pane @username.

    Resolution tiers:
      0. Already open in right-pane header == @friend.
      1. Exact handle / fiber / network-harvested / cached match in visible inbox rows.
      2. Inbox search box (if visible) + strict right-pane header verification.
      3. Bounded read-only scan of READ inbox rows (explicitly skipping unread rows).
    Raises DMBlockedError if the verified handle cannot be opened.
    """
    target = clean_handle(friend).lower()
    if not target:
        raise DMBlockedError("Empty friend handle.")

    if _is_cancelled(stop_event):
        return ""

    set_headful_guard_status(browser, f"TikTok Streak Bot: opening @{target}...")

    if "/messages" not in str(getattr(browser, "current_url", "") or ""):
        browser.get(MESSAGES_URL)
        _sleep_interruptible(random.uniform(3.0, 4.5), stop_event)
        if _is_cancelled(stop_event):
            return ""
    dismiss_modals(browser)
    if _is_cancelled(stop_event):
        return ""

    # Tier 0: Already open on exact target handle
    if get_active_chat_handle(browser) == target:
        logger.info("@%s is already open and verified in chat header.", target)
        return target

    # Wait for inbox list to stream in
    deadline = time.time() + 15.0
    rows: list[dict] = []
    while time.time() < deadline:
        if _is_cancelled(stop_event):
            return ""
        rows = _inspect_rows(browser)
        if rows or _thread_list_present(browser):
            break
        _sleep_interruptible(0.5, stop_event)

    if _is_cancelled(stop_event):
        return ""

    # Read known nickname mappings from network harvester / previous row scans
    known_nick = ""
    try:
        nick_lookup = browser.execute_script(
            "var vt = window.__verifiedThreads || {};"
            "var hm = window.__dmUsersByHandle || {};"
            "return vt[arguments[0]] || hm[arguments[0]] || '';",
            target,
        )
        if isinstance(nick_lookup, str):
            known_nick = nick_lookup.strip().lower()
    except Exception:
        known_nick = ""

    # Tier 1: Exact handle metadata / React Fiber / unique harvested nickname (works even if unread)
    exact_candidates: list[dict] = []
    for r in rows:
        if str(r.get("handle") or "").lower() == target:
            exact_candidates.append(r)
    if not exact_candidates and known_nick:
        nick_matches = [r for r in rows if (r.get("nickname") or "").strip().lower() == known_nick]
        if len(nick_matches) == 1:
            exact_candidates = nick_matches

    for cand in exact_candidates:
        if _click_row(browser, cand["idx"]):
            ok, actual = _wait_for_active_handle(browser, target, timeout=4.0, stop_event=stop_event)
            _cache_verified_thread(browser, actual, cand.get("nickname", ""))
            if ok:
                logger.info("Opened @%s via Tier-1 exact metadata (%r).", target, cand.get("nickname"))
                _sleep_interruptible(random.uniform(0.8, 1.5), stop_event)
                return target

    if _is_cancelled(stop_event):
        return ""

    # Tier 2: Inbox search box (only if rendered inside the DM inbox pane)
    search_box = None
    try:
        search_box = find_element_by_candidates(
            browser,
            INBOX_SEARCH_CANDIDATES,
            "inbox search box",
            timeout=2.0,
            dump_on_timeout=False,
        )
    except Exception:
        search_box = None

    if search_box is not None and not _is_cancelled(stop_event):
        try:
            safe_click(browser, search_box)
            search_box.clear()
            type_human_like(search_box, target, stop_event=stop_event)
            if _is_cancelled(stop_event):
                return ""
            _sleep_interruptible(random.uniform(1.2, 2.0), stop_event)
            s_deadline = time.time() + 5.0
            matched_in_search = False
            while time.time() < s_deadline and not _is_cancelled(stop_event):
                s_rows = _inspect_rows(browser)
                exact_s = [r for r in s_rows if str(r.get("handle") or "").lower() == target]
                pick = exact_s[0] if exact_s else (s_rows[0] if len(s_rows) == 1 else None)
                if pick and _click_row(browser, pick["idx"]):
                    ok, actual = _wait_for_active_handle(browser, target, timeout=3.5, stop_event=stop_event)
                    _cache_verified_thread(browser, actual, pick.get("nickname", ""))
                    if ok:
                        logger.info("Opened @%s via Tier-2 inbox search.", target)
                        matched_in_search = True
                        break
                _sleep_interruptible(0.5, stop_event)
            if matched_in_search:
                _sleep_interruptible(random.uniform(0.8, 1.4), stop_event)
                return target
        except Exception as e:
            logger.debug("Tier-2 inbox search for @%s failed: %s", target, e)
        finally:
            try:
                search_box.send_keys(Keys.CONTROL, "a")
                search_box.send_keys(Keys.BACK_SPACE)
                search_box.send_keys(Keys.ESCAPE)
                _sleep_interruptible(0.5, stop_event)
            except Exception:
                pass

    if _is_cancelled(stop_event):
        return ""

    # Tier 3: Bounded read-only scan of READ threads (never opens unread chats)
    seen_row_keys: set[str] = set()
    scanned = 0
    max_scan = 35
    total_unread_skipped = 0

    for _scroll_pass in range(3):
        if _is_cancelled(stop_event) or scanned >= max_scan:
            break
        rows = _inspect_rows(browser)
        unread_rows = [r for r in rows if r.get("unread")]
        if _scroll_pass == 0:
            total_unread_skipped = len(unread_rows)

        candidates = []
        for r in rows:
            if r.get("unread"):
                continue
            rk = str(r.get("rowKey") or r.get("idx"))
            if rk in seen_row_keys:
                continue
            rh = str(r.get("handle") or "").lower()
            if rh and rh != target:
                seen_row_keys.add(rk)
                continue
            candidates.append(r)

        candidates.sort(key=lambda item: (_row_scan_priority(str(item.get("nickname") or ""), target), int(item.get("idx", 0))))

        for cand in candidates:
            if _is_cancelled(stop_event) or scanned >= max_scan:
                break
            rk = str(cand.get("rowKey") or cand.get("idx"))
            seen_row_keys.add(rk)
            scanned += 1
            prev_handle = get_active_chat_handle(browser)
            if not _click_row(browser, cand["idx"]):
                continue
            # Wait briefly for right-pane header to reflect clicked conversation
            actual = None
            h_deadline = time.time() + 2.0
            while time.time() < h_deadline:
                if _is_cancelled(stop_event):
                    return ""
                actual = get_active_chat_handle(browser)
                if actual == target:
                    _cache_verified_thread(browser, actual, cand.get("nickname", ""))
                    logger.info(
                        "Opened @%s via Tier-3 verified read-row scan (%r).",
                        target,
                        cand.get("nickname"),
                    )
                    _sleep_interruptible(random.uniform(0.8, 1.4), stop_event)
                    return target
                if actual and actual != prev_handle:
                    break
                _sleep_interruptible(0.25, stop_event)
            _cache_verified_thread(browser, actual, cand.get("nickname", ""))

        # Scroll conversation container to load older read threads if available
        try:
            scrolled = browser.execute_script(
                "var item = document.querySelector('[data-e2e=\"dm-new-conversation-item\"], [data-e2e=\"chat-item\"], div[class*=\"ConversationItem\"]');"
                "if (!item || !item.parentElement) return false;"
                "var p = item.parentElement;"
                "while (p && p !== document.body && p.scrollHeight <= p.clientHeight + 10) p = p.parentElement;"
                "if (!p || p === document.body) return false;"
                "var prev = p.scrollTop; p.scrollTop = Math.min(p.scrollHeight, prev + Math.max(300, p.clientHeight));"
                "return p.scrollTop > prev;"
            )
            if not scrolled:
                break
            _sleep_interruptible(0.6, stop_event)
        except Exception:
            break

    dump_debug_diagnostics(browser, f"open_chat_failed_{target}")
    raise DMBlockedError(
        f"Could not open verified chat for @{target} in /messages "
        f"(scanned {scanned} read thread(s), skipped {total_unread_skipped} unread thread(s); "
        "target may have a different Display Name and be unread, or DMs require mutual follow)."
    )


def send_streak_message(browser, friend: str, message_text: str, stop_event=None) -> dict:
    status = {"friend": friend, "sent": False, "error": None}
    target = clean_handle(friend).lower()
    try:
        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        if stop_event is not None:
            open_inbox_chat(browser, friend, stop_event=stop_event)
        else:
            open_inbox_chat(browser, friend)

        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        set_headful_guard_status(browser, f"TikTok Streak Bot: sending to @{target}...")
        reset_friend_send_capture(browser, friend)
        assert_active_chat_handle(browser, friend)

        logger.info("Locating chat input for @%s...", friend)
        msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=10.0)
        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        assert_active_chat_handle(browser, friend)
        safe_click(browser, msg_input)
        _sleep_interruptible(0.4, stop_event)
        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        assert_active_chat_handle(browser, friend)
        logger.info("Typing message to @%s...", friend)
        if stop_event is not None:
            type_human_like(msg_input, message_text, stop_event=stop_event)
        else:
            type_human_like(msg_input, message_text)
        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        _sleep_interruptible(random.uniform(0.4, 0.8), stop_event)
        if _is_cancelled(stop_event):
            status["error"] = "Cancelled"
            return status

        # Final fail-closed recipient check immediately before hitting Enter.
        assert_active_chat_handle(browser, friend)
        msg_input.send_keys(Keys.RETURN)

        _sleep_interruptible(random.uniform(1.5, 2.5), stop_event)
        check_rate_limits(browser)

        # Confirm the send: if our text is still sitting in the composer, RETURN didn't submit.
        try:
            leftover = (msg_input.text or "").strip()
            if leftover and leftover == message_text.strip():
                status["error"] = "Message still present in composer after RETURN; send not confirmed."
                logger.error("Streak message to @%s not confirmed sent.", friend)
                return status
        except Exception:
            pass  # composer element re-rendered (stale) => send was accepted

        logger.info("Streak message successfully sent to @%s.", friend)
        status["sent"] = True
    except RecipientMismatchError as e:
        dump_debug_diagnostics(browser, f"recipient_mismatch_{target or 'unknown'}")
        status["error"] = str(e)
        logger.error("Aborted streak message to @%s before sending: %s", friend, e)
    except RateLimitError as e:
        logger.error("Rate limit encountered while messaging @%s: %s", friend, e)
        raise
    except Exception as e:
        status["error"] = str(e)
        logger.error("Failed sending streak message to @%s: %s", friend, e)

    return status
