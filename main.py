from utils import init_browser, login_tiktok, auto_send_message, logger
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    logger.info("=== Starting TikTok Streak Auto Send ===")
    browser, wait = init_browser()
    try:
        login_tiktok(browser, wait)
        auto_send_message(browser, wait)
    finally:
        browser.quit()
        logger.info("=== Process Finished. Browser closed. ===")
