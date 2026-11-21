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
from .browser import MESSAGES_URL
from .config import clean_handle
from .exceptions import RecipientMismatchError

logger = logging.getLogger("tiktok-streak")


WS_HOOK_JS = r"""
(function () {
  if (window.__hooked) return; window.__hooked = true;
  window.__socks = []; window.__sent = []; window.__recv = [];
  window.__activeTargetHandle = window.__activeTargetHandle || null;
  window.__friendSentStart = window.__friendSentStart || 0;

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
      var curHandle = window.__activeTargetHandle || null;
      window.__sent.push({ t: Date.now(), url: this.url, sock: window.__socks.indexOf(this), b64: b64, handle: curHandle });
      // Remember the latest borrowable SEND_MESSAGE frame for this target handle.
      try {
        var text = window.__pb.utf8(u8);
        if (text.indexOf("client_message_id") >= 0 && text.indexOf("aweType") >= 0) {
          sessionStorage.setItem("__tmplB64", b64);
          sessionStorage.setItem("__tmplHandle", String(curHandle || ""));
        }
      } catch (e) {}
    } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


# The page sends DMs over HTTP, not the socket: `POST /v1/message/send` with a
# protobuf body (ground truth in the capture dumps). This hook keeps the last
# outgoing message/send request (url + body + headers, scoped to the active handle)
# and passively harvests user handle <-> nickname metadata from IM/user responses.
HTTP_SEND_HOOK_JS = r"""
(function () {
  if (window.__httpHook) return; window.__httpHook = true;
  window.__httpSend = null;
  window.__mediaSend = null;   // last media (photo/share) message/send + status
  window.__imgCommit = 0;      // count of imagex CommitImageUpload (photo uploaded)
  window.__dmUsersByHandle = window.__dmUsersByHandle || {};
  window.__dmUsersByNick = window.__dmUsersByNick || {};

  function harvestUsers(obj, depth) {
    if (!obj || typeof obj !== 'object' || depth > 6) return;
    try {
      if (Array.isArray(obj)) {
        for (var i = 0; i < obj.length && i < 200; i++) harvestUsers(obj[i], depth + 1);
        return;
      }
      var handle = obj.uniqueId || obj.unique_id || obj.username || obj.handle;
      var nick = obj.nickname || obj.nick_name || obj.displayName || obj.name;
      if (typeof handle === 'string' && /^[A-Za-z0-9._]{2,40}$/.test(handle.trim())) {
        var hl = handle.trim().toLowerCase();
        var ns = (typeof nick === 'string') ? nick.trim() : '';
        if (ns) {
          window.__dmUsersByHandle[hl] = ns;
          var nl = ns.toLowerCase();
          var list = window.__dmUsersByNick[nl] || [];
          if (list.indexOf(hl) < 0) {
            list.push(hl);
            window.__dmUsersByNick[nl] = list;
          }
        } else if (!(hl in window.__dmUsersByHandle)) {
          window.__dmUsersByHandle[hl] = '';
        }
      }
      var keys = Object.keys(obj);
      for (var k = 0; k < keys.length && k < 80; k++) {
        var val = obj[keys[k]];
        if (val && typeof val === 'object') harvestUsers(val, depth + 1);
      }
    } catch (e) {}
  }

  function tryHarvestText(url, txt) {
    try {
      var u = String(url || '');
      if (!/(\/im\/|\/user\/|\/friend\/|\/message|conversation)/i.test(u)) return;
      if (!txt || txt.charAt(0) !== '{' && txt.charAt(0) !== '[') return;
      harvestUsers(JSON.parse(txt), 0);
    } catch (e) {}
  }

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
      var u = String(url);
      if (u.indexOf('CommitImageUpload') >= 0) { window.__imgCommit = (window.__imgCommit || 0) + 1; return; }
      if (u.indexOf('/v1/message/send') < 0) return;
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
      var curHandle = window.__activeTargetHandle || null;
      window.__httpSend = { url: String(url), method: method || 'POST', headers: hk, b64: pb.b64(u8), handle: curHandle };
      try {
        sessionStorage.setItem('__httpSendB64', window.__httpSend.b64);
        sessionStorage.setItem('__httpSendUrl', window.__httpSend.url);
        sessionStorage.setItem('__httpSendHandle', String(curHandle || ''));
      } catch (e) {}
    } catch (e) {}
  }
  function headersObj(h) {
    var hk = {};
    if (h) {
      if (typeof h.forEach === 'function' && typeof h.get === 'function') h.forEach(function (v, k) { hk[k] = v; });
      else if (Array.isArray(h)) h.forEach(function (p) { hk[p[0]] = p[1]; });
      else { for (var k in h) hk[k] = h[k]; }
    }
    return hk;
  }
  // A photo/share send carries media markers in its payload; a plain text DM does not.
  function looksMedia(u8) {
    try {
      if (!u8) return false;
      var s = window.__pb.utf8(u8);
      return s.indexOf('sender_preview') >= 0 || s.indexOf('image_uri') >= 0 ||
             s.indexOf('decrypt_key') >= 0 || s.indexOf('aweType') >= 0;
    } catch (e) { return false; }
  }
  var _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function (input, init) {
      var u = (typeof input === 'string') ? input : (input && input.url);
      var m = (init && init.method) || (input && input.method) || 'GET';
      var h = (init && init.headers) || (input && typeof input !== 'string' && input.headers) || null;
      try { keep(u, m, h, init && init.body); } catch (e) {}
      var p = _fetch.apply(this, arguments);
      try {
        if (String(u).indexOf('/v1/message/send') >= 0 && looksMedia(toU8(init && init.body))) {
          window.__mediaSend = { t: Date.now(), headers: headersObj(h), status: null, kind: 'fetch' };
          p.then(function (r) { try { window.__mediaSend.status = r.status; } catch (e) {} },
                 function () { try { window.__mediaSend.status = -1; } catch (e) {} });
        } else if (/(\/im\/|\/user\/|\/friend\/|\/message|conversation)/i.test(String(u || ''))) {
          p.then(function (r) {
            try {
              var ct = (r && r.headers && r.headers.get) ? (r.headers.get('content-type') || '') : '';
              if (ct.indexOf('json') >= 0 || ct === '') {
                r.clone().text().then(function (txt) { tryHarvestText(u, txt); }).catch(function () {});
              }
            } catch (e) {}
          }, function () {});
        }
      } catch (e) {}
      return p;
    };
  }
  var _open = XMLHttpRequest.prototype.open, _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; return _open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    try {
      keep(this.__u, this.__m, null, b);
      var xhr = this;
      if (String(this.__u).indexOf('/v1/message/send') >= 0 && looksMedia(toU8(b))) {
        window.__mediaSend = { t: Date.now(), headers: {}, status: null, kind: 'xhr' };
        xhr.addEventListener('loadend', function () { try { window.__mediaSend.status = xhr.status; } catch (e) {} });
      } else if (/(\/im\/|\/user\/|\/friend\/|\/message|conversation)/i.test(String(this.__u || ''))) {
        xhr.addEventListener('load', function () {
          try { tryHarvestText(xhr.__u, xhr.responseText); } catch (e) {}
        });
      }
    } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


def reset_friend_send_capture(browser, friend: str) -> None:
    """Scope WS/HTTP capture to `friend` and clear any prior recipient's templates."""
    target = clean_handle(friend).lower()
    try:
        browser.execute_script(
            "window.__activeTargetHandle = arguments[0];"
            "window.__friendSentStart = (window.__sent || []).length;"
            "window.__httpSend = null;"
            "window.__mediaSend = null;"
            "try {"
            "  sessionStorage.removeItem('__tmplB64');"
            "  sessionStorage.removeItem('__tmplHandle');"
            "  sessionStorage.removeItem('__httpSendB64');"
            "  sessionStorage.removeItem('__httpSendUrl');"
            "  sessionStorage.removeItem('__httpSendHandle');"
            "} catch (e) {}",
            target,
        )
    except Exception as e:
        logger.debug("reset_friend_send_capture skipped for @%s: %s", target, e)


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


def _find_template_js(expected_friend: str | None = None) -> str:
    import json as _json
    expected_arg = _json.dumps(clean_handle(expected_friend).lower() if expected_friend else None)
    return """
return (function () {
  var expected = %s;
  var sent = window.__sent || [];
  var startIdx = Math.max(0, Number(window.__friendSentStart || 0));
  if (startIdx > sent.length) startIdx = 0;
  for (var i = sent.length - 1; i >= startIdx; i--) {
    if (!sent[i].b64) continue;
    if (expected && String(sent[i].handle || '').toLowerCase() !== expected) continue;
    try {
      var blob = window.__pb.utf8(window.__pb.b64dec(sent[i].b64));
      if (blob.indexOf("client_message_id") >= 0 && blob.indexOf("aweType") >= 0) {
        return { b64: sent[i].b64, sock: sent[i].sock, handle: sent[i].handle || null };
      }
    } catch (e) {}
  }
  // A reload between the text send and the share forge wipes window.__sent;
  // reuse the SEND_MESSAGE frame ONLY if it belongs to this same recipient handle.
  try {
    var b = sessionStorage.getItem("__tmplB64");
    var bh = String(sessionStorage.getItem("__tmplHandle") || "").toLowerCase();
    if (b && (!expected || bh === expected)) {
      return { b64: b, sock: -1, stale: true, handle: bh || null };
    }
  } catch (e) {}
  return null;
})();
""" % expected_arg


FIND_TEMPLATE_JS = _find_template_js(None)


# Replay the captured HTTP send with the payload swapped to a share card. The
# page's own request rides POST /v1/message/send, so a forged socket frame is
# never seen by the server. Body layout (ground truth): f8(imreq).f100(msgbody)
# .f4 = JSON payload, .f6 = content type, .f8 = client_message_id, .f5 = headers.
# Strictly scoped to `expected_friend` so frames never bleed across recipients.
def _forge_http_async_js(share_json: str, expected_friend: str | None = None) -> str:
    import json as _json
    expected_arg = _json.dumps(clean_handle(expected_friend).lower() if expected_friend else None)
    return (
        """
var cb = arguments[arguments.length - 1];
(function () {
  var expected = __EXPECTED__;
  var pb = window.__pb, get = pb.get;
  if (!pb) return cb({ ok: false, error: 'no __pb (hook not installed)' });
  var cap = window.__httpSend;
  if (cap && expected && String(cap.handle || '').toLowerCase() !== expected) {
    cap = null;
  }
  if (!cap || !cap.b64) {
    try {
      var b = sessionStorage.getItem('__httpSendB64');
      var u = sessionStorage.getItem('__httpSendUrl');
      var ch = String(sessionStorage.getItem('__httpSendHandle') || '').toLowerCase();
      if (b && u && (!expected || ch === expected)) {
        cap = { b64: b, url: u, headers: {}, method: 'POST', handle: ch || null };
      }
    } catch (e) {}
  }
  if (!cap || !cap.b64) return cb({ ok: false, error: 'no captured HTTP send for target recipient' });

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
"""
        .replace("__EXPECTED__", expected_arg)
        .replace("__SHAREJSON__", _json.dumps(share_json))
    )


def send_share_card(browser, friend: str, item_id: str, template: dict = None, share_json: str = None) -> dict:
    """Send one video-share card to `friend`.

    Verifies the active chat header matches `@friend` before forging and before
    replaying, and refuses to reuse any WS/HTTP frame captured for another chat.
    """
    target = clean_handle(friend).lower()
    try:
        assert_active_chat_handle(browser, friend)
        if share_json is None:
            detail = fetch_item_detail(browser, item_id)
            if not detail.get("ok"):
                return {"sent": False, "error": f"item_detail failed: {detail.get('error')}"}
            share_json = build_share_json(item_id, detail["data"])

        # 1. Socket forge - proven transport, strictly scoped to @friend.
        socket_err = None
        if template is None:
            template = browser.execute_script(_find_template_js(target))
        if template:
            tmpl_handle = str(template.get("handle") or "").strip().lstrip("@").lower()
            if tmpl_handle and tmpl_handle != target:
                socket_err = f"stale template belongs to @{tmpl_handle}, not @{target}"
                template = None
        if template:
            assert_active_chat_handle(browser, friend)
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
                                socket_err = f"status_code={code.group(1)}"
                                break
                    time.sleep(0.5)
                socket_err = "no server ack for share card (timeout)"
        elif socket_err is None:
            socket_err = "no SEND_MESSAGE template frame"

        # 2. Fallback: replay the page's own HTTP send (strictly scoped to @friend).
        assert_active_chat_handle(browser, friend)
        logger.info("[%s] socket forge unavailable (%s); trying HTTP replay.", friend, socket_err)
        browser.set_script_timeout(25)
        hres = browser.execute_async_script(_forge_http_async_js(share_json, expected_friend=target)) or {}
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
    except RecipientMismatchError as e:
        logger.error("Recipient mismatch while sending share card to @%s: %s", friend, e)
        return {"sent": False, "error": str(e)}
    except Exception as e:
        logger.error("Share card to @%s failed: %s", friend, e)
        return {"sent": False, "error": str(e)}


def find_template(browser, timeout: float = 8.0, expected_friend: str | None = None) -> dict | None:
    """Return a borrowable SEND_MESSAGE frame for `expected_friend`, waiting up to `timeout`."""
    script = _find_template_js(expected_friend) if expected_friend else FIND_TEMPLATE_JS
    end = time.time() + timeout
    while time.time() < end:
        template = browser.execute_script(script)
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

# Read ONLY the right-pane active chat header's exact @username handle.
# Never reads left sidebar items or chat message bubbles.
READ_ACTIVE_CHAT_HANDLE_JS = r"""
return (function () {
  var SIDEBAR_EXCL = '[data-e2e="dm-new-conversation-list"], [data-e2e="dm-new-conversation-item"], [data-e2e="chat-item"], div[class*="ConversationList"], div[class*="ChatList"]';
  var BUBBLE_EXCL = '[data-e2e="dm-new-message-list"], [data-e2e="dm-new-chat-item"], [data-e2e="dm-new-chat-bottom"], [data-e2e="dm-new-input-editor"], [data-e2e="message-input-area"], [data-e2e="top-header"]';
  function cleanHandle(raw) {
    if (!raw || typeof raw !== 'string') return null;
    var s = raw.trim().replace(/^@+/, '').trim();
    if (/^[A-Za-z0-9._]{2,40}$/.test(s)) return s.toLowerCase();
    return null;
  }
  function handleFromHref(href) {
    if (!href) return null;
    var m = String(href).match(/\/@([A-Za-z0-9._]{2,40})(?:[/?#]|$)/);
    return m ? m[1].toLowerCase() : null;
  }
  function isVisible(el) {
    if (!el) return false;
    try {
      var st = window.getComputedStyle(el);
      if (st && (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0')) return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function isExcluded(el) {
    return Boolean(el && el.closest && (el.closest(SIDEBAR_EXCL) || el.closest(BUBBLE_EXCL)));
  }
  function fiberPeerHandle(el) {
    try {
      var keys = Object.keys(el);
      var fKey = null;
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf('__reactFiber$') === 0 || keys[i].indexOf('__reactInternalInstance$') === 0 || keys[i].indexOf('__reactProps$') === 0) {
          fKey = keys[i]; break;
        }
      }
      if (!fKey) return null;
      var cur = el[fKey], steps = 0;
      var seen = [];
      function scanObj(o, d) {
        if (!o || typeof o !== 'object' || d > 3 || seen.indexOf(o) >= 0) return null;
        seen.push(o);
        if (typeof o.uniqueId === 'string') return cleanHandle(o.uniqueId);
        if (typeof o.unique_id === 'string') return cleanHandle(o.unique_id);
        var pref = ['toUser', 'peerUser', 'peer', 'targetUser', 'user', 'participant', 'conversation'];
        for (var p = 0; p < pref.length; p++) {
          if (o[pref[p]] && typeof o[pref[p]] === 'object') {
            var h = scanObj(o[pref[p]], d + 1);
            if (h) return h;
          }
        }
        return null;
      }
      while (cur && steps < 6) {
        var props = cur.memoizedProps || cur;
        var h = scanObj(props, 0);
        if (h) return h;
        cur = cur.return;
        steps++;
      }
    } catch (e) {}
    return null;
  }
  function extractFromRoot(root) {
    if (!root || !isVisible(root)) return null;
    if (isExcluded(root)) return null;
    var links = root.querySelectorAll('a[href*="/@"]');
    for (var i = 0; i < links.length; i++) {
      if (!isVisible(links[i]) || isExcluded(links[i])) continue;
      var h = handleFromHref(links[i].getAttribute('href') || links[i].href);
      if (h) return h;
    }
    var texts = root.querySelectorAll('[data-e2e="chat-uniqueid"], p, span, a, div, h1, h2, h3');
    for (var j = 0; j < texts.length; j++) {
      var el = texts[j];
      if (!isVisible(el) || isExcluded(el)) continue;
      if (el.tagName === 'DIV' && el.children && el.children.length > 0) continue;
      var txt = (el.innerText || el.textContent || '').trim();
      var m = txt.match(/^@([A-Za-z0-9._]{2,40})$/);
      if (m) return m[1].toLowerCase();
    }
    return fiberPeerHandle(root);
  }

  // Priority 0: Explicit chat header uniqueId badge (<p data-e2e="chat-uniqueid">@handle</p>)
  var uidNodes = document.querySelectorAll('[data-e2e="chat-uniqueid"]');
  for (var ui = 0; ui < uidNodes.length; ui++) {
    var uidEl = uidNodes[ui];
    if (!isVisible(uidEl) || isExcluded(uidEl)) continue;
    var uidTxt = (uidEl.innerText || uidEl.textContent || '').trim();
    var um = uidTxt.match(/^@?([A-Za-z0-9._]{2,40})$/);
    if (um) return um[1].toLowerCase();
    var parentA = uidEl.closest('a[href*="/@"]');
    if (parentA) {
      var ph = handleFromHref(parentA.getAttribute('href') || parentA.href);
      if (ph) return ph;
    }
  }

  var headerSels = [
    'div[class*="DivChatHeader"]',
    'div[class*="ChatHeader"]',
    '[data-e2e="dm-new-chat-header"]',
    '[data-e2e="chat-header"]',
    '[data-e2e*="chat-header"]',
    'div[class*="ConversationHeader"]',
    'div[class*="HeaderWrapper"]'
  ];
  for (var s = 0; s < headerSels.length; s++) {
    var hdrs = document.querySelectorAll(headerSels[s]);
    for (var hi = 0; hi < hdrs.length; hi++) {
      var h = extractFromRoot(hdrs[hi]);
      if (h) return h;
    }
  }

  // Geometric right-pane header band fallback (top right pane, excluding sidebar & chat bubbles)
  try {
    var paneLeft = Math.floor((window.innerWidth || 1280) * 0.18);
    var convList = document.querySelector('[data-e2e="dm-new-conversation-list"], div[class*="ConversationList"], div[class*="ChatList"]');
    if (convList) {
      var cr = convList.getBoundingClientRect();
      if (cr.width > 0 && cr.right > 0) paneLeft = Math.max(paneLeft, cr.right - 4);
    }
    var box = document.querySelector('[data-e2e="dm-new-chatbox"]') || document.querySelector('[data-e2e="dm-new-message-list"]');
    if (box) {
      var br = box.getBoundingClientRect();
      if (br.width > 0 && br.left > 0) paneLeft = Math.max(paneLeft, br.left - 4);
    }
    var links2 = document.querySelectorAll('a[href*="/@"]');
    for (var li = 0; li < links2.length; li++) {
      var a = links2[li];
      if (!isVisible(a) || isExcluded(a)) continue;
      var ar = a.getBoundingClientRect();
      if (ar.left >= paneLeft && ar.top >= 0 && ar.top <= 220) {
        var lh = handleFromHref(a.getAttribute('href') || a.href);
        if (lh) return lh;
      }
    }
    var nodes2 = document.querySelectorAll('p, span, a, div');
    for (var ni = 0; ni < nodes2.length; ni++) {
      var nd = nodes2[ni];
      if (!isVisible(nd) || isExcluded(nd)) continue;
      if (nd.tagName === 'DIV' && nd.children && nd.children.length > 0) continue;
      var nr = nd.getBoundingClientRect();
      if (nr.left >= paneLeft && nr.top >= 0 && nr.top <= 220) {
        var nm = (nd.innerText || nd.textContent || '').trim().match(/^@([A-Za-z0-9._]{2,40})$/);
        if (nm) return nm[1].toLowerCase();
      }
    }
  } catch (e) {}
  return null;
})();
"""


def get_active_chat_handle(browser) -> str | None:
    """Return the normalized @username shown in the right-pane chat header, or None."""
    try:
        raw = browser.execute_script(READ_ACTIVE_CHAT_HANDLE_JS)
    except Exception:
        return None
    if not isinstance(raw, str):
        return None
    cleaned = clean_handle(raw).lower()
    return cleaned or None


def assert_active_chat_handle(browser, expected_friend: str) -> str:
    """Verify the open chat header exactly matches `expected_friend` (fail-closed)."""
    expected = clean_handle(expected_friend).lower()
    actual = get_active_chat_handle(browser)
    if not actual or actual != expected:
        seen = f"@{actual}" if actual else "<unverified>"
        raise RecipientMismatchError(
            f"Active chat recipient mismatch: expected @{expected}, header shows {seen}."
        )
    return actual


# Inspect visible inbox rows in the left conversation list (without clicking).
INSPECT_INBOX_ROWS_JS = r"""
return (function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll('[data-e2e="dm-new-conversation-item"]'));
  if (!rows.length) rows = Array.prototype.slice.call(document.querySelectorAll('[data-e2e="chat-item"]'));
  if (!rows.length) rows = Array.prototype.slice.call(document.querySelectorAll('div[class*="ConversationItem"]'));
  if (!rows.length) {
    var nicks = document.querySelectorAll('p[class*="PInfoNickname"]');
    for (var ni = 0; ni < nicks.length; ni++) {
      var p = nicks[ni].closest('div[role="listitem"], li, div');
      if (p && rows.indexOf(p) < 0) rows.push(p);
    }
  }
  function isVisible(el) {
    if (!el) return false;
    try { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && el.offsetParent !== null; }
    catch (e) { return false; }
  }
  function cleanH(raw) {
    if (!raw || typeof raw !== 'string') return null;
    var s = raw.trim().replace(/^@+/, '').trim();
    return /^[A-Za-z0-9._]{2,40}$/.test(s) ? s.toLowerCase() : null;
  }
  function extractFiberInfo(el) {
    var out = { handle: null, unread: false };
    try {
      var keys = Object.keys(el), fKey = null;
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf('__reactFiber$') === 0 || keys[i].indexOf('__reactInternalInstance$') === 0 || keys[i].indexOf('__reactProps$') === 0) {
          fKey = keys[i]; break;
        }
      }
      if (!fKey) return out;
      var cur = el[fKey], steps = 0, seen = [];
      function scan(o, d) {
        if (!o || typeof o !== 'object' || d > 3 || seen.indexOf(o) >= 0) return;
        seen.push(o);
        if (!out.handle) {
          if (typeof o.uniqueId === 'string') out.handle = cleanH(o.uniqueId);
          else if (typeof o.unique_id === 'string') out.handle = cleanH(o.unique_id);
        }
        if (typeof o.unreadCount === 'number' && o.unreadCount > 0) out.unread = true;
        if (typeof o.unread_count === 'number' && o.unread_count > 0) out.unread = true;
        if (o.hasUnread === true) out.unread = true;
        var ks = ['user', 'peer', 'peerUser', 'toUser', 'conversation', 'item', 'data', 'thread'];
        for (var j = 0; j < ks.length; j++) {
          if (o[ks[j]] && typeof o[ks[j]] === 'object') scan(o[ks[j]], d + 1);
        }
      }
      while (cur && steps < 5) {
        scan(cur.memoizedProps || cur, 0);
        cur = cur.return;
        steps++;
      }
    } catch (e) {}
    return out;
  }
  function isUnreadRow(row, fiberUnread) {
    if (fiberUnread) return true;
    try {
      if (row.querySelector('[data-e2e*="unread" i], [class*="Unread" i], [class*="unread"], [class*="RedDot" i], [class*="BadgeCount" i]')) {
        return true;
      }
      var aria = (row.getAttribute('aria-label') || '') + ' ' + (row.querySelector('[aria-label]') ? row.querySelector('[aria-label]').getAttribute('aria-label') || '' : '');
      if (/\bunread\b|\bbelum dibaca\b/i.test(aria)) return true;
      var spans = row.querySelectorAll('span, div');
      for (var i = 0; i < spans.length; i++) {
        var sp = spans[i];
        if (!isVisible(sp)) continue;
        var cls = String(sp.className || '');
        var t = (sp.innerText || '').trim();
        if (/^\d{1,3}\+?$/.test(t) && /badge|unread|dot|count|num/i.test(cls)) return true;
      }
    } catch (e) {}
    return false;
  }

  var byNick = window.__dmUsersByNick || {};
  var out = [];
  for (var idx = 0; idx < rows.length; idx++) {
    var r = rows[idx];
    if (!isVisible(r)) continue;
    var nickEl = r.querySelector('p[class*="PInfoNickname"], [data-e2e="dm-new-conversation-nickname"]');
    var nick = nickEl ? (nickEl.innerText || '').trim() : '';
    var rawText = (r.innerText || '').trim();
    if (!nick && rawText) {
      nick = rawText.split(/\r?\n/)[0].trim().slice(0, 60);
    }
    var h = null;
    var a = r.querySelector('a[href*="/@"]');
    if (a) {
      var hm = String(a.getAttribute('href') || a.href || '').match(/\/@([A-Za-z0-9._]{2,40})/);
      if (hm) h = hm[1].toLowerCase();
    }
    if (!h) {
      var am = rawText.match(/@([A-Za-z0-9._]{2,40})/);
      if (am) h = am[1].toLowerCase();
    }
    var fib = extractFiberInfo(r);
    if (!h && fib.handle) h = fib.handle;
    if (!h && nick) {
      var cands = byNick[nick.toLowerCase()];
      if (Array.isArray(cands) && cands.length === 1) h = cands[0];
    }
    var unread = isUnreadRow(r, fib.unread);
    var previewLine = rawText.replace(/\s+/g, ' ').slice(0, 60);
    var convId = (r.getAttribute('data-conv-id') || '').trim();
    out.push({
      idx: idx,
      nickname: nick,
      handle: h,
      unread: unread,
      selected: r.getAttribute('aria-selected') === 'true',
      rowKey: convId || (nick.toLowerCase() + '|' + (h || '') + '|' + previewLine.slice(0, 28))
    });
  }
  return out;
})();
"""


CLICK_INBOX_ROW_JS = r"""
return (function (targetIdx) {
  var rows = Array.prototype.slice.call(document.querySelectorAll('[data-e2e="dm-new-conversation-item"]'));
  if (!rows.length) rows = Array.prototype.slice.call(document.querySelectorAll('[data-e2e="chat-item"]'));
  if (!rows.length) rows = Array.prototype.slice.call(document.querySelectorAll('div[class*="ConversationItem"]'));
  if (!rows.length) {
    var nicks = document.querySelectorAll('p[class*="PInfoNickname"]');
    for (var ni = 0; ni < nicks.length; ni++) {
      var p = nicks[ni].closest('div[role="listitem"], li, div');
      if (p && rows.indexOf(p) < 0) rows.push(p);
    }
  }
  var r = rows[targetIdx];
  if (!r) return false;
  window.__botAllowSidebarClick = true;
  try {
    r.scrollIntoView({ block: 'center' });
    r.click();
    return true;
  } catch (e) {
    return false;
  } finally {
    window.__botAllowSidebarClick = false;
  }
})(arguments[0]);
"""


# Legacy helper retained strictly for backwards compatibility: matches exact
# @handle only (never substring display names like single-letter "M").
OPEN_THREAD_JS = r"""
return (function () {
  var name = __NAME__;
  var t = String(name || '').replace(/^@+/, '').trim().toLowerCase();
  if (!t) return null;
  function match(txt) {
    if (!txt) return false;
    txt = txt.trim().toLowerCase();
    if (!txt) return false;
    return txt === t || txt === ('@' + t) || txt.indexOf('@' + t) >= 0;
  }
  var cands = ['[data-e2e="dm-new-conversation-item"]', '[data-e2e="chat-item"]', '[data-e2e="dm-new-conversation-nickname"]', 'p[class*="PInfoNickname"]'];
  for (var c = 0; c < cands.length; c++) {
    var nodes = document.querySelectorAll(cands[c]);
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.offsetParent === null) continue;
      var hrefEl = n.querySelector ? n.querySelector('a[href*="/@"]') : null;
      var hrefMatch = hrefEl && String(hrefEl.getAttribute('href') || '').toLowerCase().indexOf('/@' + t) >= 0;
      var txt = n.innerText || '';
      if (hrefMatch || (match(txt) && txt.trim().length < 60)) {
        window.__botAllowSidebarClick = true;
        try { n.click(); } finally { window.__botAllowSidebarClick = false; }
        return { sel: cands[c], txt: txt.trim().slice(0, 40) };
      }
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


# A sent message shows up as a new chat bubble; that DOM change is a more
# reliable "did it send" signal than the picture_card WS echo (photo sends can
# go over HTTP, and the echo is not always delivered).
_CHAT_ITEMS_JS = "return document.querySelectorAll('[data-e2e=\"dm-new-chat-item\"]').length;"

# The media modal's Send button is on screen only while the modal is open - a
# successful send closes it, so its presence means the send has not gone yet.
SEND_BTN_PRESENT_JS = r"""
return (function () {
  var n = document.querySelectorAll('button, [role="button"]');
  for (var i = 0; i < n.length; i++) {
    var x = n[i];
    if (x.offsetParent === null) continue;
    var t = (x.innerText || '').trim();
    if (/^send\b/i.test(t) && t.length < 24) return true;
  }
  return false;
})();
"""


# Content of the newest chat bubble. A bubble appearing after the Send click is
# NOT proof the photo was delivered: TikTok renders an "unsupported media"
# placeholder bubble (or an optimistic bubble that never syncs). Read the bubble
# so a failed media send is not logged as success.
LAST_CHAT_JS = r"""
return (function () {
  var items = document.querySelectorAll('[data-e2e="dm-new-chat-item"]');
  if (!items.length) return null;
  var last = items[items.length - 1];
  return {
    text: (last.innerText || '').trim().slice(0, 160),
    imgs: last.querySelectorAll('img:not([class*="Avatar"]):not([class*="avatar"])').length,
    len: (last.innerHTML || '').length
  };
})();
"""


def _chat_item_count(browser) -> int:
    try:
        return int(browser.execute_script(_CHAT_ITEMS_JS) or 0)
    except Exception:
        return 0


# TikTok renders a placeholder bubble when it refuses the media ("This message
# type isn't supported"). That is a *failed* send, so it must never count as ok.
_UNSUPPORTED_TEXT = ("isn't supported", "is not supported", "not supported",
                     "unsupported", "không được hỗ trợ")


def _last_chat_bubble(browser) -> dict | None:
    try:
        return browser.execute_script(LAST_CHAT_JS)
    except Exception:
        return None


def _bubble_is_unsupported(b: dict | None) -> bool:
    """True when the newest bubble is TikTok's 'media not supported' placeholder."""
    if not b:
        return False
    t = (b.get("text") or "").lower().replace("’", "'")
    return any(s in t for s in ("isn't supported", "not supported", "unsupported", "download tiktok app", "không được hỗ trợ"))


def _bubble_has_image(b: dict | None) -> bool:
    if not b or _bubble_is_unsupported(b):
        return False
    text = (b.get("text") or "").strip()
    if text.endswith("%"):
        return False
    return b.get("imgs", 0) > 0


# Network truth for a media send: the page uploads the image (imagex
# CommitImageUpload) then POSTs /v1/message/send and gets 2xx. A new bubble can
# appear optimistically and never sync, so the network status is the real proof.
_MEDIA_NET_JS = r"""
return (function () {
  var m = window.__mediaSend;
  return { commit: window.__imgCommit || 0, media: m ? { status: m.status, t: m.t } : null };
})();
"""


def _media_net_state(browser) -> dict:
    try:
        return browser.execute_script(_MEDIA_NET_JS) or {}
    except Exception:
        return {}


def _media_net_ok(state: dict) -> bool:
    m = (state or {}).get("media")
    return bool(m) and isinstance(m.get("status"), int) and 200 <= m["status"] < 300


def send_photo_card(browser, friend: str, photo_path: str,
                    open_retries: int = 2, per_try_wait: float = 3.0, stop_event=None) -> dict:
    """Send one photo to @friend via the inbox media button.

    Verifies the active chat header matches `@friend` before opening the media
    picker and before every modal Send click so photos can never be sent to an
    interrupted or wrong conversation. Returns {sent, error}.
    """
    import os as _os
    from selenium.webdriver.common.by import By
    from .actions import open_inbox_chat, dismiss_modals

    # ChromeDriver rejects a relative path for a file input ("path is not
    # absolute"), so resolve against the working directory first.
    photo_path = _os.path.abspath(photo_path)
    if not _os.path.exists(photo_path):
        return {"sent": False, "error": f"photo not found: {photo_path}"}

    target = clean_handle(friend).lower()
    try:
        opened = False
        for attempt in range(1, open_retries + 1):
            if stop_event is not None and stop_event.is_set():
                return {"sent": False, "error": "Cancelled"}
            if attempt > 1 or "/messages" not in str(getattr(browser, "current_url", "") or ""):
                browser.get(MESSAGES_URL)
                time.sleep(4)
                open_inbox_chat(browser, friend, stop_event=stop_event)
            elif get_active_chat_handle(browser) != target:
                open_inbox_chat(browser, friend, stop_event=stop_event)
            assert_active_chat_handle(browser, friend)
            time.sleep(1.5)
            if _wait_media_button(browser, per_try_wait):
                assert_active_chat_handle(browser, friend)
                opened = True
                logger.info("[%s] media button present (attempt %d).", friend, attempt)
                break
            logger.warning("[%s] media button missing (attempt %d/%d); reloading inbox.",
                           friend, attempt, open_retries)

        if not opened:
            return {"sent": False, "error": "media button never rendered (TikTok Web desktop does not support photo upload in this session)"}

        assert_active_chat_handle(browser, friend)
        items_before = _chat_item_count(browser)
        before_recv = browser.execute_script("return (window.__recv || []).length;") or 0
        # Fresh network markers for this send.
        browser.execute_script("window.__mediaSend = null; return 0;")
        browser.execute_script(CLICK_MEDIA_JS)

        # The guard parks TikTok's file input in the DOM; wait for it.
        inp = None
        for _ in range(20):
            if stop_event is not None and stop_event.is_set():
                dismiss_modals(browser)
                return {"sent": False, "error": "Cancelled"}
            els = browser.find_elements(By.ID, "im-file-input-select")
            if els:
                inp = els[0]
                break
            time.sleep(0.5)
        if inp is None:
            return {"sent": False, "error": "file input not captured (guard not triggered)"}

        inp.send_keys(photo_path)

        # The media modal must open (Send button appears) before we can send.
        modal_seen = False
        for _ in range(20):
            if stop_event is not None and stop_event.is_set():
                dismiss_modals(browser)
                return {"sent": False, "error": "Cancelled"}
            if browser.execute_script(SEND_BTN_PRESENT_JS):
                modal_seen = True
                break
            time.sleep(0.5)
        if not modal_seen:
            return {"sent": False, "error": "media preview modal did not open after choosing a file"}

        # Click Send, then confirm by DOM. Always verify header handle before Send.
        import base64 as _b64
        deadline = time.time() + 20
        clicked_once = False
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                dismiss_modals(browser)
                return {"sent": False, "error": "Cancelled"}
            # Network is the truth: the page uploaded the image and the send
            # came back 2xx. A bubble alone can be optimistic and never sync.
            if _media_net_ok(_media_net_state(browser)):
                time.sleep(1.5)
                bubble = _last_chat_bubble(browser)
                if _bubble_is_unsupported(bubble):
                    logger.warning("[%s] media rejected by server: %s", friend, bubble)
                    return {"sent": False, "error": f"media not supported ({bubble.get('text') if bubble else ''})"}
                logger.info("[%s] photo confirmed by network (media send 2xx).", friend)
                return {"sent": True, "error": None}

            still_modal = browser.execute_script(SEND_BTN_PRESENT_JS)
            if not still_modal and _chat_item_count(browser) > items_before:
                bubble = _last_chat_bubble(browser)
                if _bubble_is_unsupported(bubble):
                    logger.warning("[%s] media rejected by server: %s", friend, bubble)
                    return {"sent": False, "error": f"media not supported (bubble={bubble})"}
                if _bubble_has_image(bubble):
                    logger.info("[%s] photo confirmed by new chat bubble.", friend)
                    return {"sent": True, "error": None}
                # text-only optimistic bubble: keep waiting for the network send

            # Bonus signal: the server echoed a picture_card/decrypt_key frame.
            recv = browser.execute_script("return (window.__recv || []).slice(%d);" % int(before_recv)) or []
            for r in recv:
                if not r.get("b64"):
                    continue
                try:
                    blob = _b64.b64decode(r["b64"]).decode("latin1", "ignore")
                except Exception:
                    continue
                if "picture_card" in blob or "decrypt_key" in blob:
                    logger.info("[%s] photo confirmed by server frame.", friend)
                    return {"sent": True, "error": None}

            if still_modal:
                try:
                    assert_active_chat_handle(browser, friend)
                except RecipientMismatchError as rme:
                    logger.error("[%s] %s Dismissing media modal without sending.", friend, rme)
                    dismiss_modals(browser)
                    return {"sent": False, "error": str(rme)}
                # Modal still up: the send did not register. Re-click it.
                clicked = (browser.execute_script(CLICK_SEND_JS) or {}).get("clicked")
                if not clicked:  # real-click fallback on the modal's Send button
                    for xp in ["//button[starts-with(normalize-space(.), 'Send')]",
                               "//button[contains(., 'Send')]"]:
                        els = [e for e in browser.find_elements(By.XPATH, xp) if e.is_displayed()]
                        if els:
                            els[-1].click()
                            clicked = True
                            break
                clicked_once = clicked_once or bool(clicked)
                if not clicked:
                    return {"sent": False, "error": "Send button in media modal not found"}
            time.sleep(1.0)

        # Final check: the network send may have landed and the bubble appeared late.
        if _media_net_ok(_media_net_state(browser)):
            return {"sent": True, "error": None}
        bubble = _last_chat_bubble(browser)
        if _bubble_has_image(bubble):
            return {"sent": True, "error": None}
        net = _media_net_state(browser)
        if browser.execute_script(SEND_BTN_PRESENT_JS):
            return {"sent": False, "error": "photo not sent: media modal stayed open after Send"}
        if _bubble_is_unsupported(bubble):
            return {"sent": False, "error": f"media not supported (bubble={bubble})"}
        return {"sent": False,
                "error": f"photo send unconfirmed (no media network send; net={net}, bubble={bubble})"}
    except RecipientMismatchError as e:
        dismiss_modals(browser)
        logger.error("Photo card aborted for @%s: %s", friend, e)
        return {"sent": False, "error": str(e)}
    except Exception as e:
        logger.error("Photo card to @%s failed: %s", friend, e)
        return {"sent": False, "error": str(e)}
