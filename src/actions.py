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
    (By.XPATH, "//button[contains(., 'Message') or contains(., 'Nhắn tin')]"),
    (By.CSS_SELECTOR, 'button[class*="ButtonMessage"]'),
    (By.CSS_SELECTOR, 'a[href*="/messages"]'),
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
    (By.CSS_SELECTOR, 'button[aria-label*="Share"]'),
    (By.CSS_SELECTOR, 'button[aria-label*="Chia sẻ"]'),
    (By.XPATH, "//button[contains(@class, 'Share') or contains(@aria-label, 'Share')]"),
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
    page_src = browser.page_source.lower()
    if (
        "couldn't find this account" in page_src
        or "user not found" in page_src
        or "page not available" in page_src
    ):
        raise UserNotFoundError(f"Account @{username} not found or deleted.")


def check_rate_limits(browser) -> None:
    src = browser.page_source.lower()
    if "sending messages too fast" in src or "action blocked" in src:
        raise RateLimitError("TikTok anti-spam rate limit triggered: sending messages too fast.")


def send_text_message(browser, friend: str, message_text: str) -> None:
    logger.info("Opening @%s profile...", friend)
    browser.get(f"https://www.tiktok.com/@{friend}")
    time.sleep(random.uniform(2.5, 3.5))

    verify_account_available(browser, friend)

    logger.info("Opening DM conversation for @%s...", friend)
    try:
        msg_button = find_element_by_candidates(
            browser, PROFILE_MESSAGE_BUTTON_CANDIDATES, f"Message button on @{friend} profile", timeout=8.0
        )
    except TimeoutError:
        raise DMBlockedError(f"Direct Message button not available for @{friend} (mutual follow required or DMs restricted).")

    msg_button.click()
    time.sleep(random.uniform(2.0, 3.0))

    msg_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "chat input field", timeout=10.0)
    msg_input.click()
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
            logger.warning("Photo upload input not available in web DM for @%s. Skipping photo.", friend)
            return False

        file_inputs[0].send_keys(abs_path)
        time.sleep(random.uniform(2.5, 4.0))

        # Send uploaded media by pressing enter or send button
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

    logger.info("Opening video post to share: %s...", video_url)
    try:
        browser.get(video_url)
        time.sleep(random.uniform(2.5, 3.5))

        share_btn = find_element_by_candidates(browser, VIDEO_SHARE_ICON_CANDIDATES, "Share video icon", timeout=8.0)
        share_btn.click()
        time.sleep(random.uniform(1.5, 2.5))

        # Attempt to click "Send to friends" or search for recipient
        send_friends = browser.find_elements(By.XPATH, "//*[contains(text(), 'Send to friend') or contains(text(), 'Gửi cho bạn bè')]")
        if send_friends:
            send_friends[0].click()
            time.sleep(1.5)

        # Look for friend in share modal
        target_el = browser.find_elements(By.XPATH, f"//*[contains(text(), '{friend}')]")
        if target_el:
            target_el[0].click()
            time.sleep(1.0)
            confirm_send = browser.find_elements(By.XPATH, "//button[contains(text(), 'Send') or contains(text(), 'Gửi')]")
            if confirm_send:
                confirm_send[0].click()
                time.sleep(2.0)
                logger.info("Post shared natively to @%s.", friend)
                return True

        logger.warning("Could not locate @%s in share dialog for %s.", friend, video_url)
        return False
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
