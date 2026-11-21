from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
import json, logging, os, re, time
from .exceptions import InboxUnavailableError, SessionExpiredError

logger = logging.getLogger("tiktok-streak")

MESSAGES_URL = "https://www.tiktok.com/messages?allow_label=true&lang=en&scene=business"

_HTTPONLY_COOKIES = frozenset({
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "uid_tt_ss",
    "odin_tt",
    "ttwid",
    "sid_ucp_v1",
    "ssid_ucp_v1",
    "d_ticket",
    "cmpl_token",
    "tt_chain_token",
})


def init_browser(headless: bool = None, capture: bool = False) -> tuple:
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

    if capture:
        # Enable network traffic logging so capture scripts can inspect DM requests.
        chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

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

    if not headless:
        install_headful_guard(browser)

    wait = WebDriverWait(browser, 20)
    logger.info("Browser initialized successfully.")
    return browser, wait


HEADFUL_GUARD_JS = r"""
(function () {
  if (window.__headfulGuard) return;
  window.__headfulGuard = true;
  window.__botAllowSidebarClick = false;
  window.__guardBannerText = "TikTok Streak Bot running — Do NOT click inbox chats";

  var SIDEBAR_SEL = [
    '[data-e2e="dm-new-conversation-list"]',
    '[data-e2e="dm-new-conversation-item"]',
    '[data-e2e="chat-item"]',
    '[data-e2e="dm-new-conversation-nickname"]',
    'p[class*="PInfoNickname"]',
    'div[class*="ConversationItem"]',
    'div[class*="ConversationList"]',
    'div[class*="ChatList"]'
  ].join(', ');

  var MODAL_EXEMPT_SEL = [
    '[role="dialog"]',
    '[class*="captcha" i]',
    '[id*="captcha" i]',
    '[class*="TUXModal"]',
    '[data-e2e*="modal"]'
  ].join(', ');

  function ensureBanner() {
    try {
      var existing = document.getElementById('__tiktok_streak_guard_banner');
      if (existing) return existing;
      var root = document.body || document.documentElement;
      if (!root) return null;
      var b = document.createElement('div');
      b.id = '__tiktok_streak_guard_banner';
      b.style.cssText = [
        'position:fixed',
        'top:8px',
        'left:50%',
        'transform:translateX(-50%)',
        'z-index:2147483647',
        'pointer-events:none',
        'background:rgba(24,24,27,0.92)',
        'color:#fafafa',
        'border:1px solid rgba(254,44,85,0.7)',
        'border-radius:8px',
        'padding:6px 14px',
        'font-family:Segoe UI,system-ui,sans-serif',
        'font-size:12px',
        'font-weight:600',
        'box-shadow:0 4px 16px rgba(0,0,0,0.35)',
        'transition:background-color 150ms ease,border-color 150ms ease'
      ].join(';');
      b.textContent = window.__guardBannerText;
      root.appendChild(b);
      return b;
    } catch (e) {
      return null;
    }
  }

  function flashBanner() {
    var b = ensureBanner();
    if (!b) return;
    var prevText = window.__guardBannerText;
    b.textContent = "Sidebar clicks locked while bot is sending — please wait";
    b.style.background = 'rgba(185, 28, 28, 0.96)';
    b.style.borderColor = '#fca5a5';
    clearTimeout(window.__guardFlashTimer);
    window.__guardFlashTimer = setTimeout(function () {
      try {
        var cur = document.getElementById('__tiktok_streak_guard_banner');
        if (cur) {
          cur.textContent = prevText;
          cur.style.background = 'rgba(24,24,27,0.92)';
          cur.style.borderColor = 'rgba(254,44,85,0.7)';
        }
      } catch (e) {}
    }, 1600);
  }

  window.__setHeadfulGuardStatus = function (msg) {
    window.__guardBannerText = String(msg || "TikTok Streak Bot running");
    var b = ensureBanner();
    if (b) b.textContent = window.__guardBannerText;
  };

  function blockSidebar(ev) {
    if (window.__botAllowSidebarClick === true) return;
    var tgt = ev && ev.target;
    if (!tgt || typeof tgt.closest !== 'function') return;
    try {
      if (tgt.closest(MODAL_EXEMPT_SEL)) return;
      if (tgt.closest(SIDEBAR_SEL)) {
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof ev.stopImmediatePropagation === 'function') {
          ev.stopImmediatePropagation();
        }
        flashBanner();
      }
    } catch (e) {}
  }

  var events = ['pointerdown', 'mousedown', 'mouseup', 'click', 'auxclick'];
  for (var i = 0; i < events.length; i++) {
    document.addEventListener(events[i], blockSidebar, true);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureBanner);
  } else {
    ensureBanner();
  }
})();
"""


def install_headful_guard(browser) -> None:
    """Lock accidental inbox sidebar clicks during visible-browser runs (CAPTCHA dialogs stay clickable)."""
    try:
        browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": HEADFUL_GUARD_JS})
    except Exception as e:
        logger.debug("Headful guard CDP registration skipped: %s", e)


def set_headful_guard_status(browser, text: str) -> None:
    """Best-effort update of the non-blocking top status banner in visible-browser mode."""
    try:
        browser.execute_script(
            "if (typeof window.__setHeadfulGuardStatus === 'function') "
            "window.__setHeadfulGuardStatus(arguments[0]);",
            str(text or ""),
        )
    except Exception:
        pass


def _clean_cookie_part(val: object) -> str:
    return re.sub(r"[\r\n]+", "", str(val)).strip()


def parse_cookie_payload(raw: str) -> dict:
    if not raw:
        return {}
    raw = str(raw).strip()
    if not raw:
        return {}

    # 1. JSON dict or list of dicts
    if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
        try:
            parsed = json.loads(raw, strict=False)
            if isinstance(parsed, dict):
                out = {}
                for k, v in parsed.items():
                    if k is None or v is None:
                        continue
                    ck = _clean_cookie_part(k)
                    cv = _clean_cookie_part(v)
                    if ck:
                        out[ck] = cv
                return out
            elif isinstance(parsed, list):
                out = {}
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        ck = _clean_cookie_part(item["name"])
                        cv = _clean_cookie_part(item["value"]) if item["value"] is not None else ""
                        if ck:
                            out[ck] = cv
                if out:
                    return out
        except Exception:
            pass

    # 2. Semicolon-separated or line-separated cookie header string
    if ";" in raw or "=" in raw:
        out = {}
        for part in re.split(r"[;\r\n]+", raw):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                ck = _clean_cookie_part(k)
                if ck.lower().startswith("cookie:"):
                    ck = ck.split(":", 1)[1].strip()
                cv = _clean_cookie_part(v).strip('"').strip()
                if ck:
                    out[ck] = cv
        if out:
            return out

    # 3. Raw single session token fallback (strip CR/LF and all whitespace)
    token = re.sub(r"\s+", "", raw)
    if not token:
        return {}
    return {
        "sessionid": token,
        "sessionid_ss": token,
        "sid_tt": token,
    }


def authenticate_session(browser, session_id: str, account_name: str = "Account") -> None:
    if not session_id:
        raise SessionExpiredError(f"[{account_name}] session_id is empty.")

    logger.info("[%s] Injecting session cookies...", account_name)
    browser.get("https://www.tiktok.com")
    browser.delete_all_cookies()
    try:
        browser.execute_cdp_cmd("Network.clearBrowserCookies", {})
    except Exception:
        pass
    try:
        browser.execute_cdp_cmd(
            "Storage.clearDataForOrigin",
            {"origin": "https://www.tiktok.com", "storageTypes": "all"},
        )
    except Exception:
        pass
    # Drop any WS/HTTP SEND_MESSAGE template stashed for the previous account
    # and reset hook buffers so replay templates never bleed across accounts.
    try:
        browser.execute_script(
            "try {"
            "  localStorage.clear();"
            "  sessionStorage.clear();"
            "  sessionStorage.removeItem('__tmplB64');"
            "  sessionStorage.removeItem('__tmplHandle');"
            "  sessionStorage.removeItem('__httpSendB64');"
            "  sessionStorage.removeItem('__httpSendUrl');"
            "  sessionStorage.removeItem('__httpSendHandle');"
            "} catch(e) {}"
            "try {"
            "  window.__sent = [];"
            "  window.__recv = [];"
            "  window.__httpSend = null;"
            "  window.__mediaSend = null;"
            "  window.__activeTargetHandle = null;"
            "  window.__friendSentStart = 0;"
            "  window.__dmUsersByHandle = {};"
            "  window.__dmUsersByNick = {};"
            "  window.__verifiedThreads = {};"
            "} catch(e) {}"
        )
    except Exception:
        pass

    # Start from the optional global extras, then let the per-account session
    # win. A full cookie dump in TIKTOK_COOKIES carries its own sessionid; merging
    # it last would clobber every account's session and cause bogus "expired".
    cookies_to_inject = parse_cookie_payload(os.getenv("TIKTOK_COOKIES", "").strip())
    cookies_to_inject.update(parse_cookie_payload(session_id))

    # Best-effort runtime hydration when only a bare sessionid was supplied without
    # regional routing cookies (tt-target-idc / store-idc).
    if (
        cookies_to_inject.get("sessionid")
        and not cookies_to_inject.get("tt-target-idc")
        and not cookies_to_inject.get("store-idc")
    ):
        try:
            from gui.services.tiktok_auth import check_session_api

            api_res = check_session_api(cookies_to_inject, timeout=8.0)
            if isinstance(api_res, dict) and api_res.get("cookie_text"):
                hydrated = parse_cookie_payload(api_res["cookie_text"])
                if isinstance(hydrated, dict):
                    for hk, hv in hydrated.items():
                        if hk and hv and hk not in cookies_to_inject:
                            cookies_to_inject[hk] = hv
        except Exception as exc:
            logger.debug("[%s] Runtime bare-session hydration skipped: %s", account_name, exc)

    for c_name, c_val in cookies_to_inject.items():
        if not c_name or not c_val:
            continue
        try:
            cookie_dict = {
                "name": c_name,
                "value": c_val,
                "path": "/",
                "secure": True,
                "httpOnly": c_name in _HTTPONLY_COOKIES,
            }
            if not c_name.startswith("__Host-"):
                cookie_dict["domain"] = ".tiktok.com"
            browser.add_cookie(cookie_dict)
        except Exception as e:
            logger.debug("Failed adding cookie %s: %s", c_name, e)

    logger.info("[%s] Verifying authentication at messages endpoint...", account_name)
    browser.get(MESSAGES_URL)
    time.sleep(4)

    if "login" in browser.current_url:
        dump_debug_diagnostics(browser, f"session_expired_{account_name}")
        if not (cookies_to_inject.get("tt-target-idc") or cookies_to_inject.get("store-idc")):
            raise SessionExpiredError(
                f"[{account_name}] Session invalid or expired (redirected to login page). "
                "Regional routing cookies (tt-target-idc / store-idc) are missing — a bare "
                "sessionid works locally in your home region but fails on US GitHub Actions runners; "
                "re-authenticate via Guided Login, Verify, or a full Cookie export."
            )
        raise SessionExpiredError(f"[{account_name}] Session invalid or expired. Redirected to login page.")

    # Some sessions are bounced from /messages to the Business Suite inbox,
    # which has a different DOM - every inbox selector then fails and the run
    # dies with a misleading "session expired" far downstream. Fail now, while
    # the URL still explains why.
    if "business-suite" in browser.current_url:
        dump_debug_diagnostics(browser, f"inbox_unavailable_{account_name}")
        raise InboxUnavailableError(
            f"[{account_name}] Web inbox unavailable: /messages redirected to "
            f"{browser.current_url}."
        )

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
