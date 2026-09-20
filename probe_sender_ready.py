"""Readiness probe: are the two primitives reachable from page context?

Primitive 1: window.byted_acrawler.frontierSign  (page's own frame signer)
Primitive 2: the live im-ws WebSocket instance    (page's own transport)

We install a constructor wrapper BEFORE page scripts run (CDP
addScriptToEvaluateOnNewDocument) so the IM client's socket is recorded, then
navigate to the inbox and report both. Read-only: it never sends anything.
"""
import json, logging, os, time
from dotenv import load_dotenv
from src.browser import init_browser, authenticate_session

load_dotenv()
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")

# Runs before any page script: record every WebSocket the page creates.
INSTALL_JS = r"""
(function () {
  if (window.__socks) return;
  window.__socks = [];
  var _WS = window.WebSocket;
  if (!_WS) return;
  function WrappedWS(url, protocols) {
    var ws = (protocols === undefined) ? new _WS(url) : new _WS(url, protocols);
    try { window.__socks.push(ws); } catch (e) {}
    return ws;
  }
  WrappedWS.prototype = _WS.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) { WrappedWS[k] = _WS[k]; });
  window.WebSocket = WrappedWS;
})();
"""

CHECK_JS = r"""
return (function () {
  var out = { sockets: [], signer: {}, self_uid: null };

  (window.__socks || []).forEach(function (ws) {
    out.sockets.push({ url: String(ws.url || "").slice(0, 90), state: ws.readyState });
  });

  try {
    var ba = window.byted_acrawler;
    out.signer.has_acrawler = !!ba;
    out.signer.frontierSign_type = ba ? typeof ba.frontierSign : "n/a";
    if (ba && typeof ba.frontierSign === "function") {
      var r = ba.frontierSign({ "X-MS-STUB": "probe-test-payload-123" });
      out.signer.result_type = typeof r;
      try { out.signer.result_json = JSON.stringify(r).slice(0, 400); }
      catch (e) { out.signer.result_json = "non-serializable"; }
    }
  } catch (e) {
    out.signer.error = String(e).slice(0, 200);
  }

  // Try to read the logged-in uid from a few likely spots (best-effort).
  try {
    var m = document.cookie.match(/uid_tt=([^;]+)/) || document.cookie.match(/tt_uid=([^;]+)/);
    if (m) out.self_uid = m[1];
  } catch (e) {}
  return out;
})();
"""


def main() -> None:
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        logger.error("TIKTOK_SESSION_ID missing in .env")
        return

    browser, wait = init_browser(headless=False)
    try:
        # Install the socket recorder before navigation.
        try:
            browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": INSTALL_JS})
        except Exception as e:
            logger.warning("Could not install socket hook: %s", e)

        authenticate_session(browser, session_id, "Readiness Probe")
        # Re-navigate so the IM client builds its socket through our wrapper.
        browser.get("https://www.tiktok.com/messages?lang=vi")
        time.sleep(6)
        result = browser.execute_script(CHECK_JS) or {}
    finally:
        browser.quit()

    ts = time.strftime("%Y%m%d_%H%M%S")
    out = f"probe_sender_ready_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    logger.info("Saved readiness probe to %s", out)


if __name__ == "__main__":
    main()
