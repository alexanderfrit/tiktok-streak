"""Read-only recorder for TikTok DM share traffic (v2: WebSocket aware).

v1 only saw HTTP, and the send request never appeared - TikTok web DMs go over
a WebSocket. v2 adds an in-page hook that wraps WebSocket.send / message,
fetch and XMLHttpRequest, so the actual share payload is captured.

Opens a headful browser in your inbox; you MANUALLY share a video to a friend
(Share -> Send to). It only reads traffic - it never sends anything itself.
"""
import json, logging, os, threading, time
from dotenv import load_dotenv
from src.browser import init_browser, authenticate_session

load_dotenv()
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("tiktok-streak")

# HTTP events worth keeping (no URL filter - we keep everything now).
WANTED_HTTP = {"Network.requestWillBeSent", "Network.requestWillBeSentExtraInfo", "Network.responseReceived"}
# WebSocket events (where the DM send actually lives).
WANTED_WS = {"Network.webSocketFrameSent", "Network.webSocketFrameReceived", "Network.webSocketCreated"}

# Injected before any page script runs; survives navigations via CDP.
HOOK_JS = r"""
(function () {
  if (window.__cap) return;
  window.__cap = [];
  function rec(o) { try { window.__cap.push(o); if (window.__cap.length > 3000) window.__cap.shift(); } catch (e) {} }
  function bufInfo(buf) {
    try {
      var u8 = new Uint8Array(buf);
      var text = '';
      try { text = new TextDecoder('utf-8', { fatal: false }).decode(u8); } catch (e) { text = ''; }
      var b64 = '';
      try {
        // Chunked so large frames encode without blowing the call stack. No cap.
        var CH = 8192, parts = [];
        for (var i = 0; i < u8.length; i += CH) {
          parts.push(String.fromCharCode.apply(null, u8.subarray(i, i + CH)));
        }
        b64 = btoa(parts.join(''));
      } catch (e) { b64 = ''; }
      return { text: text, b64: b64, bytes: u8.length, full: true };
    } catch (e) { return { err: String(e) }; }
  }
  function toPayload(data) {
    try {
      if (data == null) return { kind: 'null' };
      if (typeof data === 'string') return { kind: 'str', text: data };
      if (data instanceof ArrayBuffer) return { kind: 'arraybuffer', data: bufInfo(data) };
      if (data && data.buffer) return { kind: 'view', data: bufInfo(data.buffer) };
      if (typeof Blob !== 'undefined' && data instanceof Blob) return { kind: 'blob', size: data.size };
      return { kind: typeof data, text: String(data).slice(0, 2000) };
    } catch (e) { return { kind: 'err', err: String(e) }; }
  }

  function probeGlobals() {
    try {
      var out = {};
      var keys = Object.keys(window);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        if (/sdk|sign|acrawler|webmssdk|mssdk|bogus|secsdk|frontier|im_?core|slardar/i.test(k)) {
          out[k] = typeof window[k];
        }
      }
      return out;
    } catch (e) { return { err: String(e) }; }
  }

  var _WS = window.WebSocket;
  if (_WS) {
    var _send = _WS.prototype.send;
    _WS.prototype.send = function (data) {
      var p = toPayload(data);
      var extra = {};
      try {
        var s = (typeof p.text === 'string') ? p.text : ((p.data && p.data.text) || '');
        // Only the real message frames carry these; capture who called send().
        if (s.indexOf('aweType') >= 0 || s.indexOf('client_message_id') >= 0 || s.indexOf('command_type') >= 0) {
          extra.stack = String((new Error()).stack || '');
          extra.globals = probeGlobals();
        }
      } catch (e) {}
      var rec_obj = { dir: 'ws-sent', url: this.url, payload: p, t: Date.now() };
      for (var kk in extra) rec_obj[kk] = extra[kk];
      rec(rec_obj);
      return _send.apply(this, arguments);
    };
    function WrappedWS(url, protocols) {
      var ws = (protocols === undefined) ? new _WS(url) : new _WS(url, protocols);
      try {
        ws.addEventListener('message', function (ev) {
          rec({ dir: 'ws-recv', url: ws.url, payload: toPayload(ev.data), t: Date.now() });
        });
        rec({ dir: 'ws-open', url: url, t: Date.now() });
      } catch (e) {}
      return ws;
    }
    WrappedWS.prototype = _WS.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { WrappedWS[k] = _WS[k]; });
    window.WebSocket = WrappedWS;
  }

  var _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function (input, init) {
      try {
        var u = (typeof input === 'string') ? input : (input && input.url);
        rec({ dir: 'fetch', url: u, method: (init && init.method) || 'GET', payload: toPayload(init && init.body), t: Date.now() });
      } catch (e) {}
      return _fetch.apply(this, arguments);
    };
  }

  var _open = XMLHttpRequest.prototype.open;
  var _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; return _open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    try { rec({ dir: 'xhr', url: this.__u, method: this.__m, payload: toPayload(b), t: Date.now() }); } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


def _parse(entry) -> dict:
    try:
        return json.loads(entry["message"])["message"]
    except Exception:
        return {}


def _record(msg: dict) -> dict | None:
    method = msg.get("method", "")
    if method not in WANTED_HTTP and method not in WANTED_WS:
        return None
    params = msg.get("params", {})
    req = params.get("request", {})
    resp = params.get("response", {})
    rec = {
        "event": method,
        "url": req.get("url") or resp.get("url") or params.get("url") or "",
        "http_method": req.get("method"),
        "postData": req.get("postData"),
        "requestId": params.get("requestId"),
    }
    if method in WANTED_WS:
        fr = params.get("response", {})
        rec["opcode"] = fr.get("opcode")
        rec["payloadData"] = fr.get("payloadData")
    else:
        rec["status"] = resp.get("status")
    return rec


def main() -> None:
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        logger.error("TIKTOK_SESSION_ID missing in .env")
        return

    browser, wait = init_browser(headless=False, capture=True)

    # Turn on the Network domain so Chrome emits webSocketFrameSent/Received etc.
    try:
        browser.execute_cdp_cmd("Network.enable", {})
    except Exception as e:
        logger.warning("Network.enable failed: %s", e)

    # Inject the hook before any page script, on every navigation.
    try:
        browser.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": HOOK_JS},
        )
    except Exception as e:
        logger.warning("Could not inject page hook: %s", e)

    http_caps: list = []
    js_caps: list = []
    seen_http: set = set()
    seen_js: set = set()
    stop = threading.Event()
    lock = threading.Lock()

    def poll() -> None:
        while not stop.is_set():
            # (a) Chrome performance log (HTTP + WS frames)
            try:
                for entry in browser.get_log("performance"):
                    rec = _record(_parse(entry))
                    if not rec:
                        continue
                    key = json.dumps([rec.get("url"), rec.get("http_method"), rec.get("postData"), rec.get("payloadData")], ensure_ascii=False)
                    if key in seen_http:
                        continue
                    seen_http.add(key)
                    with lock:
                        http_caps.append(rec)
                    tag = rec.get("event", "").replace("Network.", "")
                    logger.info("http %s %s", tag, rec.get("url"))
            except Exception:
                pass
            # (b) in-page hook buffer (catches WS sends even on SPA nav)
            try:
                buf = browser.execute_script("return (window.__cap || []).slice(-500);")
                for rec in (buf or []):
                    key = json.dumps([rec.get("dir"), rec.get("url"), rec.get("t"), rec.get("payload")], ensure_ascii=False)
                    if key in seen_js:
                        continue
                    seen_js.add(key)
                    with lock:
                        js_caps.append(rec)
            except Exception:
                pass
            time.sleep(1.0)

    try:
        authenticate_session(browser, session_id, "Share Capture")
        browser.get("https://www.tiktok.com/messages?lang=vi")
        time.sleep(3)

        print("\n" + "=" * 64)
        print(" RECORDING (v2). In the Chrome window that just opened:")
        print("   1. Open the target conversation")
        print("   2. Share a video -> Send to friend -> send it")
        print("   (open the video in a NEW TAB if needed - capture still works)")
        print(" Then come back to this terminal and press ENTER.")
        print("=" * 64 + "\n")

        thread = threading.Thread(target=poll, daemon=True)
        thread.start()

        try:
            input("Press ENTER here when the share is done... ")
        except EOFError:
            time.sleep(90)  # non-interactive fallback

        stop.set()
        thread.join(timeout=3)

        # Final sweep: grab whatever remains in the in-page buffer.
        try:
            for rec in (browser.execute_script("return (window.__cap || []);") or []):
                key = json.dumps([rec.get("dir"), rec.get("url"), rec.get("t"), rec.get("payload")], ensure_ascii=False)
                if key not in seen_js:
                    seen_js.add(key)
                    js_caps.append(rec)
        except Exception:
            pass
    finally:
        browser.quit()

    ts = time.strftime("%Y%m%d_%H%M%S")
    ws_out = f"capture_ws_{ts}.json"
    with open(ws_out, "w", encoding="utf-8") as f:
        json.dump(js_caps, f, indent=2, ensure_ascii=False)
    http_out = f"capture_share_{ts}.json"
    with open(http_out, "w", encoding="utf-8") as f:
        json.dump(http_caps, f, indent=2, ensure_ascii=False)

    ws_sent = [c for c in js_caps if c.get("dir") == "ws-sent"]
    logger.info("Saved %d in-page events (%d ws-sent) -> %s", len(js_caps), len(ws_sent), ws_out)
    logger.info("Saved %d HTTP/WS network events -> %s", len(http_caps), http_out)
    if not ws_sent:
        logger.warning("No ws-sent frames captured. Did the share go through? Check both JSON files.")


if __name__ == "__main__":
    main()
