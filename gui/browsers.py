"""Read TikTok cookies from installed browsers (Windows).

Two families, two realities:

- **Chromium family** (Chrome, Edge, Brave, Vivaldi, Opera, Yandex, Thorium and
  other Chromium forks). Cookies are AES-GCM encrypted (key in `Local State`,
  unwrapped via DPAPI). While the browser is OPEN it holds the cookie DB with an
  exclusive lock, and on Windows even a read-only open fails (WinError 32 /
  errno 13) - so a locked browser is reported, not silently mis-read. Close it,
  then read.
- **Firefox family** (Firefox, Zen, Waterfox, LibreWolf, Floorp, Pale Moon,
  SeaMonkey, Mullvad, Tor, ...). Cookies are plaintext in SQLite (`cookies.sqlite`)
  and the DB is readable while the browser runs. This family is auto-discovered
  by scanning for `profiles.ini` / `Profiles/*/cookies.sqlite`, so obscure forks
  are covered without a hardcoded list.

Reads use a read-only SQLite URI first (no copy, no temp file); a copy fallback
covers the case where the URI open is refused but a byte copy is allowed.
Nothing here sends data anywhere - it only reads local cookie stores.
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


class BrowserLocked(RuntimeError):
    """The browser is running and holds its cookie DB with an exclusive lock."""


# --------------------------------------------------------------------------
# Read helpers (work live where the OS allows it)
# --------------------------------------------------------------------------

def _connect(path: str) -> sqlite3.Connection:
    """Open a DB read-only without copying. Raises BrowserLocked if locked."""
    uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    uri += "?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "unable to open" in msg or "permission" in msg:
            raise BrowserLocked(f"{os.path.basename(path)} is locked (browser open)")
        raise


def _query(path: str, sql: str, params: tuple = ()) -> list:
    """Run a query via the read-only URI; fall back to a byte copy if refused.

    The copy fallback exists because a few builds refuse the URI open but still
    allow reading the file bytes. If both fail the browser is holding the lock.
    """
    try:
        con = _connect(path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except BrowserLocked:
        raise
    except sqlite3.OperationalError:
        pass

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        shutil.copy2(path, tmp.name)
    except (PermissionError, OSError) as e:
        os.remove(tmp.name)
        raise BrowserLocked(f"{os.path.basename(path)} is locked (browser open): {e}")
    try:
        con = sqlite3.connect(tmp.name)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def _count_tiktok(path: str, table: str, host_col: str, domain: str = TIKTOK_DOMAIN) -> int:
    """Cheap presence check: how many cookie rows are for the domain."""
    try:
        rows = _query(path, f"SELECT COUNT(*) FROM {table} WHERE {host_col} LIKE ?", (f"%{domain}%",))
        return int(rows[0][0]) if rows else 0
    except BrowserLocked:
        raise
    except Exception:
        return 0


# --------------------------------------------------------------------------
# Chromium family
# --------------------------------------------------------------------------

def _local() -> str:
    return os.environ.get("LOCALAPPDATA", "")


def _roam() -> str:
    return os.environ.get("APPDATA", "")


# Known Chromium forks and where their user-data dir lives. Obscure ones are
# welcome here; the label is what the user sees in the picker.
_CHROMIUM_SPECS = [
    ("chrome", "Google Chrome", "local", "Google/Chrome/User Data"),
    ("chrome_beta", "Chrome Beta", "local", "Google/Chrome Beta/User Data"),
    ("chrome_dev", "Chrome Dev", "local", "Google/Chrome Dev/User Data"),
    ("chrome_canary", "Chrome Canary", "local", "Google/Chrome SxS/User Data"),
    ("edge", "Microsoft Edge", "local", "Microsoft/Edge/User Data"),
    ("edge_beta", "Edge Beta", "local", "Microsoft/Edge Beta/User Data"),
    ("edge_dev", "Edge Dev", "local", "Microsoft/Edge Dev/User Data"),
    ("brave", "Brave", "local", "BraveSoftware/Brave-Browser/User Data"),
    ("brave_beta", "Brave Beta", "local", "BraveSoftware/Brave-Browser-Beta/User Data"),
    ("brave_nightly", "Brave Nightly", "local", "BraveSoftware/Brave-Browser-Nightly/User Data"),
    ("chromium", "Chromium", "local", "Chromium/User Data"),
    ("thorium", "Thorium", "local", "Thorium/User Data"),
    ("iridium", "Iridium", "local", "Iridium/User Data"),
    ("slimjet", "Slimjet", "local", "Slimjet/User Data"),
    ("iron", "SRWare Iron", "local", "SRWare Iron/User Data"),
    ("cent", "Cent Browser", "local", "CentBrowser/User Data"),
    ("yandex", "Yandex", "local", "Yandex/YandexBrowser/User Data"),
    ("vivaldi", "Vivaldi", "local", "Vivaldi/User Data"),
    ("whale", "Naver Whale", "local", "Naver/Whale/User Data"),
    ("coccoc", "Cốc Cốc", "local", "CocCoc/Browser/User Data"),
    ("360", "360 Chrome", "local", "360Chrome/Chrome/User Data"),
    ("avast", "Avast Secure Browser", "local", "AVAST Software/Browser/User Data"),
    ("avg", "AVG Secure Browser", "local", "AVG/Browser/User Data"),
    ("dragon", "Comodo Dragon", "local", "Comodo/Dragon/User Data"),
    ("opera", "Opera", "roam", "Opera Software/Opera Stable"),
    ("opera_gx", "Opera GX", "roam", "Opera Software/Opera GX Stable"),
    ("opera_beta", "Opera Beta", "roam", "Opera Software/Opera Next"),
]


def _chromium_roots() -> list:
    if sys.platform != "win32":
        return []
    out = []
    for bid, label, which, rel in _CHROMIUM_SPECS:
        base = _local() if which == "local" else _roam()
        path = os.path.join(base, *rel.split("/"))
        if path and os.path.isdir(path):
            out.append({"id": bid, "label": label, "user_data_dir": path})
    return out


def _chromium_profiles(user_data_dir: str) -> list:
    """Profiles inside a Chromium user-data dir that have a cookie store.

    Handles the modern `<profile>/Network/Cookies`, the flat `<dir>/Cookies`
    (Opera), and the dir itself when it holds cookies at the root.
    """
    candidates = [("Default", user_data_dir)]
    if os.path.isdir(user_data_dir):
        for name in os.listdir(user_data_dir):
            base = os.path.join(user_data_dir, name)
            if os.path.isdir(base):
                candidates.append((name, base))
    seen, profiles = set(), []
    for name, base in candidates:
        for cand in (os.path.join(base, "Network", "Cookies"), os.path.join(base, "Cookies")):
            if os.path.exists(cand) and cand not in seen:
                seen.add(cand)
                profiles.append({"profile": name, "cookies": cand})
                break
    return profiles


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _chromium_key(user_data_dir: str) -> bytes:
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
        nonce, ct = value[3:15], value[15:]
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct[:-16], ct[-16:]).decode("utf-8", "ignore")
    if value[:3] == b"v20":
        # Chrome app-bound encryption: not decryptable outside the browser.
        raise RuntimeError("app-bound (v20) cookie encryption; use manual paste")
    # Legacy: the value itself is a DPAPI blob.
    return _dpapi_unprotect(value).decode("utf-8", "ignore")


def _read_chromium(path: str, user_data_dir: str, domain: str) -> list:
    key = _chromium_key(user_data_dir)
    rows = _query(path,
                  "SELECT host_key, name, value, encrypted_value FROM cookies WHERE host_key LIKE ?",
                  (f"%{domain}%",))
    out = []
    for _host, name, value, enc in rows:
        try:
            val = value if value else _decrypt_chromium(bytes(enc), key)
        except Exception as e:
            logger.debug("skip cookie %s: %s", name, e)
            continue
        out.append({"name": name, "value": val})
    return out


# --------------------------------------------------------------------------
# Firefox family (auto-discovered - covers Zen, Waterfox, LibreWolf, ...)
# --------------------------------------------------------------------------

# Friendly labels for the common forks; anything else falls back to a title-cased
# folder name, so a new obscure fork still shows up with a sensible name.
_FIREFOX_LABELS = {
    "firefox": "Firefox", "zen": "Zen", "waterfox": "Waterfox",
    "librewolf": "LibreWolf", "floorp": "Floorp", "palemoon": "Pale Moon",
    "basilisk": "Basilisk", "seamonkey": "SeaMonkey", "mullvad": "Mullvad Browser",
    "tor browser": "Tor Browser", "mercury": "Mercury", "ghostery": "Ghostery",
}


def _firefox_family_roots() -> list:
    """Every Firefox-family root that holds profiles.ini or a Profiles dir.

    Scans the AppData roots one level deep (and Mozilla/* one deeper) so a fork
    added later - Zen, LibreWolf, etc. - is picked up with no code change.
    """
    if sys.platform != "win32":
        return []
    seen, roots = set(), []

    def consider(path: str):
        if not path or path in seen or not os.path.isdir(path):
            return
        has_ini = os.path.exists(os.path.join(path, "profiles.ini"))
        has_profiles = os.path.isdir(os.path.join(path, "Profiles"))
        if has_ini or has_profiles:
            seen.add(path)
            roots.append(path)

    for base in (_roam(), _local()):
        if not base or not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            consider(os.path.join(base, name))
        mozilla = os.path.join(base, "Mozilla")
        if os.path.isdir(mozilla):
            for name in os.listdir(mozilla):
                consider(os.path.join(mozilla, name))
    return roots


def _parse_profiles_ini(root: str) -> list:
    """Profile dirs from profiles.ini (Path may be relative or absolute)."""
    ini = os.path.join(root, "profiles.ini")
    if not os.path.exists(ini):
        return []
    blocks, cur = [], {}
    for line in open(ini, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line.startswith("["):
            if cur:
                blocks.append(cur)
            cur = {}
        elif "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip().lower()] = v.strip()
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        p = b.get("path")
        if not p:
            continue
        base = os.path.join(root, p) if b.get("isrelative") == "1" else p
        out.append(base)
    return out


def _firefox_profiles(root: str) -> list:
    """Profiles under a Firefox-family root that have a cookies.sqlite."""
    dirs = _parse_profiles_ini(root)
    dirs += glob.glob(os.path.join(root, "Profiles", "*"))
    dirs.append(root)  # some forks keep cookies.sqlite at the root
    seen, profiles = set(), []
    for base in dirs:
        ck = os.path.join(base, "cookies.sqlite")
        key = os.path.normcase(os.path.abspath(ck))  # same file, any separator
        if os.path.exists(ck) and key not in seen:
            seen.add(key)
            profiles.append({"profile": os.path.basename(base.rstrip("/\\")) or base, "cookies": ck})
    return profiles


def _firefox_label(root: str) -> str:
    name = os.path.basename(root.rstrip("/\\")).lower()
    return _FIREFOX_LABELS.get(name, name.title())


def _read_firefox(path: str, domain: str) -> list:
    rows = _query(path, "SELECT host, name, value FROM moz_cookies WHERE host LIKE ?", (f"%{domain}%",))
    return [{"name": name, "value": value} for _host, name, value in rows]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def list_sources(enrich: bool = True) -> list:
    """Every readable (browser, profile) pair for the GUI to offer.

    With `enrich`, each source is tagged with whether it holds a TikTok login
    (`has_tiktok`) and whether its DB is currently locked by a running browser
    (`locked`), and the list is sorted so logged-in, unlocked sources come first.
    That fixes the "wrong profile / didn't detect my browser" problem.
    """
    sources = []

    for b in _chromium_roots():
        for p in _chromium_profiles(b["user_data_dir"]):
            sources.append({
                "id": f"chromium::{b['id']}::{p['profile']}",
                "label": f"{b['label']} — {p['profile']}",
                "browser_label": b["label"],
                "kind": "chromium",
                "user_data_dir": b["user_data_dir"],
                "profile": p["profile"],
                "cookies_path": p["cookies"],
            })

    for root in _firefox_family_roots():
        label = _firefox_label(root)
        for p in _firefox_profiles(root):
            sources.append({
                "id": f"firefox::{os.path.basename(root)}::{p['profile']}",
                "label": f"{label} — {p['profile']}",
                "browser_label": label,
                "kind": "firefox",
                "profile": p["profile"],
                "cookies_path": p["cookies"],
            })

    if enrich:
        for s in sources:
            try:
                if s["kind"] == "chromium":
                    s["has_tiktok"] = _count_tiktok(s["cookies_path"], "cookies", "host_key")
                else:
                    s["has_tiktok"] = _count_tiktok(s["cookies_path"], "moz_cookies", "host")
                s["locked"] = False
            except BrowserLocked:
                s["has_tiktok"] = 0
                s["locked"] = True
        sources.sort(key=lambda s: (not s.get("has_tiktok"), s.get("locked"), s["label"]))

    return sources


def read_source(source: dict, domain: str = TIKTOK_DOMAIN) -> dict:
    """Read cookies for a source dict from list_sources().

    Returns {cookies: {name: value}, cookie_text: "n=v; ...", sessionid: "..."}.
    Raises BrowserLocked when a running Chromium browser holds the DB.
    """
    if source.get("kind") == "chromium":
        rows = _read_chromium(source["cookies_path"], source["user_data_dir"], domain)
    elif source.get("kind") == "firefox":
        rows = _read_firefox(source["cookies_path"], domain)
    else:
        raise ValueError("unknown source kind")

    cookies = {}
    for r in rows:
        cookies[r["name"]] = r["value"]  # later rows win; name->value is what the bot wants
    cookie_text = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "cookies": cookies,
        "cookie_text": cookie_text,
        "sessionid": cookies.get("sessionid", ""),
    }


def grab_session(source_id: str) -> dict:
    """Locate a source by id and read its TikTok cookies."""
    for s in list_sources(enrich=False):
        if s["id"] == source_id:
            return read_source(s)
    raise ValueError(f"source not found: {source_id}")


if __name__ == "__main__":  # ponytail: manual check, run when browsers.py changes
    for s in list_sources():
        flag = "TikTok:%d" % s["has_tiktok"] if s["has_tiktok"] else "no-login"
        lock = " [LOCKED]" if s.get("locked") else ""
        print(f"{s['label']:42} {flag}{lock}")
