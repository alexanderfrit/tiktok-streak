from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
import logging, os, time
from .exceptions import SessionExpiredError

logger = logging.getLogger("tiktok-streak")


def init_browser(headless: bool = None) -> tuple:
    if headless is None:
        env_headless = os.getenv("HEADLESS", "true").strip().lower()
        headless = env_headless not in ("false", "0", "no")

    logger.info("Initializing Chrome browser (headless=%s)...", headless)
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    if headless:
        chrome_options.add_argument("--headless=new")

    browser = webdriver.Chrome(options=chrome_options)

    # CDP stealth script
    try:
        browser.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                window.chrome = {
                    runtime: {}
                };
                """
            },
        )
    except Exception as e:
        logger.debug("CDP stealth patch skipped: %s", e)

    wait = WebDriverWait(browser, 20)
    logger.info("Browser initialized successfully.")
    return browser, wait


def authenticate_session(browser, session_id: str, account_name: str = "Account") -> None:
    if not session_id:
        raise SessionExpiredError(f"[{account_name}] session_id is empty.")

    logger.info("[%s] Injecting session cookie...", account_name)
    browser.get("https://www.tiktok.com")
    browser.delete_all_cookies()

    for cookie_name in ("sessionid", "sessionid_ss"):
        browser.add_cookie({
            "name": cookie_name,
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        })

    logger.info("[%s] Verifying authentication at messages endpoint...", account_name)
    browser.get("https://www.tiktok.com/messages?lang=vi")
    time.sleep(3)

    if "login" in browser.current_url:
        dump_debug_diagnostics(browser, f"session_expired_{account_name}")
        raise SessionExpiredError(f"[{account_name}] Session invalid or expired. Redirected to login page.")

    logger.info("[%s] Session authenticated successfully.", account_name)


def dump_debug_diagnostics(browser, label: str = "timeout") -> None:
    try:
        browser.save_screenshot(f"debug_{label}.png")
        with open(f"debug_{label}.html", "w", encoding="utf-8") as file:
            file.write(browser.page_source)
        logger.info("Saved diagnostics: debug_%s.png, debug_%s.html", label, label)
    except Exception as e:
        logger.warning("Failed saving debug diagnostics: %s", e)
