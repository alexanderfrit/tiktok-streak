from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
import json, logging, os, time
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


def parse_cookie_payload(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {}

    # 1. JSON dict or list of dicts
    if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            elif isinstance(parsed, list):
                out = {}
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        out[item["name"]] = item["value"]
                if out:
                    return out
        except Exception:
            pass

    # 2. Semicolon-separated cookie header string
    if ";" in raw or "=" in raw:
        out = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
        if out:
            return out

    # 3. Raw single session token fallback
    return {
        "sessionid": raw,
        "sessionid_ss": raw,
        "sid_tt": raw,
    }


def authenticate_session(browser, session_id: str, account_name: str = "Account") -> None:
    if not session_id:
        raise SessionExpiredError(f"[{account_name}] session_id is empty.")

    logger.info("[%s] Injecting session cookies...", account_name)
    browser.get("https://www.tiktok.com")
    browser.delete_all_cookies()

    cookies_to_inject = parse_cookie_payload(session_id)

    # Merge additional cookies from TIKTOK_COOKIES if available
    extra_env = os.getenv("TIKTOK_COOKIES", "").strip()
    if extra_env:
        extra_cookies = parse_cookie_payload(extra_env)
        cookies_to_inject.update(extra_cookies)

    for c_name, c_val in cookies_to_inject.items():
        if not c_name or not c_val:
            continue
        try:
            browser.add_cookie({
                "name": c_name,
                "value": c_val,
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })
        except Exception as e:
            logger.debug("Failed adding cookie %s: %s", c_name, e)

    logger.info("[%s] Verifying authentication at messages endpoint...", account_name)
    browser.get("https://www.tiktok.com/messages?lang=vi")
    time.sleep(4)

    if "login" in browser.current_url:
        dump_debug_diagnostics(browser, f"session_expired_{account_name}")
        raise SessionExpiredError(f"[{account_name}] Session invalid or expired. Redirected to login page.")

    logger.info("[%s] Session authenticated successfully.", account_name)


def dump_debug_diagnostics(browser, label: str = "timeout") -> None:
    # Timestamp the artifact so repeated failures in one run don't clobber each other.
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"debug_{label}_{stamp}"
    try:
        browser.save_screenshot(f"{name}.png")
        with open(f"{name}.html", "w", encoding="utf-8") as file:
            file.write(browser.page_source)
        logger.info("Saved diagnostics: %s.png, %s.html", name, name)
    except Exception as e:
        logger.warning("Failed saving debug diagnostics: %s", e)
