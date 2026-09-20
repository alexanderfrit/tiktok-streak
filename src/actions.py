from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import logging, random, time
from .browser import dump_debug_diagnostics
from .exceptions import (
    DMBlockedError,
    RateLimitError,
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
    (By.CSS_SELECTOR, 'input[data-e2e="search-user-input"]'),
    (By.CSS_SELECTOR, 'input[placeholder*="Search" i]'),
    (By.CSS_SELECTOR, 'input[placeholder*="Tìm" i]'),
    (By.CSS_SELECTOR, 'div[class*="SearchBar"] input'),
    (By.CSS_SELECTOR, 'input[type="search"]'),
]

MESSAGE_INPUT_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="dm-new-chatbox"] [contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"]'),
    (By.CSS_SELECTOR, 'div[class*="public-DraftStyleDefault-block"]'),
    (By.CSS_SELECTOR, 'textarea'),
]


def find_elements_by_candidates(browser, candidates: list, description: str, timeout: float = 8.0) -> list:
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

    dump_debug_diagnostics(browser, "selector_timeout")
    raise TimeoutError(f"Could not locate {description} using candidate selectors.")


def find_element_by_candidates(browser, candidates: list, description: str, timeout: float = 8.0):
    return find_elements_by_candidates(browser, candidates, description, timeout=timeout)[0]


def type_human_like(element, text: str) -> None:
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.12))


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


def open_inbox_chat(browser, friend: str) -> None:
    # Inbox-only: the bot must never touch a public profile route. Find the
    # thread in the existing conversation list (bidirectional nickname match,
    # same as the media path), or use the inbox search box, or fail clearly.
    import json as _json
    from .share import OPEN_THREAD_JS

    if "/messages" not in browser.current_url:
        browser.get("https://www.tiktok.com/messages?lang=en")
        time.sleep(random.uniform(3.0, 4.5))
    dismiss_modals(browser)

    # 1. Existing conversation thread in the left pane. The thread shows the
    # display name ("cel"), which the handle ("celuley") may not contain either
    # way in a naive substring test - OPEN_THREAD_JS matches both directions.
    try:
        hit = browser.execute_script(OPEN_THREAD_JS.replace("__NAME__", _json.dumps(friend)))
        if hit:
            logger.info("Opened @%s from the inbox list (matched %r).", friend, hit.get("txt"))
            time.sleep(random.uniform(1.5, 2.5))
            return
    except Exception as e:
        logger.debug("Inbox list match for @%s failed: %s", friend, e)

    # 2. Inbox search box (no public profile navigation).
    try:
        search = find_element_by_candidates(browser, INBOX_SEARCH_CANDIDATES, "inbox search box", timeout=4.0)
        safe_click(browser, search)
        search.clear()
        type_human_like(search, friend)
        time.sleep(random.uniform(1.5, 2.5))
        hit = browser.execute_script(OPEN_THREAD_JS.replace("__NAME__", _json.dumps(friend)))
        if hit:
            logger.info("Found @%s via inbox search (matched %r).", friend, hit.get("txt"))
            time.sleep(random.uniform(1.5, 2.5))
            return
    except Exception as e:
        logger.debug("Inbox search for @%s failed: %s", friend, e)

    raise DMBlockedError(
        f"Could not open @{friend} from the inbox (no existing thread, search found no match). "
        f"DMs may be restricted or a mutual follow is required."
    )


def send_streak_message(browser, friend: str, message_text: str) -> dict:
    status = {"friend": friend, "sent": False, "error": None}
    try:
        open_inbox_chat(browser, friend)

        logger.info("Locating chat input for @%s...", friend)
        msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=10.0)
        safe_click(browser, msg_input)
        time.sleep(0.4)

        logger.info("Typing message to @%s...", friend)
        type_human_like(msg_input, message_text)
        time.sleep(random.uniform(0.4, 0.8))
        msg_input.send_keys(Keys.RETURN)

        time.sleep(random.uniform(1.5, 2.5))
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
    except Exception as e:
        status["error"] = str(e)
        logger.error("Failed sending streak message to @%s: %s", friend, e)

    return status
