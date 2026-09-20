"""Read cookies from installed browsers (Windows).

Supported: Chromium family (Chrome, Edge, Brave, Chromium, Vivaldi, Opera) via
DPAPI + AES-GCM, and Firefox via its SQLite store. Safari (not on Windows) and
browsers without on-disk cookies are not supported - the GUI falls back to a
manual paste for those.

Cookies for a domain are read for the user to hand to the bot. Reading requires
the browser to be CLOSED (its Cookie DB is locked while running); we copy the DB
to a temp file first so a lock does not stop us, but a running browser may hold
the newest values only in memory.

Nothing here sends data anywhere; it only reads local cookie stores.
"""
import base64
import glob
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile

logger = logging.getLogger("tiktok-streak")

TIKTOK_DOMAIN = "tiktok.com"


# --------------------------------------------------------------------------
# Chromium family
# --------------------------------------------------------------------------

def _chromium_browsers() -> list:
    """Return [{id, label, user_data_dir}] for installed Chromium browsers."""
    if sys.platform != "win32":
        return []
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    specs = [
        ("chrome", "Google Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ("edge", "Microsoft Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
        ("brave", "Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
        ("chromium", "Chromium", os.path.join(local, "Chromium", "User Data")),
        ("vivaldi", "Vivaldi", os.path.join(local, "Vivaldi", "User Data")),
        ("opera", "Opera", os.path.join(roaming, "Opera Software", "Opera Stable")),
        ("opera_gx", "Opera GX", os.path.join(roaming, "Opera Software", "Opera GX Stable")),
    ]
    out = []
    for bid, label, path in specs:
        if path and os.path.isdir(path):
            out.append({"id": bid, "label": label, "user_data_dir": path})
    return out


def _chromium_profiles(user_data_dir: str) -> list:
    """Profiles inside a Chromium user-data dir that have a cookie store."""
    profiles = []
    for name in os.listdir(user_data_dir) if os.path.isdir(user_data_dir) else []:
        base = os.path.join(user_data_dir, name)
        if not os.path.isdir(base):
            continue
        # Modern layout: <profile>/Network/Cookies ; Opera keeps it flat.
        cand = os.path.join(base, "Network", "Cookies")
        if not os.path.exists(cand):
            cand = os.path.join(base, "Cookies")
        if os.path.exists(cand):
            profiles.append({"profile": name, "cookies": cand})
    return profiles


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _chromium_key(user_data_dir: str) -> bytes:
    """Decrypt the AES key stored in Local State (via DPAPI)."""
    state = os.path.join(user_data_dir, "Local State")
    with open(state, "r", encoding="utf-8") as f:
        data = json.load(f)
    enc = base64.b64decode(data["os_crypt"]["encrypted_key"])
    if enc[:5] == b"DPAPI":
        enc = enc[5:]
    return _dpapi_unprotect(enc)


def _decrypt_chromium(value: bytes, key: bytes) -> str:
    # v10/v11: AES-256-GCM. 3-byte prefix + 12-byte nonce + ciphertext + 16-byte tag.
    if value[:3] in (b"v10", b"v11"):
        from Crypto.Cipher import AES  # pycryptodome
        nonce = value[3:15]
        ct = value[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ct[:-16], ct[-16:]).decode("utf-8", "ignore")
    if value[:3] == b"v20":
        # Chrome app-bound encryption: not decryptable outside the browser.
        raise RuntimeError("app-bound (v20) cookie encryption; use manual paste")
    # Legacy: the value itself is a DPAPI blob.
    return _dpapi_unprotect(value).decode("utf-8", "ignore")


def _read_chromium(profile: dict, user_data_dir: str, domain: str) -> list:
    key = _chromium_key(user_data_dir)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(profile["cookies"], tmp.name)
    rows = []
    try:
        con = sqlite3.connect(tmp.name)
        cur = con.execute(
            "SELECT host_key, name, value, encrypted_value FROM cookies "
            "WHERE host_key LIKE ?", (f"%{domain}%",))
        for host, name, value, enc in cur.fetchall():
            try:
                val = value if value else _decrypt_chromium(bytes(enc), key)
            except Exception as e:
                logger.debug("skip cookie %s: %s", name, e)
                continue
            rows.append({"host": host, "name": name, "value": val})
        con.close()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    return rows


# --------------------------------------------------------------------------
# Firefox
# --------------------------------------------------------------------------

def _firefox_profiles() -> list:
    if sys.platform != "win32":
        return []
    appdata = os.environ.get("APPDATA", "")
    root = os.path.join(appdata, "Mozilla", "Firefox")
    ini = os.path.join(root, "profiles.ini")
    out = []
    if os.path.exists(ini):
        cur = {}
        for line in open(ini, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("["):
                if cur.get("Path"):
                    out.append(cur)
                cur = {}
            elif "=" in line:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
        if cur.get("Path"):
            out.append(cur)
        profs = []
        for p in out:
            base = os.path.join(root, p["Path"]) if p.get("IsRelative") == "1" else p["Path"]
            profs.append(base)
    else:
        profs = glob.glob(os.path.join(root, "Profiles", "*"))
    result = []
    for base in profs:
        ck = os.path.join(base, "cookies.sqlite")
        if os.path.exists(ck):
            result.append({"profile": os.path.basename(base.rstrip("/\\")), "cookies": ck})
    return result


def _read_firefox(profile: dict, domain: str) -> list:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(profile["cookies"], tmp.name)
    rows = []
    try:
        con = sqlite3.connect(tmp.name)
        cur = con.execute(
            "SELECT host, name, value FROM moz_cookies WHERE host LIKE ?", (f"%{domain}%",))
        for host, name, value in cur.fetchall():
            rows.append({"host": host, "name": name, "value": value})
        con.close()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    return rows


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def list_sources() -> list:
    """Every readable (browser, profile) pair for the GUI to offer."""
    sources = []
    for b in _chromium_browsers():
        for p in _chromium_profiles(b["user_data_dir"]):
            sources.append({
                "id": f"chromium::{b['id']}::{p['profile']}",
                "label": f"{b['label']} - {p['profile']}",
                "kind": "chromium",
                "browser_id": b["id"],
                "user_data_dir": b["user_data_dir"],
                "profile": p["profile"],
                "cookies_path": p["cookies"],
            })
    for p in _firefox_profiles():
        sources.append({
            "id": f"firefox::default::{p['profile']}",
            "label": f"Firefox - {p['profile']}",
            "kind": "firefox",
            "profile": p["profile"],
            "cookies_path": p["cookies"],
        })
    return sources


def read_source(source: dict, domain: str = TIKTOK_DOMAIN) -> dict:
    """Read cookies for a source dict from list_sources().

    Returns {cookies: {name: value}, cookie_text: "n=v; ...", sessionid: "..."}.
    """
    if source.get("kind") == "chromium":
        rows = _read_chromium({"cookies": source["cookies_path"]},
                              source["user_data_dir"], domain)
    elif source.get("kind") == "firefox":
        rows = _read_firefox({"cookies": source["cookies_path"]}, domain)
    else:
        raise ValueError("unknown source kind")

    cookies = {}
    for r in rows:
        # Later/specific hosts win; a plain name->value map is what the bot wants.
        cookies[r["name"]] = r["value"]
    cookie_text = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "cookies": cookies,
        "cookie_text": cookie_text,
        "sessionid": cookies.get("sessionid", ""),
    }


def grab_session(source_id: str) -> dict:
    """Convenience: locate a source by id and read its TikTok cookies."""
    for s in list_sources():
        if s["id"] == source_id:
            return read_source(s)
    raise ValueError(f"source not found: {source_id}")
