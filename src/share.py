"""Send rich video-share cards inside the TikTok inbox (no public watch page).

Proven flow (see capture notes): borrow a real SEND_MESSAGE frame the page
already emitted, swap its payload JSON for a video-share payload, set the
content type to share (item.f6=8), mirror client_message_id, refresh seqid/
logid, re-sign X-Bogus with the page's own byted_acrawler.frontierSign, and
send it on the live im-ws socket.

The page must be hooked (install_ws_hook) BEFORE its sockets are created, i.e.
before navigating to /messages.
"""
import logging, re, time

logger = logging.getLogger("tiktok-streak")


WS_HOOK_JS = r"""
(function () {
  if (window.__hooked) return; window.__hooked = true;
  window.__socks = []; window.__sent = []; window.__recv = [];

  function rvb(b, i) {
    var v = 0n, s = 0n, x;
    while (true) { x = BigInt(b[i++]); v |= (x & 127n) << s; s += 7n; if (!(x & 128n)) break; }
    return [v, i];
  }
  function wv(n) {
    var b = BigInt(n); var o = [];
    do { var x = Number(b & 127n); b >>= 7n; if (b > 0n) x |= 128; o.push(x); } while (b > 0n);
    return o;
  }

  window.__pb = {
    read: function (u8) {
      var out = [], i = 0, r;
      while (i < u8.length) {
        r = rvb(u8, i); var key = Number(r[0]); i = r[1]; var tag = key >> 3, wire = key & 7;
        if (wire === 0) { r = rvb(u8, i); out.push({ tag: tag, wire: 0, val: r[0] }); i = r[1]; }
        else if (wire === 2) { r = rvb(u8, i); var len = Number(r[0]); i = r[1]; out.push({ tag: tag, wire: 2, val: u8.slice(i, i + len) }); i += len; }
        else if (wire === 5) { out.push({ tag: tag, wire: 5, val: u8.slice(i, i + 4) }); i += 4; }
        else if (wire === 1) { out.push({ tag: tag, wire: 1, val: u8.slice(i, i + 8) }); i += 8; }
        else break;
      }
      return out;
    },
    write: function (fields) {
      var o = [];
      for (var k = 0; k < fields.length; k++) {
        var f = fields[k];
        o = o.concat(wv((f.tag << 3) | f.wire));
        if (f.wire === 0) o = o.concat(wv(f.val));
        else if (f.wire === 2) { var b = Array.prototype.slice.call(f.val); o = o.concat(wv(b.length)).concat(b); }
        else if (f.wire === 5 || f.wire === 1) { o = o.concat(Array.prototype.slice.call(f.val)); }
      }
      return new Uint8Array(o);
    },
    b64: function (u8) { var s = "", c = 8192; for (var i = 0; i < u8.length; i += c) s += String.fromCharCode.apply(null, u8.subarray(i, i + c)); return btoa(s); },
    b64dec: function (s) { var bin = atob(s), u = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; },
    utf8: function (u8) { return new TextDecoder().decode(u8); },
    enc: function (s) { return new TextEncoder().encode(s); },
    uuid: function () { return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) { var r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16); }); },
    get: function (fs, tag) { for (var i = 0; i < fs.length; i++) if (fs[i].tag === tag) return fs[i]; return null; }
  };

  var _WS = window.WebSocket;
  if (!_WS) return;
  function W(url, proto) {
    var ws = (proto === undefined) ? new _WS(url) : new _WS(url, proto);
    var idx = window.__socks.length; window.__socks.push(ws);
    try {
      ws.addEventListener("message", function (ev) {
        try {
          var d = ev.data;
          window.__recv.push({ sock: idx, t: Date.now(), b64: (typeof d === "string") ? null : window.__pb.b64(new Uint8Array(d)) });
        } catch (e) {}
      });
    } catch (e) {}
    return ws;
  }
  W.prototype = _WS.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) { W[k] = _WS[k]; });
  window.WebSocket = W;

  var _send = _WS.prototype.send;
  _WS.prototype.send = function (data) {
    try {
      var u8 = (typeof data === "string") ? window.__pb.enc(data) : new Uint8Array(data.buffer || data);
      var b64 = window.__pb.b64(u8);
      window.__sent.push({ t: Date.now(), url: this.url, sock: window.__socks.indexOf(this), b64: b64 });
      // Remember the latest borrowable SEND_MESSAGE frame. A reload between the
      // text send and the share forge wipes window.__sent; this survives it.
      try {
        var text = window.__pb.utf8(u8);
        if (text.indexOf("client_message_id") >= 0 && text.indexOf("aweType") >= 0) {
          sessionStorage.setItem("__tmplB64", b64);
        }
      } catch (e) {}
    } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


# The page sends DMs over HTTP, not the socket: `POST /v1/message/send` with a
# protobuf body (ground truth in the capture dumps). This hook keeps the last
# outgoing message/send request (url + body + headers) so a share card can be
# replayed over the same transport. `window.__pb` (from WS_HOOK_JS) provides the
# base64 helper. The request body is a Uint8Array; we b64 it on capture.
HTTP_SEND_HOOK_JS = r"""
(function () {
  if (window.__httpHook) return; window.__httpHook = true;
  window.__httpSend = null;
  function toU8(b) {
    try {
      if (b == null) return null;
      if (b instanceof Uint8Array) return b;
      if (typeof b === 'string') return new TextEncoder().encode(b);
      if (b instanceof ArrayBuffer) return new Uint8Array(b);
      if (b.buffer) return new Uint8Array(b.buffer);  // ArrayBufferView (DataView etc.)
      if (b instanceof Blob) return null;  // async; caller can't await here
    } catch (e) {}
    return null;
  }
  function keep(url, method, headers, body) {
    try {
      if (String(url).indexOf('/v1/message/send') < 0) return;
      var u8 = toU8(body); if (!u8 || !u8.length) return;
      var pb = window.__pb; if (!pb) return;
      var hk = {};
      if (headers) {
        if (typeof headers.forEach === 'function' && typeof headers.get === 'function') {
          headers.forEach(function (v, k) { hk[k] = v; });            // Headers object
        } else if (Array.isArray(headers)) {
          headers.forEach(function (p) { hk[p[0]] = p[1]; });
        } else {
          for (var k in headers) hk[k] = headers[k];
        }
      }
      window.__httpSend = { url: String(url), method: method || 'POST', headers: hk, b64: pb.b64(u8) };
      try {
        sessionStorage.setItem('__httpSendB64', window.__httpSend.b64);
        sessionStorage.setItem('__httpSendUrl', window.__httpSend.url);
      } catch (e) {}
    } catch (e) {}
  }
  var _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function (input, init) {
      try {
        var u = (typeof input === 'string') ? input : (input && input.url);
        var m = (init && init.method) || (input && input.method) || 'GET';
        var h = (init && init.headers) || (input && typeof input !== 'string' && input.headers) || null;
        keep(u, m, h, init && init.body);
      } catch (e) {}
      return _fetch.apply(this, arguments);
    };
  }
  var _open = XMLHttpRequest.prototype.open, _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; return _open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    try { keep(this.__u, this.__m, null, b); } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


def install_ws_hook(browser) -> None:
    """Install the socket + HTTP-send hooks; must run before the page navigates."""
    try:
        browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": WS_HOOK_JS})
    except Exception as e:
        logger.warning("Failed installing WS hook: %s", e)
    try:
        browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": HTTP_SEND_HOOK_JS})
    except Exception as e:
        logger.warning("Failed installing HTTP send hook: %s", e)


def parse_aweme_id(url: str) -> str | None:
    """Extract the numeric video id from a TikTok URL (or a bare id)."""
    if not url:
        return None
    url = url.strip()
    if url.isdigit():
        return url
    m = re.search(r"/(?:video|v|photo)/(\d{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{15,})", url)
    return m.group(1) if m else None


FETCH_DETAIL_JS = r"""
var cb = arguments[arguments.length - 1];
(function () {
  try {
    var it = %s;
    var q = "aid=1988&app_name=tiktok_web&channel=tiktok_web&device_platform=web_pc"
          + "&coverFormat=2&itemId=" + encodeURIComponent(it);
    fetch("/api/im/item_detail/?" + q, { credentials: "include" })
      .then(function (r) { return r.json(); })
      .then(function (j) { cb({ ok: true, data: j }); })
      .catch(function (e) { cb({ ok: false, error: String(e) }); });
  } catch (e) { cb({ ok: false, error: String(e) }); }
})();
"""


def fetch_item_detail(browser, item_id: str) -> dict:
    """Fetch video metadata from the page's own IM endpoint (cookies included)."""
    browser.set_script_timeout(20)
    return browser.execute_async_script(FETCH_DETAIL_JS % repr(str(item_id)))


def build_share_json(item_id: str, detail: dict) -> str:
    """Build the share-card JSON payload from an item_detail response.

    Shapes seen: data.itemInfo.itemStruct.{author,video,desc}. `video.cover`
    and `author.avatarThumb` are plain URL strings; we wrap them as {uri,url_list}.
    """
    import json as _json

    node = detail
    for key in ("data", "itemInfo", "itemStruct"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    if isinstance(node, dict) and isinstance(node.get("itemStruct"), dict):
        node = node["itemStruct"]
    if not isinstance(node, dict):
        node = {}

    author = node.get("author") if isinstance(node.get("author"), dict) else {}
    video = node.get("video") if isinstance(node.get("video"), dict) else {}

    def url_of(v):
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            ul = v.get("url_list") or v.get("urlList") or []
            return ul[0] if ul else ""
        return ""

    def uri_of(url):
        """The captured payload's `uri` = object path, e.g. 'tos-.../xxxx'."""
        if not url:
            return ""
        path = url.split("?")[0].split("://", 1)[-1]
        parts = path.split("/", 1)
        return (parts[1] if len(parts) > 1 else path).split("~")[0]

    cover = url_of(video.get("cover")) or url_of(video.get("originCover"))
    thumb = url_of(author.get("avatarThumb")) or url_of(author.get("avatarMedium")) or cover

    payload = {
        "aweType": 800,
        "itemId": str(item_id),
        "uid": str(author.get("id") or author.get("uid") or ""),
        "secUID": author.get("secUid") or author.get("secUID") or "",
        "content_name": author.get("nickname", ""),
        "content_title": node.get("desc", ""),
        "cover_url": {"uri": uri_of(cover), "url_list": [cover] if cover else []},
        "content_thumb": {"uri": uri_of(thumb), "url_list": [thumb] if thumb else []},
        "cover_width": video.get("width") or 1024,
        "cover_height": video.get("height") or 576,
    }
    return _json.dumps(payload, ensure_ascii=False)


def _forge_js(template_b64: str, share_json: str, preferred_sock: int) -> str:
    import json as _json
    return """
return (function () {
  var tmplB64 = %s;
  var shareJson = %s;
  var preferredSock = %d;
  var pb = window.__pb;
  var get = pb.get;
  var frame = pb.read(pb.b64dec(tmplB64));

  var imreq = pb.read(get(frame, 8).val);
  var mb = pb.read(get(imreq, 8).val);
  var itemIdxField = get(mb, 100);
  var item = pb.read(itemIdxField.val);

  // swap JSON payload
  get(item, 4).val = pb.enc(shareJson);
  // fresh client_message_id + mirror in f5 header
  var newCid = pb.uuid();
  get(item, 8).val = pb.enc(newCid);
  for (var hi = 0; hi < item.length; hi++) {
    if (item[hi].tag !== 5) continue;
    var hf = pb.read(item[hi].val), kF = get(hf, 1), vF = get(hf, 2);
    if (kF && pb.utf8(kF.val) === "s:client_message_id") { vF.val = pb.enc(newCid); item[hi].val = pb.write(hf); }
  }
  // content type: share video = 8 (plain text = 7)
  var f6 = get(item, 6);
  if (f6) f6.val = 8;
  itemIdxField.val = pb.write(item);
  get(imreq, 8).val = pb.write(mb);

  // fresh seqid + logid
  var seq = (function () { var m = 0; (window.__sent || []).forEach(function (s) { if (s.b64) { try { var f = pb.read(pb.b64dec(s.b64)); var x = get(f, 1); var xv = x ? Number(x.val) : 0; if (xv > m) m = xv; } catch (e) {} } }); return m + 1; })();
  get(frame, 1).val = seq;
  get(imreq, 2).val = seq;
  get(frame, 2).val = Date.now();
  var newImreq = pb.write(imreq);
  get(frame, 8).val = newImreq;

  // re-sign X-Bogus with the page's own signer
  var signed = {};
  try { signed = window.byted_acrawler.frontierSign({ "X-MS-STUB": pb.b64(newImreq) }) || {}; } catch (e) { return { error: "frontierSign: " + e }; }
  for (var i = 0; i < frame.length; i++) {
    if (frame[i].tag !== 5) continue;
    var h2 = pb.read(frame[i].val), k2 = get(h2, 1), v2 = get(h2, 2);
    var key = k2 ? pb.utf8(k2.val) : "";
    if (key === "X-Bogus") { v2.val = pb.enc(signed["X-Bogus"] || ""); frame[i].val = pb.write(h2); }
  }

  var outBytes = pb.write(frame);
  var sockIdx = (preferredSock >= 0 && window.__socks[preferredSock] && window.__socks[preferredSock].readyState === 1) ? preferredSock : -1;
  if (sockIdx < 0) { for (var j = 0; j < window.__socks.length; j++) { if (String(window.__socks[j].url || "").indexOf("im-ws") >= 0 && window.__socks[j].readyState === 1) { sockIdx = j; break; } } }
  if (sockIdx < 0) return { error: "no open im-ws socket" };

  var recvBefore = (window.__recv || []).length;
  window.__socks[sockIdx].send(outBytes);
  return { ok: true, seqid: seq, sockIdx: sockIdx, recv_before: recvBefore, x_bogus: signed["X-Bogus"] || null };
})();
""" % (_json.dumps(template_b64), _json.dumps(share_json), int(preferred_sock))


FIND_TEMPLATE_JS = r"""
return (function () {
  var sent = window.__sent || [];
  for (var i = sent.length - 1; i >= 0; i--) {
    if (!sent[i].b64) continue;
    try {
      var blob = window.__pb.utf8(window.__pb.b64dec(sent[i].b64));
      if (blob.indexOf("client_message_id") >= 0 && blob.indexOf("aweType") >= 0) {
        return { b64: sent[i].b64, sock: sent[i].sock };
      }
    } catch (e) {}
  }
  // A reload between the text send and the share forge wipes window.__sent;
  // reuse the SEND_MESSAGE frame stashed for this account.
  try {
    var b = sessionStorage.getItem("__tmplB64");
    if (b) return { b64: b, sock: -1, stale: true };
  } catch (e) {}
  return null;
})();
"""


# Replay the captured HTTP send with the payload swapped to a share card. The
# page's own request rides POST /v1/message/send, so a forged socket frame is
# never seen by the server. Body layout (ground truth): f8(imreq).f100(msgbody)
# .f4 = JSON payload, .f6 = content type, .f8 = client_message_id, .f5 = headers.
# Falls back to the frame stashed in sessionStorage if the page reloaded since
# the text send. Returns {ok,status,body} or {ok:false,error}.
def _forge_http_async_js(share_json: str) -> str:
    import json as _json
    return """
var cb = arguments[arguments.length - 1];
(function () {
  var pb = window.__pb, get = pb.get;
  if (!pb) return cb({ ok: false, error: 'no __pb (hook not installed)' });
  var cap = window.__httpSend;
  if (!cap || !cap.b64) {
    try {
      var b = sessionStorage.getItem('__httpSendB64');
      var u = sessionStorage.getItem('__httpSendUrl');
      if (b && u) cap = { b64: b, url: u, headers: {}, method: 'POST' };
    } catch (e) {}
  }
  if (!cap || !cap.b64) return cb({ ok: false, error: 'no captured HTTP send' });

  var body;
  try { body = pb.read(pb.b64dec(cap.b64)); } catch (e) { return cb({ ok: false, error: 'read body: ' + e }); }
  var f8 = get(body, 8); if (!f8) return cb({ ok: false, error: 'body.f8 missing' });
  var imreq = pb.read(f8.val);
  var f100 = get(imreq, 100); if (!f100) return cb({ ok: false, error: 'imreq.f100 missing' });
  var mb = pb.read(f100.val);
  var f4 = get(mb, 4); if (!f4) return cb({ ok: false, error: 'msgbody.f4 (payload) missing' });

  var newCid = pb.uuid();
  f4.val = pb.enc(__SHAREJSON__);
  var f6 = get(mb, 6); if (f6) f6.val = 8;
  var f8c = get(mb, 8); if (f8c) f8c.val = pb.enc(newCid);
  for (var i = 0; i < mb.length; i++) {
    if (mb[i].tag !== 5) continue;
    var hf = pb.read(mb[i].val), k = get(hf, 1), v = get(hf, 2);
    if (k && v && pb.utf8(k.val) === 's:client_message_id') { v.val = pb.enc(newCid); mb[i].val = pb.write(hf); }
  }
  f100.val = pb.write(mb);
  f8.val = pb.write(imreq);
  var out = pb.write(body);

  var headers = {};
  var src = cap.headers || {};
  for (var hk in src) { var lk = hk.toLowerCase(); if (lk === 'content-type' || lk.indexOf('x-') === 0) headers[hk] = src[hk]; }
  if (!headers['content-type'] && !headers['Content-Type']) headers['content-type'] = 'application/x-protobuf';

  fetch(cap.url, { method: cap.method || 'POST', headers: headers, body: out, credentials: 'include' })
    .then(function (r) { return r.text().then(function (t) { cb({ ok: true, status: r.status, body: t.slice(0, 800) }); }); })
    .catch(function (e) { cb({ ok: false, error: String(e) }); });
})();
""".replace("__SHAREJSON__", _json.dumps(share_json))


def send_share_card(browser, friend: str, item_id: str, template: dict = None, share_json: str = None) -> dict:
    """Send one video-share card to `friend`.

    `template` (from find_template) and `share_json` may be precomputed so a
    caller sending several cards reuses the same borrowed frame. Returns
    {sent, error}.
    """
    try:
        if share_json is None:
            detail = fetch_item_detail(browser, item_id)
            if not detail.get("ok"):
                return {"sent": False, "error": f"item_detail failed: {detail.get('error')}"}
            share_json = build_share_json(item_id, detail["data"])

        # 1. Socket forge - proven transport. The page emits a SEND_MESSAGE frame
        # for the text send; borrow it, swap in the share payload, re-sign, send
        # on the live im-ws socket. Confirmed by the server echo in __recv.
        socket_err = None
        if template is None:
            template = browser.execute_script(FIND_TEMPLATE_JS)
        if template:
            res = browser.execute_script(_forge_js(template["b64"], share_json, template.get("sock", -1))) or {}
            if res.get("error"):
                socket_err = res["error"]
            else:
                deadline = time.time() + 6
                while time.time() < deadline:
                    recv = browser.execute_script("return (window.__recv||[]).slice(%d);" % int(res.get("recv_before", 0))) or []
                    for r in recv:
                        if not r.get("b64"):
                            continue
                        try:
                            import base64 as _b64
                            blob = _b64.b64decode(r["b64"]).decode("latin1", "ignore")
                        except Exception:
                            continue
                        if item_id in blob and '"aweType":800' in blob:
                            return {"sent": True, "error": None}
                        if '"status_code":' in blob:
                            import re as _re
                            code = _re.search(r'"status_code":(\d+)', blob)
                            if code and code.group(1) != "0":
                                return {"sent": False, "error": f"status_code={code.group(1)}"}
                    time.sleep(0.5)
                socket_err = "no server ack for share card (timeout)"
        else:
            socket_err = "no SEND_MESSAGE template frame"

        # 2. Fallback: replay the page's own HTTP send with the payload swapped.
        # Some runs send the text over POST /v1/message/send with no socket frame,
        # so the socket forge finds no template; this covers that case.
        logger.info("[%s] socket forge unavailable (%s); trying HTTP replay.", friend, socket_err)
        browser.set_script_timeout(25)
        hres = browser.execute_async_script(_forge_http_async_js(share_json)) or {}
        if hres.get("ok"):
            body = hres.get("body") or ""
            import re as _re
            m = _re.search(r'"status_code"\s*:\s*(\d+)', body)
            if m:
                if m.group(1) == "0":
                    return {"sent": True, "error": None}
                return {"sent": False, "error": f"socket({socket_err}); HTTP status_code={m.group(1)}: {body[:200]}"}
            if hres.get("status") == 200:
                return {"sent": True, "error": None}
            return {"sent": False, "error": f"socket({socket_err}); HTTP {hres.get('status')}: {body[:200]}"}
        return {"sent": False, "error": f"socket({socket_err}); HTTP replay ({hres.get('error')})"}
    except Exception as e:
        logger.error("Share card to @%s failed: %s", friend, e)
        return {"sent": False, "error": str(e)}


def find_template(browser, timeout: float = 8.0) -> dict | None:
    """Return a borrowable SEND_MESSAGE frame, waiting up to `timeout` for one.

    The frame reaches the socket a beat after the page sends our text message,
    so a single immediate read races and can miss it (seen as "no share
    template frame" for a later account in the same run).
    """
    end = time.time() + timeout
    while time.time() < end:
        template = browser.execute_script(FIND_TEMPLATE_JS)
        if template:
            if template.get("stale"):
                logger.info("Reusing stashed SEND_MESSAGE frame (post-reload).")
            return template
        time.sleep(0.5)
    try:
        diag = browser.execute_script(
            "var s=window.__sent||[];"
            "return {seen:s.length, binary:s.filter(function(x){return x.b64}).length};"
        )
        logger.warning("find_template: no SEND_MESSAGE frame after %.0fs (%s).", timeout, diag)
    except Exception:
        pass
    return None


# ---- Photo (media) card ---------------------------------------------------
#
# The media button ([data-e2e="dm-new-media-btn"]) renders per-session in the
# automation browser (probe: ~3/4 fresh loads, instantly when present; some
# loads never show it). So we open the conversation and, if it is absent,
# reload the inbox and try again. A "fallback without the button" was tested
# (injecting our own <input type=file>, drop/paste) and does NOT work - TikTok's
# handler is bound to its own input - so the button is required.
#
# Mechanism (proven): click the button -> TikTok creates a detached
# <input id="im-file-input-select" type=file> and calls .click() on it. Our
# guard (installed before navigation, MEDIA_FILE_GUARD_JS) keeps that input in
# the DOM instead of opening the OS picker; we then set its file with Selenium,
# TikTok opens the "Send media" modal, and we click its Send button.

MEDIA_FILE_GUARD_JS = r"""
(function () {
  if (window.__fiGuard) return;
  window.__fiGuard = true;
  window.__fi = null;
  var _click = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (this.type === 'file') {
      try {
        this.id = this.id || 'im-file-input-select';
        if (!this.isConnected) document.body.appendChild(this);
        this.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;';
        window.__fi = this;
      } catch (e) {}
      return;  // do NOT open the OS file picker
    }
    return _click.apply(this, arguments);
  };
})();
"""

# Open @friend's conversation from the inbox list (nickname element), no public
# profile route. __NAME__ is replaced with the JSON-quoted handle.
# Match both directions: the thread's nickname may be a display name ("cel")
# that the handle ("celuley") contains, or vice versa.
OPEN_THREAD_JS = r"""
return (function () {
  var name = __NAME__;
  var t = name.toLowerCase();
  function match(txt) {
    if (!txt) return false;
    txt = txt.trim().toLowerCase();
    if (!txt) return false;
    return txt === t || txt.indexOf('@' + t) >= 0 || t.indexOf(txt) >= 0;
  }
  var cands = ['p[class*="PInfoNickname"]', '[data-e2e="dm-new-conversation-nickname"]', '[data-e2e="dm-new-conversation-item"]'];
  for (var c = 0; c < cands.length; c++) {
    var nodes = document.querySelectorAll(cands[c]);
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.offsetParent === null) continue;
      var txt = n.innerText || '';
      if (match(txt) && txt.trim().length < 60) { n.click(); return { sel: cands[c], txt: txt.trim().slice(0, 40) }; }
    }
  }
  return null;
})();
"""

MEDIA_BTN_JS = "return !!document.querySelector('[data-e2e=\"dm-new-media-btn\"]');"

CLICK_MEDIA_JS = r"""
return (function () {
  var mb = document.querySelector('[data-e2e="dm-new-media-btn"]');
  if (!mb) return false;
  var label = mb.closest('label') || document.querySelector('label[class*="LabelMedia"]');
  try { (label || mb).click(); return true; } catch (e) { return false; }
})();
"""

# Click the media modal's Send button (text like "Send (1)"), whole document.
CLICK_SEND_JS = r"""
return (function () {
  var out = { clicked: false };
  var nodes = document.querySelectorAll('button, [role="button"]');
  var best = null;
  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i];
    if (n.offsetParent === null) continue;
    var t = (n.innerText || '').trim();
    if (!t || t.length > 24) continue;
    if (/^send\b/i.test(t)) {
      var isBtn = n.tagName.toLowerCase() === 'button';
      if (!best || (isBtn && best.tagName.toLowerCase() !== 'button')) best = n;
    }
  }
  if (!best) return out;
  try { best.click(); out.clicked = true; } catch (e) { out.err = String(e); }
  return out;
})();
"""


def install_media_guard(browser) -> None:
    """Install the file-input guard; must run before the inbox navigation."""
    try:
        browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": MEDIA_FILE_GUARD_JS})
    except Exception as e:
        logger.warning("Failed installing media file guard: %s", e)


def _media_button_present(browser) -> bool:
    try:
        return bool(browser.execute_script(MEDIA_BTN_JS))
    except Exception:
        return False


def _wait_media_button(browser, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _media_button_present(browser):
            return True
        time.sleep(0.5)
    return False


def send_photo_card(browser, friend: str, photo_path: str,
                    open_retries: int = 4, per_try_wait: float = 8.0) -> dict:
    """Send one photo to @friend via the inbox media button.

    Retries by reloading the inbox when the media button does not render.
    Requires the WS hook (install_ws_hook) so the send can be confirmed via the
    picture_card frame, and the file guard (install_media_guard) installed
    before navigation. Returns {sent, error}.
    """
    import json as _json, os as _os
    from selenium.webdriver.common.by import By

    # ChromeDriver rejects a relative path for a file input ("path is not
    # absolute"), so resolve against the working directory first.
    photo_path = _os.path.abspath(photo_path)
    if not _os.path.exists(photo_path):
        return {"sent": False, "error": f"photo not found: {photo_path}"}

    try:
        opened = False
        for attempt in range(1, open_retries + 1):
            if attempt > 1 or "/messages" not in browser.current_url:
                browser.get("https://www.tiktok.com/messages?lang=en")
                time.sleep(4)
            browser.execute_script(OPEN_THREAD_JS.replace("__NAME__", _json.dumps(friend)))
            time.sleep(2)
            if _wait_media_button(browser, per_try_wait):
                opened = True
                logger.info("[%s] media button present (attempt %d).", friend, attempt)
                break
            logger.warning("[%s] media button missing (attempt %d/%d); reloading inbox.",
                           friend, attempt, open_retries)

        if not opened:
            return {"sent": False, "error": "media button never rendered after retries"}

        before_recv = browser.execute_script("return (window.__recv || []).length;") or 0
        browser.execute_script(CLICK_MEDIA_JS)

        # The guard parks TikTok's file input in the DOM; wait for it.
        inp = None
        for _ in range(20):
            els = browser.find_elements(By.ID, "im-file-input-select")
            if els:
                inp = els[0]
                break
            time.sleep(0.5)
        if inp is None:
            return {"sent": False, "error": "file input not captured (guard not triggered)"}

        inp.send_keys(photo_path)

        clicked = False
        for _ in range(20):
            if (browser.execute_script(CLICK_SEND_JS) or {}).get("clicked"):
                clicked = True
                break
            time.sleep(0.5)
        if not clicked:  # real-click fallback
            for xp in ["//button[starts-with(normalize-space(.), 'Send')]",
                       "//button[contains(., 'Send')]"]:
                els = [e for e in browser.find_elements(By.XPATH, xp) if e.is_displayed()]
                if els:
                    els[-1].click()
                    clicked = True
                    break
        if not clicked:
            return {"sent": False, "error": "Send button in media modal not found"}

        # Confirm via the picture_card frame the server echoes on the socket.
        import base64 as _b64
        deadline = time.time() + 15
        while time.time() < deadline:
            recv = browser.execute_script("return (window.__recv || []).slice(%d);" % int(before_recv)) or []
            for r in recv:
                if not r.get("b64"):
                    continue
                try:
                    blob = _b64.b64decode(r["b64"]).decode("latin1", "ignore")
                except Exception:
                    continue
                if "picture_card" in blob or "decrypt_key" in blob:
                    return {"sent": True, "error": None}
            time.sleep(0.5)
        return {"sent": False, "error": "no picture_card frame seen (timeout)"}
    except Exception as e:
        logger.error("Photo card to @%s failed: %s", friend, e)
        return {"sent": False, "error": str(e)}
