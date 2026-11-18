from dotenv import load_dotenv
import csv, logging, os, re, time
from src.browser import init_browser, authenticate_session
from src.actions import (
    find_elements_by_candidates,
    find_element_by_candidates,
    CHAT_ITEM_CANDIDATES,
)
from selenium.webdriver.common.by import By

load_dotenv()
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")

CHAT_HEADER_LINK_CANDIDATES = [
    (By.CSS_SELECTOR, 'div[data-e2e="chat-header"] a[href*="/@"]'),
    (By.XPATH, "//div[contains(@class, 'ChatHeader') or contains(@data-e2e, 'chat-header')]//a[contains(@href, '/@')]"),
    (By.XPATH, "//main//a[contains(@href, '/@')]"),
]

if __name__ == "__main__":
    logger.info("=== Starting TikTok Friends Fetcher ===")
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        logger.error("TIKTOK_SESSION_ID missing in .env")
        exit(1)

    browser, wait = init_browser()
    try:
        authenticate_session(browser, session_id, "Inbox Scanner")
        browser.get("https://www.tiktok.com/messages?lang=vi")
        threads = find_elements_by_candidates(browser, CHAT_ITEM_CANDIDATES, "chat items", timeout=15)
        logger.info("Found %d conversation threads.", len(threads))

        existing = []
        if os.path.exists("friends.csv"):
            with open("friends.csv", "r", encoding="utf-8") as f:
                existing = [line.strip().lstrip("@") for line in f if line.strip() and line.strip().lower() != "username"]

        added = 0
        for i, user_el in enumerate(threads, 1):
            user_el.click()
            time.sleep(1.5)
            header_link = find_element_by_candidates(browser, CHAT_HEADER_LINK_CANDIDATES, "header link")
            href = header_link.get_attribute("href")
            match = re.search(r"/@([^/?]+)", href)
            if not match:
                continue
            handle = match.group(1)
            if handle in existing:
                continue

            with open("friends.csv", "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if f.tell() == 0:
                    writer.writerow(["Username"])
                writer.writerow([handle])
            existing.append(handle)
            added += 1
            logger.info("[%d/%d] Added new friend: @%s", i, len(threads), handle)

        logger.info("Fetch complete. Added %d new friends (Total: %d).", added, len(existing))
    finally:
        browser.quit()
