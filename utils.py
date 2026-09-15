from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timezone, timedelta
import time, re, csv, os, logging, random
import urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")


class TikTokError(Exception):
    action_tip = "Check execution details and configurations."


class SessionExpiredError(TikTokError):
    action_tip = "Update TIKTOK_SESSION_ID secret/cookie from normal browser."


class UserNotFoundError(TikTokError):
    action_tip = "Verify TikTok username handle exists and is spelled correctly."


class DMBlockedError(TikTokError):
    action_tip = "Ensure mutual follow on TikTok or adjust privacy settings to allow direct messages."


class RateLimitError(TikTokError):
    action_tip = "TikTok rate limit active. Stop bot runs for 24 hours to clear cooldown."


def get_wib_timestamp():
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S WIB")


def notify_telegram(title: str, details: str, action_tip: str = None, is_error: bool = False):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.debug("Telegram credentials not set; skipping alert.")
        return False

    status_icon = "❌" if is_error else "✅"
    wib_time = get_wib_timestamp()

    msg_lines = [
        f"{status_icon} *{title}*",
        f"🕒 `{wib_time}`",
        f"📝 {details}",
    ]
    if action_tip:
        msg_lines.append(f"💡 *Action:* `{action_tip}`")

    text = "\n".join(msg_lines)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Failed sending Telegram alert: %s", e)
        return False


def type_human_like(element, text: str):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.12))


def init_browser(headless=True):
    logger.info("Initializing Chrome browser (headless=%s)...", headless)
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    if headless:
        chrome_options.add_argument("--headless=new")
    browser = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(browser, 20)
    logger.info("Browser initialized successfully.")
    return browser, wait


def login_tiktok(browser, wait, username=None, password=None):
    # ponytail: sessionid cookie injection; automated credentials login bypassed, add when headless re-auth flow needed.
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        logger.error("TIKTOK_SESSION_ID missing from .env")
        raise SessionExpiredError("TIKTOK_SESSION_ID not found in .env or repository secrets.")

    logger.info("Injecting session cookies into browser...")
    browser.get("https://www.tiktok.com")
    for cookie_name in ("sessionid", "sessionid_ss"):
        browser.add_cookie({
            "name": cookie_name,
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        })

    logger.info("Navigating to messages endpoint...")
    browser.get("https://www.tiktok.com/messages?lang=vi")
    time.sleep(3)
    if "login" in browser.current_url:
        logger.error("Session invalid or expired. Check TIKTOK_SESSION_ID in .env.")
        raise SessionExpiredError("Session invalid or expired. TikTok redirected to login page.")
    logger.info("Session authenticated successfully.")


def _dump_debug_diagnostics(browser, label="timeout"):
    try:
        browser.save_screenshot(f"debug_{label}.png")
        with open(f"debug_{label}.html", "w", encoding="utf-8") as file:
            file.write(browser.page_source)
        logger.info("Saved diagnostics: debug_%s.png, debug_%s.html", label, label)
    except Exception as e:
        logger.warning("Failed saving diagnostics: %s", e)


def find_elements_by_candidates(browser, candidates, description, timeout=15):
    # ponytail: polled multi-selector fallback; add dedicated page-object models when UI surface expands.
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
        time.sleep(0.5)

    _dump_debug_diagnostics(browser, "timeout")
    raise TimeoutError(f"Could not locate {description} using candidate selectors.")


def find_element_by_candidates(browser, candidates, description, timeout=15):
    elements = find_elements_by_candidates(browser, candidates, description, timeout=timeout)
    return elements[0]


CHAT_ITEM_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="chat-item"]'),
    (By.CSS_SELECTOR, 'div[class*="PInfoNickname"]'),
    (By.XPATH, "//*[contains(@class, 'Nickname') or contains(@class, 'InfoNickname')]"),
    (By.CSS_SELECTOR, 'div[role="listitem"]'),
    (By.CSS_SELECTOR, 'div[class*="ConversationItem"]'),
]

PROFILE_MESSAGE_BUTTON_CANDIDATES = [
    (By.CSS_SELECTOR, '[data-e2e="message-button"]'),
    (By.XPATH, "//button[contains(., 'Message') or contains(., 'Nhắn tin')]"),
    (By.CSS_SELECTOR, 'button[class*="ButtonMessage"]'),
    (By.CSS_SELECTOR, 'a[href*="/messages"]'),
]

CHAT_HEADER_LINK_CANDIDATES = [
    (By.CSS_SELECTOR, 'div[data-e2e="chat-header"] a[href*="/@"]'),
    (By.XPATH, "//div[contains(@class, 'ChatHeader') or contains(@data-e2e, 'chat-header')]//a[contains(@href, '/@')]"),
    (By.XPATH, "//main//a[contains(@href, '/@')]"),
]

MESSAGE_INPUT_CANDIDATES = [
    (By.CSS_SELECTOR, 'div[contenteditable="true"]'),
    (By.CSS_SELECTOR, 'div[role="textbox"]'),
    (By.CSS_SELECTOR, 'div[class*="public-DraftStyleDefault-block"]'),
    (By.CSS_SELECTOR, 'textarea'),
]


def load_friends(filepath='friends.csv'):
    friends = set()
    env_friends = os.getenv("FRIENDS_LIST")
    if env_friends:
        for item in env_friends.split(","):
            cleaned = item.strip().lstrip('@')
            if cleaned:
                friends.add(cleaned)
        if friends:
            return friends

    if not os.path.exists(filepath):
        return friends
    with open(filepath, mode='r', encoding='utf-8') as file:
        for line in file:
            cleaned = line.strip().lstrip('@')
            if cleaned and cleaned.lower() != 'username':
                friends.add(cleaned)
    return friends


def get_all_friends(browser, wait):
    logger.info("Opening messages page to gather friends...")
    browser.get('https://www.tiktok.com/messages?lang=vi')

    logger.info("Waiting for conversation list to load...")
    all_user = find_elements_by_candidates(browser, CHAT_ITEM_CANDIDATES, "conversation items")
    total_chats = len(all_user)
    logger.info("Found %d conversation threads.", total_chats)

    my_friends = load_friends('friends.csv')
    logger.info("Loaded %d existing friends from friends.csv.", len(my_friends))

    added_count = 0
    for idx, user in enumerate(all_user, start=1):
        logger.info("[%d/%d] Inspecting chat thread...", idx, total_chats)
        user.click()
        time.sleep(random.uniform(1.5, 2.5))
        profile_element = find_element_by_candidates(browser, CHAT_HEADER_LINK_CANDIDATES, "chat header profile link")
        href = profile_element.get_attribute("href")
        match = re.search(r"/@([^/?]+)", href)
        if not match:
            logger.warning("[%d/%d] Could not parse username from %s", idx, total_chats, href)
            continue
        username = match.group(1)

        if username in my_friends:
            logger.info("[%d/%d] @%s already in friends.csv, skipping.", idx, total_chats, username)
            continue

        with open('friends.csv', mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if file.tell() == 0:
                writer.writerow(['Username'])
            writer.writerow([username])
        my_friends.add(username)
        added_count += 1
        logger.info("[%d/%d] Saved new friend @%s to friends.csv.", idx, total_chats, username)

    logger.info("Gathering complete. %d new friends added (total: %d).", added_count, len(my_friends))


def auto_send_message(browser, wait):
    # ponytail: direct profile message dispatch; inbox thread scanning bypassed, add when batch-broadcasting to unknown inbox users.
    my_friends = sorted(load_friends('friends.csv'))
    total_friends = len(my_friends)
    logger.info("Loaded %d target friends: %s", total_friends, my_friends)
    if total_friends == 0:
        logger.warning("No target usernames configured. Exiting.")
        return 0, 0, ["friends list is empty"]

    message_text = os.getenv('MESSAGE')
    if not message_text:
        logger.warning("MESSAGE environment variable is empty.")
        return 0, total_friends, ["MESSAGE environment variable is empty"]

    sent_count = 0
    failures = []

    for idx, username in enumerate(my_friends, start=1):
        logger.info("[%d/%d] Navigating to profile: https://www.tiktok.com/@%s", idx, total_friends, username)
        try:
            browser.get(f"https://www.tiktok.com/@{username}")
            time.sleep(random.uniform(2.5, 4.0))

            page_src = browser.page_source.lower()
            if "couldn't find this account" in page_src or "user not found" in page_src or "page not available" in page_src:
                raise UserNotFoundError(f"Account @{username} not found on TikTok.")

            logger.info("[%d/%d] Finding Message button for @%s...", idx, total_friends, username)
            try:
                message_button = find_element_by_candidates(browser, PROFILE_MESSAGE_BUTTON_CANDIDATES, f"message button on @{username} profile", timeout=8)
            except TimeoutError:
                raise DMBlockedError(f"Direct Message button not available for @{username} (mutual follow required or DMs restricted).")

            message_button.click()
            time.sleep(random.uniform(2.0, 3.5))

            logger.info("[%d/%d] Locating chat input field...", idx, total_friends)
            message_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "message input field", timeout=10)
            message_input.click()
            time.sleep(0.5)

            type_human_like(message_input, message_text)
            time.sleep(random.uniform(0.4, 0.8))
            message_input.send_keys(Keys.RETURN)

            time.sleep(1.5)
            check_src = browser.page_source.lower()
            if "sending messages too fast" in check_src or "action blocked" in check_src:
                raise RateLimitError("TikTok rate limit detected: sending messages too fast.")

            sent_count += 1
            logger.info("[%d/%d] Message sent successfully to @%s.", idx, total_friends, username)
            time.sleep(random.uniform(3.0, 5.0))
        except Exception as e:
            err_msg = f"@{username}: {e}"
            logger.error("[%d/%d] Failed sending message to @%s: %s", idx, total_friends, username, e)
            failures.append(err_msg)

    logger.info("Auto send complete. Successfully sent %d/%d messages.", sent_count, total_friends)
    return sent_count, total_friends, failures
