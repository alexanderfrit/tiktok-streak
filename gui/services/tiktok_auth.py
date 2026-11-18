"""TikTok session parsing, API verification, Guided Browser Login, and headless inbox check.

Important: NO top-level ``selenium`` import so non-browser services and unit
tests remain fast and lightweight. ``guided_browser_login`` launches the real
installed Chrome/Edge binary directly (no ChromeDriver / ``--enable-automation``)
and reads cookies via a one-shot stdlib CDP WebSocket after login completes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tiktok-streak")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LOGIN_URL = (
    "https://www.tiktok.com/login?"
    "redirect_url=https%3A%2F%2Fwww.tiktok.com%2Fmessages%3Fallow_label%3Dtrue%26lang%3Den%26scene%3Dbusiness"
)
MESSAGES_URL = "https://www.tiktok.com/messages?allow_label=true&lang=en&scene=business"

_COOKIE_ATTRS = {"path", "domain", "expires", "max-age", "secure", "httponly", "samesite", "priority"}


def _clean_cookie_part(text: str) -> str:
    return str(text or "").replace("\r", "").replace("\n", "").strip()


def parse_cookie_payload(raw: str) -> dict[str, str]:
    """Parse JSON dict/list, HTTP Cookie header, DevTools TSV rows, or raw session token."""
    cleaned = str(raw or "").strip()
    if not cleaned:
        return {}

    # 1. JSON dict or list of cookie dicts
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (
        cleaned.startswith("[") and cleaned.endswith("]")
    ):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                out: dict[str, str] = {}
                for k, v in parsed.items():
                    ck = _clean_cookie_part(str(k))
                    cv = _clean_cookie_part(str(v))
                    if ck and cv:
                        out[ck] = cv
                if out:
                    return out
            elif isinstance(parsed, list):
                out = {}
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        ck = _clean_cookie_part(str(item["name"]))
                        cv = _clean_cookie_part(str(item["value"]))
                        if ck and cv:
                            out[ck] = cv
                if out:
                    return out
        except Exception:
            pass

    # Strip optional leading "Cookie:" header prefix
    header_candidate = cleaned
    if header_candidate.lower().startswith("cookie:"):
        header_candidate = header_candidate.split(":", 1)[1].strip()

    # 2. Tab-separated DevTools cookie table rows (no '=' or ';')
    if "\t" in header_candidate and "=" not in header_candidate:
        out = {}
        for line in header_candidate.splitlines():
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) >= 2 and cols[0] and cols[1]:
                ck = _clean_cookie_part(cols[0])
                cv = _clean_cookie_part(cols[1])
                if ck and cv and " " not in ck:
                    out[ck] = cv
        if out:
            return out

    # 3. Semicolon / newline separated key=value pairs
    if ";" in header_candidate or "=" in header_candidate:
        out = {}
        # Replace CR/LF inside cookie headers with semicolons so multi-line pastes parse cleanly
        normalized = header_candidate.replace("\r\n", ";").replace("\n", ";").replace("\r", ";")
        for part in normalized.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            ck = _clean_cookie_part(k)
            cv = _clean_cookie_part(v).strip('"')
            if not ck or not cv:
                continue
            if ck.lower() in _COOKIE_ATTRS and ("sessionid" in out or "sid_tt" in out):
                continue
            out[ck] = cv
        if out:
            return out

    # 4. Raw single sessionid token fallback (strip all whitespace/CR/LF)
    token = "".join(cleaned.split())
    if not token:
        return {}
    return {
        "sessionid": token,
        "sessionid_ss": token,
        "sid_tt": token,
    }


def extract_session_summary(raw: str | dict[str, str]) -> dict[str, Any]:
    """Normalize a cookie payload and summarize `sessionid`, `cookie_text`, and count."""
    if isinstance(raw, dict):
        cookies = {
            _clean_cookie_part(k): _clean_cookie_part(v)
            for k, v in raw.items()
            if _clean_cookie_part(k) and _clean_cookie_part(v)
        }
    else:
        cookies = parse_cookie_payload(raw)

    sessionid = (
        cookies.get("sessionid")
        or cookies.get("sessionid_ss")
        or cookies.get("sid_tt")
        or ""
    )
    cookie_text = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "ok": bool(sessionid),
        "sessionid": sessionid,
        "cookie_text": cookie_text,
        "cookies": cookies,
        "count": len(cookies),
        "has_sessionid": bool(sessionid),
    }


def check_session_api(cookie_or_sid: str | dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    """Verify a TikTok cookie/sessionid via TikTok's account info endpoint."""
    summary = extract_session_summary(cookie_or_sid)
    if not summary["cookie_text"]:
        return {
            "ok": False,
            "username": "",
            "uid": "",
            "sessionid": "",
            "cookie_text": "",
            "waf_blocked": False,
            "error": "sessionid is empty",
        }

    req = urllib.request.Request(
        "https://www.tiktok.com/passport/web/account/info/?aid=1459",
        headers={
            "Cookie": summary["cookie_text"],
            "User-Agent": CHROME_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.tiktok.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "username": "",
            "uid": "",
            "sessionid": summary["sessionid"],
            "cookie_text": summary["cookie_text"],
            "waf_blocked": exc.code in (403, 429),
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "username": "",
            "uid": "",
            "sessionid": summary["sessionid"],
            "cookie_text": summary["cookie_text"],
            "waf_blocked": False,
            "error": str(exc),
        }

    try:
        data = json.loads(raw_body)
    except Exception:
        return {
            "ok": False,
            "username": "",
            "uid": "",
            "sessionid": summary["sessionid"],
            "cookie_text": summary["cookie_text"],
            "waf_blocked": True,
            "error": "TikTok returned a non-JSON / WAF response.",
        }

    d = data.get("data") or {}
    username = d.get("username") or d.get("unique_id") or d.get("screen_name") or ""
    uid = str(d.get("user_id_str") or d.get("user_id") or "")
    if username:
        return {
            "ok": True,
            "username": str(username),
            "uid": uid,
            "sessionid": summary["sessionid"],
            "cookie_text": summary["cookie_text"],
            "waf_blocked": False,
            "error": None,
        }

    msg = data.get("message") or d.get("description") or "Session invalid or expired"
    return {
        "ok": False,
        "username": "",
        "uid": uid,
        "sessionid": summary["sessionid"],
        "cookie_text": summary["cookie_text"],
        "waf_blocked": False,
        "error": str(msg),
    }


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _query_windows_app_path(exe_name: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:
        return None

    subkeys = (
        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
    )
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in subkeys:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    cand = str(val or "").strip().strip('"')
                    if cand and os.path.isfile(cand):
                        return cand
            except OSError:
                continue
    return None


def _find_browser_executable(browser_kind: str = "auto") -> tuple[str, str]:
    """Locate installed native Chrome or Edge executable on Windows (or PATH)."""
    kind = (browser_kind or "auto").strip().lower()
    if kind == "edge":
        order = ["edge"]
    elif kind == "chrome":
        order = ["chrome"]
    else:
        order = ["chrome", "edge"]

    pf = os.environ.get("ProgramFiles", "").strip()
    pf86 = os.environ.get("ProgramFiles(x86)", "").strip()
    local_app = os.environ.get("LocalAppData", "").strip()

    for target in order:
        candidates: list[str] = []
        if target == "chrome":
            for cmd in ("chrome.exe", "chrome", "google-chrome"):
                hit = shutil.which(cmd)
                if hit:
                    candidates.append(hit)
            reg_hit = _query_windows_app_path("chrome.exe")
            if reg_hit:
                candidates.append(reg_hit)
            for base in (pf, pf86, local_app):
                if base:
                    candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        else:
            for cmd in ("msedge.exe", "msedge", "microsoft-edge"):
                hit = shutil.which(cmd)
                if hit:
                    candidates.append(hit)
            reg_hit = _query_windows_app_path("msedge.exe")
            if reg_hit:
                candidates.append(reg_hit)
            for base in (pf86, pf, local_app):
                if base:
                    candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))

        for cand in candidates:
            if cand and os.path.isfile(cand):
                return cand, target

    raise RuntimeError(
        "Could not locate native Chrome or Edge executable (chrome.exe / msedge.exe) for Guided Browser Login."
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _launch_native_browser(
    profile_dir: Path,
    browser_kind: str = "auto",
    start_url: str = LOGIN_URL,
) -> tuple[subprocess.Popen, int, str]:
    """Launch real Chrome/Edge directly via subprocess without ChromeDriver or automation flags."""
    exe, used_browser = _find_browser_executable(browser_kind)
    port = _find_free_port()
    cmd = [
        exe,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1100,820",
        start_url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, port, used_browser


def _http_json_get(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _ws_cdp_call(
    ws_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Minimal pure-Python RFC 6455 WebSocket client for a single CDP request."""
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((sec_key + _WS_GUID).encode("ascii")).digest()
    ).decode("ascii")

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(req)

        raw_hdr = bytearray()
        while b"\r\n\r\n" not in raw_hdr:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed prematurely")
            raw_hdr.extend(chunk)
            if len(raw_hdr) > 65536:
                raise ConnectionError("WebSocket handshake headers too large")

        hdr_bytes, leftover = bytes(raw_hdr).split(b"\r\n\r\n", 1)
        lines = hdr_bytes.decode("latin1", "ignore").split("\r\n")
        if not lines or "101" not in lines[0]:
            raise ConnectionError(f"WebSocket upgrade failed: {lines[0] if lines else 'empty'}")

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if headers.get("sec-websocket-accept", "") != expected_accept:
            raise ConnectionError("WebSocket Sec-WebSocket-Accept mismatch")

        buf = bytearray(leftover)

        def recv_exact(n: int) -> bytes:
            while len(buf) < n:
                chunk = sock.recv(max(4096, n - len(buf)))
                if not chunk:
                    raise EOFError("WebSocket connection closed")
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        def send_frame(opcode: int, payload: bytes) -> None:
            length = len(payload)
            first = 0x80 | (opcode & 0x0F)
            if length < 126:
                hdr = struct.pack("!BB", first, 0x80 | length)
            elif length < 65536:
                hdr = struct.pack("!BBH", first, 0x80 | 126, length)
            else:
                hdr = struct.pack("!BBQ", first, 0x80 | 127, length)
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            sock.sendall(hdr + mask + masked)

        def recv_text() -> str:
            fragments: list[bytes] = []
            while True:
                b1, b2 = recv_exact(2)
                fin = bool(b1 & 0x80)
                opcode = b1 & 0x0F
                masked = bool(b2 & 0x80)
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack("!H", recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", recv_exact(8))[0]
                mask_key = recv_exact(4) if masked else b""
                payload = recv_exact(length) if length else b""
                if masked:
                    payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

                if opcode == 0x8:
                    raise EOFError("WebSocket closed by browser")
                if opcode == 0x9:
                    send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    fragments.append(payload)
                    if fin:
                        return b"".join(fragments).decode("utf-8", "ignore")

        cmd_id = 1
        send_frame(0x1, json.dumps({"id": cmd_id, "method": method, "params": params or {}}).encode("utf-8"))
        while True:
            msg = json.loads(recv_text())
            if msg.get("id") == cmd_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result") or {}
    finally:
        try:
            mask = os.urandom(4)
            sock.sendall(struct.pack("!BB", 0x88, 0x80) + mask)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def _is_tiktok_cookie_domain(domain: str) -> bool:
    dom = str(domain or "").lstrip(".").lower()
    return (not dom) or dom == "tiktok.com" or dom.endswith(".tiktok.com")


def _cdp_get_cookies(port: int, page_ws_url: str = "", timeout: float = 10.0) -> list[dict[str, Any]]:
    """Fetch cookies once via CDP WebSocket and filter to TikTok domains."""
    raw_cookies: list[dict[str, Any]] | None = None

    try:
        ver = _http_json_get(f"http://127.0.0.1:{port}/json/version", timeout=min(timeout, 3.0))
        browser_ws = str((ver or {}).get("webSocketDebuggerUrl") or "").strip()
        if browser_ws:
            res = _ws_cdp_call(browser_ws, "Storage.getCookies", timeout=timeout)
            cooks = res.get("cookies")
            if isinstance(cooks, list) and cooks:
                raw_cookies = cooks
    except Exception as exc:
        logger.debug("Storage.getCookies fallback (%s)", exc)

    if raw_cookies is None:
        ws_target = page_ws_url
        if not ws_target:
            try:
                tabs = _http_json_get(f"http://127.0.0.1:{port}/json/list", timeout=min(timeout, 3.0))
                if isinstance(tabs, list):
                    for t in tabs:
                        if isinstance(t, dict) and t.get("webSocketDebuggerUrl"):
                            ws_target = str(t["webSocketDebuggerUrl"])
                            break
            except Exception:
                pass
        if not ws_target:
            raise RuntimeError("No CDP WebSocket URL available to read cookies.")
        res = _ws_cdp_call(ws_target, "Network.getAllCookies", timeout=timeout)
        cooks = res.get("cookies")
        raw_cookies = cooks if isinstance(cooks, list) else []

    return [
        c for c in raw_cookies
        if isinstance(c, dict) and _is_tiktok_cookie_domain(str(c.get("domain") or ""))
    ]


def _cookies_to_dict(raw_cookies: list[dict[str, Any]]) -> dict[str, str]:
    now = time.time()

    def _sort_key(c: dict[str, Any]) -> tuple[int, float]:
        exp = c.get("expires")
        if exp is None:
            return (1, 0.0)
        try:
            exp_f = float(exp)
        except (TypeError, ValueError):
            return (1, 0.0)
        if exp_f <= 0:
            return (1, 0.0)
        if exp_f >= now:
            return (2, exp_f)
        return (0, exp_f)

    cookies: dict[str, str] = {}
    for c in sorted(raw_cookies, key=_sort_key):
        if not _is_tiktok_cookie_domain(str(c.get("domain") or "")):
            continue
        name = _clean_cookie_part(c.get("name", ""))
        val = _clean_cookie_part(c.get("value", ""))
        if name and val:
            cookies[name] = val
    return cookies


def guided_browser_login(
    profile_dir: str | Path,
    browser_kind: str = "auto",
    cancel_event: threading.Event | None = None,
    status_cb: Callable[[str], None] | None = None,
    timeout: int = 300,
    manual_grab_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Open the real Chrome/Edge browser with an isolated profile and capture TikTok cookies via one-shot CDP."""
    prof_path = Path(profile_dir)
    prof_path.mkdir(parents=True, exist_ok=True)

    def _emit(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    proc: subprocess.Popen | None = None
    used_browser = browser_kind
    try:
        _emit("Launching browser...")
        proc, port, used_browser = _launch_native_browser(prof_path, browser_kind=browser_kind, start_url=LOGIN_URL)
        _emit("Opening TikTok login page...")

        deadline = time.time() + max(15, int(timeout))
        messages_settled_at: float | None = None

        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return {"ok": False, "cancelled": True, "error": "Guided login cancelled."}

            if proc.poll() is not None:
                return {"ok": False, "cancelled": True, "error": "Browser window was closed."}

            manual_requested = manual_grab_event is not None and manual_grab_event.is_set()

            tabs: list[dict[str, Any]] = []
            try:
                raw_tabs = _http_json_get(f"http://127.0.0.1:{port}/json/list", timeout=1.5)
                if isinstance(raw_tabs, list):
                    tabs = [t for t in raw_tabs if isinstance(t, dict)]
            except Exception:
                if manual_requested:
                    pass
                else:
                    time.sleep(0.5)
                    continue

            page_tabs = [t for t in tabs if t.get("type", "page") == "page"] or tabs
            on_messages_page = False
            target_tab: dict[str, Any] | None = page_tabs[0] if page_tabs else None

            for tab in page_tabs:
                tab_url = str(tab.get("url") or "")
                low_url = tab_url.lower()
                if "business-suite" in low_url:
                    return {
                        "ok": False,
                        "inbox_verified": False,
                        "business_suite": True,
                        "browser": used_browser,
                        "error": "InboxUnavailable: TikTok redirected /messages to Business Suite.",
                    }
                parsed = urllib.parse.urlparse(low_url)
                host = parsed.hostname or ""
                path = parsed.path or ""
                is_auth_page = any(seg in path for seg in ("/login", "/signup", "/register"))
                if (host == "tiktok.com" or host.endswith(".tiktok.com")) and "/messages" in path and not is_auth_page:
                    on_messages_page = True
                    target_tab = tab
                    break
                if host == "tiktok.com" or host.endswith(".tiktok.com"):
                    target_tab = tab

            if on_messages_page:
                if messages_settled_at is None:
                    messages_settled_at = time.time()
                    _emit("Checking /messages inbox availability...")
            else:
                messages_settled_at = None

            settled = (
                on_messages_page
                and messages_settled_at is not None
                and (time.time() - messages_settled_at >= 1.5)
            )

            if settled or manual_requested:
                page_ws_url = str((target_tab or {}).get("webSocketDebuggerUrl") or "")
                raw_cookies = _cdp_get_cookies(port, page_ws_url=page_ws_url)
                cookies = _cookies_to_dict(raw_cookies)
                sessionid = (
                    cookies.get("sessionid")
                    or cookies.get("sessionid_ss")
                    or cookies.get("sid_tt")
                    or ""
                )
                if sessionid:
                    cookie_text = "; ".join(f"{k}={v}" for k, v in cookies.items())
                    api_res = check_session_api(cookie_text)
                    return {
                        "ok": True,
                        "sessionid": sessionid,
                        "cookie_text": cookie_text,
                        "cookies": cookies,
                        "username": api_res.get("username", ""),
                        "inbox_verified": bool(on_messages_page),
                        "business_suite": False,
                        "browser": used_browser,
                        "error": None,
                    }
                if manual_requested and manual_grab_event is not None:
                    manual_grab_event.clear()
                    _emit("No TikTok session cookie found yet — finish logging in, then try again.")
                else:
                    messages_settled_at = None

            time.sleep(0.5)

        return {
            "ok": False,
            "timed_out": True,
            "browser": used_browser,
            "error": "Timed out waiting for TikTok login to complete.",
        }
    except Exception as exc:
        logger.warning("guided_browser_login failed: %s", exc)
        return {"ok": False, "browser": used_browser, "error": str(exc)}
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
            except Exception:
                pass


def verify_inbox_headless(cookie_payload: str, timeout: float = 25.0) -> dict[str, Any]:
    """Verify session cookies in headless Chrome, catching Business Suite redirects upfront."""
    summary = extract_session_summary(cookie_payload)
    if not summary["cookie_text"]:
        return {
            "ok": False,
            "inbox_verified": False,
            "business_suite": False,
            "session_expired": True,
            "username": "",
            "error": "sessionid is empty",
        }

    api_res = check_session_api(summary["cookie_text"], timeout=min(timeout, 15.0))
    username = api_res.get("username", "")

    from src.browser import authenticate_session, init_browser
    from src.exceptions import InboxUnavailableError, SessionExpiredError

    browser = None
    try:
        browser, _wait = init_browser(headless=True)
        authenticate_session(browser, summary["cookie_text"], account_name="Verify")
        return {
            "ok": True,
            "inbox_verified": True,
            "business_suite": False,
            "session_expired": False,
            "username": username,
            "error": None,
        }
    except InboxUnavailableError as exc:
        return {
            "ok": False,
            "inbox_verified": False,
            "business_suite": True,
            "session_expired": False,
            "username": username,
            "error": str(exc),
        }
    except SessionExpiredError as exc:
        return {
            "ok": False,
            "inbox_verified": False,
            "business_suite": False,
            "session_expired": True,
            "username": username,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "inbox_verified": False,
            "business_suite": False,
            "session_expired": False,
            "username": username,
            "error": str(exc),
        }
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass


__all__ = [
    "CHROME_UA",
    "LOGIN_URL",
    "MESSAGES_URL",
    "check_session_api",
    "extract_session_summary",
    "guided_browser_login",
    "parse_cookie_payload",
    "verify_inbox_headless",
]
