from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re, csv, os
from dotenv import load_dotenv

load_dotenv()

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, re, csv, os, logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")


def init_browser(headless=True):
    logger.info("Initializing Chrome browser (headless=%s)...", headless)
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
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
        raise ValueError("TIKTOK_SESSION_ID not found in .env")

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
        raise RuntimeError("Session invalid or expired. Check TIKTOK_SESSION_ID in .env.")
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
        time.sleep(2)
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
    logger.info("Loaded %d target friends from friends.csv: %s", total_friends, my_friends)
    if total_friends == 0:
        logger.warning("friends.csv has no target usernames. Exiting.")
        return

    message_text = os.getenv('MESSAGE')
    if not message_text:
        logger.warning("MESSAGE environment variable is empty.")

    sent_count = 0
    for idx, username in enumerate(my_friends, start=1):
        logger.info("[%d/%d] Navigating to profile: https://www.tiktok.com/@%s", idx, total_friends, username)
        try:
            browser.get(f"https://www.tiktok.com/@{username}")
            time.sleep(2)

            logger.info("[%d/%d] Finding Message button for @%s...", idx, total_friends, username)
            message_button = find_element_by_candidates(browser, PROFILE_MESSAGE_BUTTON_CANDIDATES, f"message button on @{username} profile", timeout=10)
            message_button.click()
            time.sleep(2)

            logger.info("[%d/%d] Locating chat input field...", idx, total_friends)
            message_input = find_element_by_candidates(browser, MESSAGE_INPUT_CANDIDATES, "message input field", timeout=10)
            message_input.click()
            message_input.send_keys(message_text)
            message_input.send_keys(Keys.RETURN)
            sent_count += 1
            logger.info("[%d/%d] Message sent successfully to @%s.", idx, total_friends, username)
            time.sleep(2)
        except Exception as e:
            logger.error("[%d/%d] Failed sending message to @%s: %s", idx, total_friends, username, e)

    logger.info("Auto send complete. Successfully sent %d/%d messages.", sent_count, total_friends)

