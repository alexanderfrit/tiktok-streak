from utils import init_browser, login_tiktok, get_all_friends, logger
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    logger.info("=== Starting TikTok Friends Fetch ===")
    browser, wait = init_browser()
    try:
        login_tiktok(browser, wait)
        get_all_friends(browser, wait)
    finally:
        browser.quit()
        logger.info("=== Process Finished. Browser closed. ===")