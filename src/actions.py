from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import logging, os, random, re, time
from .browser import dump_debug_diagnostics
from .exceptions import (
    UserNotFoundError,
    DMBlockedError,
    RateLimitError,
    MediaUploadError,
    VideoShareError,
)

logger = logging.getLogger("tiktok-streak")

# Candidate selectors for UI resilience
PROFILE_MESSAGE_BUTTON_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="message-button"]'),
    (By.XPATH, "//div[@data-e2e='user-page']//button[contains(., 'Message') or contains(., 'Nhắn tin')]"),
    (By.XPATH, "//main//button[contains(., 'Message') or contains(., 'Nhắn tin')]"),
    (By.XPATH, "//header//button[contains(., 'Message') or contains(., 'Nhắn tin')]"),
    (By.CSS_SELECTOR, 'div[data-e2e="user-page"] button[class*="ButtonMessage"]'),
]

MESSAGE_INPUT_CANDIDATES = [
    (By.CSS_SELECTOR, 'div[contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"]'),
    (By.CSS_SELECTOR, 'div[class*="public-DraftStyleDefault-block"]'),
    (By.CSS_SELECTOR, 'textarea'),
]

FILE_INPUT_CANDIDATES = [
    (By.CSS_SELECTOR, 'input[type="file"][accept*="image"]'),
    (By.CSS_SELECTOR, 'input[type="file"]'),
]

VIDEO_SHARE_ICON_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="share-icon"]'),
    (By.CSS_SELECTOR, '[data-e2e="feed-share-btn"]'),
    (By.CSS_SELECTOR, '[data-e2e="video-share-container"] button'),
    (By.CSS_SELECTOR, 'button[aria-label*="Share" i]'),
    (By.CSS_SELECTOR, 'button[aria-label*="Chia sẻ" i]'),
    (By.CSS_SELECTOR, 'button[aria-label*="Bagikan" i]'),
    (By.XPATH, "//button[contains(@class, 'Share') or contains(@aria-label, 'Share')]"),
]

MEDIA_BUTTON_CANDIDATES = [
    (By.XPATH, "//*[@aria-label='Click to send media' or contains(@aria-label, 'media') or contains(@aria-label, 'Media') or contains(@aria-label, 'ảnh')]"),
    (By.CSS_SELECTOR, '[data-e2e="message-media-upload"]'),
    (By.XPATH, "//div[contains(@class, 'Media') or contains(@class, 'Photo')]//button"),
]

SHARE_SEND_FRIEND_CANDIDATES = [
    (By.XPATH, "//*[contains(text(), 'Send to friends') or contains(text(), 'Gửi cho bạn bè')]"),
    (By.CSS_SELECTOR, '[data-e2e="share-to-friend"]'),
    (By.CSS_SELECTOR, 'div[class*="ShareFriend"]'),
]


def find_elements_by_candidates(browser, candidates: list, description: str, timeout: float = 12.0) -> list:
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


def find_element_by_candidates(browser, candidates: list, description: str, timeout: float = 12.0):
    return find_elements_by_candidates(browser, candidates, description, timeout=timeout)[0]


def type_human_like(element, text: str) -> None:
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.12))


def verify_account_available(browser, username: str) -> None:
    # Only match visible 404 / account missing error elements (avoids matching hidden hydration scripts)
    not_found_selectors = [
        (By.CSS_SELECTOR, '[data-e2e="user-not-found"]'),
        (By.XPATH, "//h2[contains(text(), \"Couldn't find this account\") or contains(text(), \"Không tìm thấy tài khoản này\")]"),
        (By.XPATH, "//p[contains(text(), 'Looking for videos? Try browsing')]"),
    ]
    for by_type, selector in not_found_selectors:
        try:
            elems = browser.find_elements(by_type, selector)
            if any(el.is_displayed() for el in elems):
                dump_debug_diagnostics(browser, f"not_found_{username}")
                raise UserNotFoundError(f"Account @{username} not found or deleted.")
        except UserNotFoundError:
            raise
        except Exception:
            continue


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
                    time.sleep(0.5)
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


def open_conversation(browser, friend: str) -> None:
    # Check if already in the right conversation
    if "/messages" in browser.current_url:
        current_header = browser.find_elements(By.XPATH, f"//*[contains(text(), '{friend}')]")
        if any(el.is_displayed() for el in current_header):
            return

    logger.info("Accessing messages inbox for @%s...", friend)
    browser.get("https://www.tiktok.com/messages?lang=vi")
    time.sleep(random.uniform(2.5, 3.5))
    dismiss_modals(browser)

    # 1. Look for conversation item matching friend in the inbox list
    try:
        threads = find_elements_by_candidates(browser, CHAT_ITEM_CANDIDATES, "inbox conversation items", timeout=8.0)
        for thread in threads:
            if friend.lower() in thread.text.lower():
                logger.info("Found active thread for @%s in inbox.", friend)
                safe_click(browser, thread)
                time.sleep(random.uniform(1.5, 2.5))
                return
    except Exception:
        pass

    # 2. Fallback: navigate to profile page
    logger.info("Opening @%s profile directly...", friend)
    browser.get(f"https://www.tiktok.com/@{friend}")
    time.sleep(random.uniform(2.5, 3.5))
    dismiss_modals(browser)
    verify_account_available(browser, friend)

    try:
        msg_button = find_element_by_candidates(
            browser, PROFILE_MESSAGE_BUTTON_CANDIDATES, f"Message button on @{friend} profile", timeout=8.0
        )
        safe_click(browser, msg_button)
        time.sleep(random.uniform(2.0, 3.0))
    except TimeoutError:
        raise DMBlockedError(f"Direct Message button not available for @{friend} (mutual follow required or DMs restricted).")


def send_text_message(browser, friend: str, message_text: str) -> None:
    open_conversation(browser, friend)

    msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=10.0)
    safe_click(browser, msg_input)
    time.sleep(0.4)

    type_human_like(msg_input, message_text)
    time.sleep(random.uniform(0.3, 0.6))
    msg_input.send_keys(Keys.RETURN)

    time.sleep(1.5)
    check_rate_limits(browser)
    logger.info("Text message sent to @%s.", friend)


def send_media_photo(browser, friend: str, image_path: str) -> bool:
    if not image_path:
        return False
    abs_path = os.path.abspath(image_path)
    if not os.path.exists(abs_path):
        logger.warning("Image file does not exist: %s. Skipping photo send.", abs_path)
        return False

    logger.info("Uploading photo (%s) to @%s...", os.path.basename(abs_path), friend)
    try:
        file_inputs = browser.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        if not file_inputs:
            # Try clicking media upload button to mount or trigger file input
            for by_type, selector in MEDIA_BUTTON_CANDIDATES:
                try:
                    btns = browser.find_elements(by_type, selector)
                    if btns and any(b.is_displayed() for b in btns):
                        safe_click(browser, btns[0])
                        time.sleep(1.0)
                        file_inputs = browser.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                        if file_inputs:
                            break
                except Exception:
                    continue

        if not file_inputs:
            logger.warning("Photo upload input not available in web DM for @%s. Skipping photo.", friend)
            return False

        file_inputs[0].send_keys(abs_path)
        time.sleep(random.uniform(2.5, 4.0))

        # Send uploaded media by pressing enter on the chat input
        msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=8.0)
        msg_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(1.5, 2.5))
        check_rate_limits(browser)
        logger.info("Photo delivered to @%s.", friend)
        return True
    except Exception as e:
        logger.warning("Failed uploading photo to @%s: %s", friend, e)
        return False


def share_video_post(browser, friend: str, video_url: str) -> bool:
    if not video_url:
        return False

    logger.info("Sharing video post to @%s: %s...", friend, video_url)
    try:
        if "/messages" not in browser.current_url:
            open_conversation(browser, friend)

        msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=8.0)
        safe_click(browser, msg_input)
        time.sleep(0.4)
        msg_input.send_keys(video_url)
        time.sleep(0.5)
        msg_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(1.5, 2.5))
        check_rate_limits(browser)
        logger.info("Video post shared in chat to @%s.", friend)
        return True
    except Exception as e:
        logger.warning("Failed sharing video %s to @%s: %s", video_url, friend, e)
        return False


def execute_streak_bundle(browser, friend: str, message_text: str, image_path: str, posts: list) -> dict:
    status = {"friend": friend, "text": False, "photo": False, "posts": 0, "error": None}

    # Step 1: Text message
    try:
        send_text_message(browser, friend, message_text)
        status["text"] = True
    except Exception as e:
        status["error"] = str(e)
        return status

    # Step 2: Photo upload
    if image_path:
        time.sleep(random.uniform(2.0, 3.5))
        if send_media_photo(browser, friend, image_path):
            status["photo"] = True

    # Step 3: Video posts sharing (up to 2 posts)
    if posts:
        for p_idx, post_url in enumerate(posts[:2], start=1):
            time.sleep(random.uniform(3.0, 5.0))
            if share_video_post(browser, friend, post_url):
                status["posts"] += 1

    return status
