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
      var u8 = (typeof data === "string") ? null : new Uint8Array(data.buffer || data);
      window.__sent.push({ t: Date.now(), url: this.url, sock: window.__socks.indexOf(this), b64: u8 ? window.__pb.b64(u8) : null });
    } catch (e) {}
    return _send.apply(this, arguments);
  };
})();
"""


def install_ws_hook(browser) -> None:
    """Install the WebSocket hook; must run before the page creates its sockets."""
    try:
        browser.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": WS_HOOK_JS})
    except Exception as e:
        logger.warning("Failed installing WS hook: %s", e)


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
      var blob = new TextDecoder().decode(window.__pb.b64dec(sent[i].b64));
      if (blob.indexOf("client_message_id") >= 0 && blob.indexOf("aweType") >= 0) {
        return { b64: sent[i].b64, sock: sent[i].sock };
      }
    } catch (e) {}
  }
  return null;
})();
"""


def send_share_card(browser, friend: str, item_id: str, template: dict = None, share_json: str = None) -> dict:
    """Send one video-share card to `friend`.

    `template` (from find_template) and `share_json` may be precomputed so a
    caller sending several cards reuses the same borrowed frame. Returns
    {sent, error}.
    """
    try:
        if template is None:
            template = browser.execute_script(FIND_TEMPLATE_JS)
        if not template:
            return {"sent": False, "error": "No SEND_MESSAGE template frame; send a text first."}
        if share_json is None:
            detail = fetch_item_detail(browser, item_id)
            if not detail.get("ok"):
                return {"sent": False, "error": f"item_detail failed: {detail.get('error')}"}
            share_json = build_share_json(item_id, detail["data"])

        res = browser.execute_script(_forge_js(template["b64"], share_json, template.get("sock", -1))) or {}
        if res.get("error"):
            return {"sent": False, "error": res["error"]}

        # confirm: a frame echoing our itemId (aweType 800) means the server accepted it
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
        return {"sent": False, "error": "no server ack for share card (timeout)"}
    except Exception as e:
        logger.error("Share card to @%s failed: %s", friend, e)
        return {"sent": False, "error": str(e)}


def find_template(browser) -> dict | None:
    return browser.execute_script(FIND_TEMPLATE_JS)
