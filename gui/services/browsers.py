"""Read TikTok cookies from installed Windows browsers (Chromium & Firefox families).

Fixes all legacy extraction issues:
- UTF-8 BOM safe `Local State` reading (`utf-8-sig`)
- Validates AES-GCM `v10`/`v11` ciphertext length (>= 31 bytes)
- Skips empty blobs before DPAPI
- Explicitly detects App-Bound Encryption (`v20`) in both `list_sources` and `read_source`
- Copies `-wal` and `-shm` sidecar files in temp-copy fallback without `immutable=1`
- Wraps `con.execute()` under `BrowserLocked`
- Closes `profiles.ini` safely with `with`
- Disambiguates profile IDs across roots and root-level cookie stores
- Filters exact/subdomain `.tiktok.com` hosts and orders by expiry (freshest unexpired wins)
"""
from __future__ import annotations

import base64
import glob
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any

logger = logging.getLogger("tiktok-streak")

TIKTOK_DOMAIN = "tiktok.com"
_WEBKIT_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01
_AUTH_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_tt")


class BrowserLocked(RuntimeError):
    """The browser is running and holds its cookie DB with an exclusive lock."""


class V20EncryptedError(RuntimeError):
    """Cookies are protected by Chrome/Edge App-Bound Encryption (v20)."""


def _is_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        tok in msg
        for tok in (
            "locked",
            "unable to open",
            "permission",
            "sharing violation",
            "being used by another process",
        )
    )


def _connect(path: str) -> sqlite3.Connection:
    """Open a DB read-only without `immutable=1` so WAL/SHM journals remain visible."""
    uri = "file:" + path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as e:
        if _is_lock_error(e):
            raise BrowserLocked(f"{os.path.basename(path)} is locked (browser open)") from e
        raise


def _query(path: str, sql: str, params: tuple = ()) -> list:
    """Run a query via read-only URI; fall back to copying DB + `-wal`/`-shm` sidecars."""
    try:
        con = _connect(path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except BrowserLocked:
        pass
    except sqlite3.OperationalError as e:
        if not _is_lock_error(e) and "no such column" in str(e).lower():
            raise

    tmp_dir = tempfile.mkdtemp(prefix="tt_cookies_")
    try:
        base_name = os.path.basename(path)
        dst_db = os.path.join(tmp_dir, base_name)
        try:
            shutil.copy2(path, dst_db)
            for sidecar in ("-wal", "-shm", "-journal"):
                src_sidecar = path + sidecar
                if os.path.exists(src_sidecar):
                    shutil.copy2(src_sidecar, dst_db + sidecar)
        except (PermissionError, OSError) as e:
            raise BrowserLocked(f"{os.path.basename(path)} is locked (browser open): {e}") from e

        try:
            con = sqlite3.connect(dst_db, timeout=5)
            try:
                return con.execute(sql, params).fetchall()
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            if _is_lock_error(e):
                raise BrowserLocked(f"{os.path.basename(path)} is locked (browser open): {e}") from e
            raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _host_filter_clause(host_col: str, domain: str) -> tuple[str, tuple[str, str]]:
    clean_domain = domain.lstrip(".").lower()
    return f"({host_col} = ? OR {host_col} LIKE ?)", (clean_domain, f"%.{clean_domain}")


def _sort_key_webkit(expires_utc: int | None, now_webkit: int) -> tuple[int, int]:
    exp = int(expires_utc or 0)
    if exp == 0:
        return (1, 0)
    if exp >= now_webkit:
        return (2, exp)
    return (0, exp)


def _sort_key_unix(expiry: int | None, now_unix: int) -> tuple[int, int]:
    exp = int(expiry or 0)
    if exp == 0:
        return (1, 0)
    threshold = now_unix * 1000 if exp > 10**12 else now_unix
    if exp >= threshold:
        return (2, exp)
    return (0, exp)


def _local() -> str:
    return os.environ.get("LOCALAPPDATA", "").strip()


def _roam() -> str:
    return os.environ.get("APPDATA", "").strip()


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


def _chromium_roots() -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    out: list[dict[str, str]] = []
    for bid, label, which, rel in _CHROMIUM_SPECS:
        base = _local() if which == "local" else _roam()
        if not base:
            continue
        path = os.path.join(base, *rel.split("/"))
        if os.path.isdir(path):
            out.append({"id": bid, "label": label, "user_data_dir": path})
    return out


_CHROMIUM_INTERNAL_DIRS = {
    "guest profile",
    "system profile",
    "snapshots",
    "shadercache",
    "crashpad",
    "grshadercache",
    "graphitedawncache",
}


def _chromium_profile_names(user_data_dir: str) -> dict[str, str]:
    """Return `{folder_name: friendly_name}` from Chromium `Local State` (`profile.info_cache`)."""
    state = os.path.join(user_data_dir, "Local State")
    if not os.path.isfile(state):
        return {}
    try:
        with open(state, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return {}
    info_cache = (data.get("profile") or {}).get("info_cache")
    if not isinstance(info_cache, dict):
        return {}
    out: dict[str, str] = {}
    for folder, info in info_cache.items():
        if not isinstance(info, dict):
            continue
        friendly = str(
            info.get("name")
            or info.get("gaia_name")
            or info.get("shortcut_name")
            or ""
        ).strip()
        if friendly:
            out[str(folder)] = friendly
    return out


def _chromium_profiles(user_data_dir: str) -> list[dict[str, str]]:
    """Return distinct Chromium cookie store profiles under `user_data_dir`."""
    candidates: list[tuple[str, str]] = []
    if os.path.isdir(user_data_dir):
        for name in sorted(os.listdir(user_data_dir)):
            base = os.path.join(user_data_dir, name)
            if os.path.isdir(base):
                candidates.append((name, base))
    # Root-level cookie store (e.g. Opera Stable) uses "Root" if a Default dir also exists.
    has_default_dir = any(name.lower() == "default" for name, _ in candidates)
    root_label = "Root" if has_default_dir else "Default"
    candidates.append((root_label, user_data_dir))

    name_map = _chromium_profile_names(user_data_dir)
    name_map_lower = {k.lower(): v for k, v in name_map.items()}
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    profiles: list[dict[str, str]] = []

    for name, base in candidates:
        is_internal = base != user_data_dir and name.lower() in _CHROMIUM_INTERNAL_DIRS
        for cand in (os.path.join(base, "Network", "Cookies"), os.path.join(base, "Cookies")):
            norm_path = os.path.normcase(os.path.abspath(cand))
            if os.path.exists(cand) and norm_path not in seen_paths:
                seen_paths.add(norm_path)
                if is_internal:
                    try:
                        count, _v20 = _inspect_chromium_presence(cand)
                    except Exception:
                        count = 0
                    if count <= 0:
                        break
                prof_name = name
                if prof_name in seen_names:
                    suffix = hashlib.sha1(norm_path.encode("utf-8")).hexdigest()[:6]
                    prof_name = f"{name}-{suffix}"
                seen_names.add(prof_name)
                display_name = name_map.get(name) or name_map_lower.get(name.lower()) or prof_name
                profiles.append({
                    "profile": prof_name,
                    "display_name": display_name,
                    "cookies": cand,
                })
                break
    return profiles


def _dpapi_unprotect(blob: bytes) -> bytes:
    if not blob:
        return b""
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _chromium_key(user_data_dir: str) -> bytes:
    state = os.path.join(user_data_dir, "Local State")
    with open(state, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    enc_b64 = data.get("os_crypt", {}).get("encrypted_key")
    if not enc_b64:
        raise ValueError(f"Missing os_crypt.encrypted_key in {state}")
    enc = base64.b64decode(enc_b64)
    if enc[:5] == b"DPAPI":
        enc = enc[5:]
    if not enc:
        raise ValueError(f"Empty DPAPI key in {state}")
    return _dpapi_unprotect(enc)


def _decrypt_chromium(value: bytes, key: bytes) -> str:
    if not value:
        return ""
    prefix = value[:3]
    if prefix in (b"v10", b"v11"):
        # 3-byte version prefix + 12-byte nonce + ciphertext + 16-byte GCM tag.
        if len(value) < 31:
            raise ValueError(f"Malformed {prefix.decode('ascii', 'ignore')} cookie blob (len={len(value)} < 31)")
        from Crypto.Cipher import AES
        nonce, rest = value[3:15], value[15:]
        ct, tag = rest[:-16], rest[-16:]
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag).decode("utf-8")
    if prefix == b"v20":
        raise V20EncryptedError(
            "App-Bound Encryption (v20) cookie cannot be decrypted externally; "
            "use Guided Browser Login or manual paste."
        )
    return _dpapi_unprotect(value).decode("utf-8", "ignore")


def _query_chromium_rows(path: str, domain: str) -> list[tuple[str, str, str, bytes, int]]:
    where_sql, params = _host_filter_clause("host_key", domain)
    try:
        rows = _query(
            path,
            f"SELECT host_key, name, value, encrypted_value, expires_utc "
            f"FROM cookies WHERE {where_sql} ORDER BY expires_utc ASC",
            params,
        )
        now_webkit = int((time.time() + _WEBKIT_EPOCH_OFFSET) * 1_000_000)
        ordered = sorted(
            rows,
            key=lambda r: _sort_key_webkit(r[4] if len(r) > 4 else 0, now_webkit),
        )
        return [(r[0], r[1], r[2], bytes(r[3] or b""), int(r[4] or 0)) for r in ordered]
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e).lower():
            raise
        # Minimal fallback if schema lacks expires_utc
        rows = _query(
            path,
            f"SELECT host_key, name, value, encrypted_value FROM cookies WHERE {where_sql}",
            params,
        )
        return [(r[0], r[1], r[2], bytes(r[3] or b""), 0) for r in rows]


def _inspect_chromium_presence(path: str, domain: str = TIKTOK_DOMAIN) -> tuple[int, bool]:
    """Return `(row_count, v20_blocked)` without needing Local State decryption."""
    rows = _query_chromium_rows(path, domain)
    if not rows:
        return 0, False

    latest_state: dict[str, str] = {}
    for _host, name, value, enc, _exp in rows:
        if value:
            latest_state[name] = "ok"
        elif enc:
            latest_state[name] = "v20" if enc[:3] == b"v20" else "ok"

    auth_v20 = any(latest_state.get(k) == "v20" for k in _AUTH_COOKIE_NAMES)
    auth_ok = any(latest_state.get(k) == "ok" for k in _AUTH_COOKIE_NAMES)
    any_v20 = any(v == "v20" for v in latest_state.values())
    v20_blocked = bool(auth_v20 or (any_v20 and not auth_ok))
    return len(rows), v20_blocked


def _read_chromium(path: str, user_data_dir: str, domain: str) -> list[dict[str, str]]:
    rows = _query_chromium_rows(path, domain)
    if not rows:
        return []

    key: bytes | None = None
    key_err: Exception | None = None
    needs_key = any(not value and enc and enc[:3] in (b"v10", b"v11") for _h, _n, value, enc, _e in rows)
    if needs_key:
        try:
            key = _chromium_key(user_data_dir)
        except Exception as exc:
            key_err = exc

    out: list[dict[str, str]] = []
    v20_cookies: set[str] = set()
    resolved_cookies: set[str] = set()

    for _host, name, value, enc, _exp in rows:
        if value:
            out.append({"name": name, "value": value})
            resolved_cookies.add(name)
            v20_cookies.discard(name)
            continue

        if not enc:
            # Skip empty blobs before DPAPI.
            continue

        if enc[:3] == b"v20":
            v20_cookies.add(name)
            continue

        if enc[:3] in (b"v10", b"v11"):
            if key is None:
                logger.debug("skip %s: missing Chromium key (%s)", name, key_err)
                continue
        try:
            dec = _decrypt_chromium(enc, key or b"")
        except V20EncryptedError:
            v20_cookies.add(name)
            continue
        except Exception as exc:
            logger.debug("skip cookie %s: %s", name, exc)
            continue

        if dec:
            out.append({"name": name, "value": dec})
            resolved_cookies.add(name)
            v20_cookies.discard(name)

    if any(k in v20_cookies for k in _AUTH_COOKIE_NAMES) or (v20_cookies and not resolved_cookies):
        raise V20EncryptedError(
            "Chrome/Edge uses App-Bound Encryption (v20) for TikTok cookies; "
            "use 1-Click Guided Browser Login or paste the cookie manually."
        )

    return out


_FIREFOX_LABELS = {
    "firefox": "Firefox",
    "zen": "Zen",
    "waterfox": "Waterfox",
    "librewolf": "LibreWolf",
    "floorp": "Floorp",
    "palemoon": "Pale Moon",
    "basilisk": "Basilisk",
    "seamonkey": "SeaMonkey",
    "mullvad": "Mullvad Browser",
    "tor browser": "Tor Browser",
    "mercury": "Mercury",
    "ghostery": "Ghostery",
}


def _firefox_family_roots() -> list[str]:
    if sys.platform != "win32":
        return []
    seen: set[str] = set()
    roots: list[str] = []

    def consider(path: str) -> None:
        if not path:
            return
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen or not os.path.isdir(path):
            return
        has_ini = os.path.exists(os.path.join(path, "profiles.ini"))
        has_profiles = os.path.isdir(os.path.join(path, "Profiles"))
        if has_ini or has_profiles:
            seen.add(norm)
            roots.append(path)

    for base in (_roam(), _local()):
        if not base or not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            consider(os.path.join(base, name))
        mozilla = os.path.join(base, "Mozilla")
        if os.path.isdir(mozilla):
            for name in sorted(os.listdir(mozilla)):
                consider(os.path.join(mozilla, name))
    return roots


def _parse_profiles_ini(root: str) -> list[dict[str, str]]:
    ini = os.path.join(root, "profiles.ini")
    if not os.path.exists(ini):
        return []
    blocks: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    with open(ini, encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("["):
                if cur:
                    blocks.append(cur)
                cur = {}
            elif "=" in stripped:
                k, v = stripped.split("=", 1)
                cur[k.strip().lower()] = v.strip()
    if cur:
        blocks.append(cur)

    out: list[dict[str, str]] = []
    for b in blocks:
        p = b.get("path")
        if not p:
            continue
        base = os.path.normpath(os.path.join(root, p) if b.get("isrelative") == "1" else p)
        display_name = str(b.get("name") or "").strip()
        out.append({"path": base, "name": display_name})
    return out


def _firefox_profiles(root: str) -> list[dict[str, str]]:
    entries: list[tuple[str, str]] = [
        (entry["path"], entry.get("name", "")) for entry in _parse_profiles_ini(root)
    ]
    for path in sorted(glob.glob(os.path.join(root, "Profiles", "*"))):
        entries.append((path, ""))
    entries.append((root, ""))

    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    profiles: list[dict[str, str]] = []

    for base, ini_name in entries:
        ck = os.path.join(base, "cookies.sqlite")
        norm_ck = os.path.normcase(os.path.abspath(ck))
        if os.path.exists(ck) and norm_ck not in seen_paths:
            seen_paths.add(norm_ck)
            prof_name = os.path.basename(base.rstrip("/\\")) or "Default"
            if prof_name in seen_names:
                suffix = hashlib.sha1(norm_ck.encode("utf-8")).hexdigest()[:6]
                prof_name = f"{prof_name}-{suffix}"
            seen_names.add(prof_name)
            display_name = ini_name.strip() or prof_name
            profiles.append({
                "profile": prof_name,
                "display_name": display_name,
                "cookies": ck,
            })
    return profiles


def _firefox_label(root: str) -> str:
    name = os.path.basename(root.rstrip("/\\")).lower()
    return _FIREFOX_LABELS.get(name, name.title())


def _inspect_firefox_presence(path: str, domain: str = TIKTOK_DOMAIN) -> int:
    where_sql, params = _host_filter_clause("host", domain)
    rows = _query(path, f"SELECT COUNT(*) FROM moz_cookies WHERE {where_sql}", params)
    return int(rows[0][0]) if rows else 0


def _read_firefox(path: str, domain: str) -> list[dict[str, str]]:
    where_sql, params = _host_filter_clause("host", domain)
    try:
        rows = _query(
            path,
            f"SELECT host, name, value, expiry FROM moz_cookies WHERE {where_sql} ORDER BY expiry ASC",
            params,
        )
        now_unix = int(time.time())
        ordered = sorted(
            rows,
            key=lambda r: _sort_key_unix(r[3] if len(r) > 3 else 0, now_unix),
        )
        return [{"name": name, "value": value} for _host, name, value, _exp in ordered if value]
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e).lower():
            raise
        rows = _query(
            path,
            f"SELECT host, name, value FROM moz_cookies WHERE {where_sql}",
            params,
        )
        return [{"name": name, "value": value} for _host, name, value in rows if value]


def list_sources(enrich: bool = True) -> list[dict[str, Any]]:
    """List installed Chromium & Firefox-family browser profiles on Windows."""
    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    def unique_id(base_id: str, cookies_path: str) -> str:
        if base_id not in used_ids:
            used_ids.add(base_id)
            return base_id
        suffix = hashlib.sha1(os.path.normcase(os.path.abspath(cookies_path)).encode("utf-8")).hexdigest()[:6]
        candidate = f"{base_id}::{suffix}"
        used_ids.add(candidate)
        return candidate

    for b in _chromium_roots():
        for p in _chromium_profiles(b["user_data_dir"]):
            sid = unique_id(f"chromium::{b['id']}::{p['profile']}", p["cookies"])
            display_name = str(p.get("display_name") or p["profile"]).strip() or p["profile"]
            if display_name == p["profile"]:
                full_label = f"{b['label']} — {p['profile']}"
            else:
                full_label = f"{b['label']} — {display_name} ({p['profile']})"
            sources.append({
                "id": sid,
                "label": full_label,
                "browser_label": b["label"],
                "kind": "chromium",
                "user_data_dir": b["user_data_dir"],
                "profile": p["profile"],
                "display_name": display_name,
                "cookies_path": p["cookies"],
            })

    for root in _firefox_family_roots():
        label = _firefox_label(root)
        root_key = os.path.basename(root.rstrip("/\\"))
        for p in _firefox_profiles(root):
            sid = unique_id(f"firefox::{root_key}::{p['profile']}", p["cookies"])
            display_name = str(p.get("display_name") or p["profile"]).strip() or p["profile"]
            sources.append({
                "id": sid,
                "label": f"{label} — {display_name}",
                "browser_label": label,
                "kind": "firefox",
                "profile": p["profile"],
                "display_name": display_name,
                "cookies_path": p["cookies"],
            })

    if enrich:
        for s in sources:
            try:
                if s["kind"] == "chromium":
                    count, v20_blocked = _inspect_chromium_presence(s["cookies_path"])
                    s["has_tiktok"] = count
                    s["v20_blocked"] = v20_blocked
                else:
                    s["has_tiktok"] = _inspect_firefox_presence(s["cookies_path"])
                    s["v20_blocked"] = False
                s["locked"] = False
            except BrowserLocked:
                s["has_tiktok"] = 0
                s["locked"] = True
                s["v20_blocked"] = False
            except Exception as exc:
                logger.debug("browser inspect error (%s): %s", s.get("id"), exc)
                s["has_tiktok"] = 0
                s["locked"] = False
                s["v20_blocked"] = False

        sources.sort(
            key=lambda s: (
                not s.get("has_tiktok"),
                bool(s.get("v20_blocked")),
                bool(s.get("locked")),
                s["label"],
            )
        )

    return sources


def read_source(source: dict[str, Any], domain: str = TIKTOK_DOMAIN) -> dict[str, Any]:
    """Read TikTok cookies from a source descriptor dict."""
    kind = source.get("kind")
    if kind == "chromium":
        rows = _read_chromium(source["cookies_path"], source["user_data_dir"], domain)
    elif kind == "firefox":
        rows = _read_firefox(source["cookies_path"], domain)
    else:
        raise ValueError(f"Unknown browser source kind: {kind!r}")

    cookies: dict[str, str] = {}
    for r in rows:
        name = str(r.get("name") or "").strip()
        val = str(r.get("value") or "")
        if name and val:
            cookies[name] = val

    cookie_text = "; ".join(f"{k}={v}" for k, v in cookies.items())
    sessionid = (
        cookies.get("sessionid")
        or cookies.get("sessionid_ss")
        or cookies.get("sid_tt")
        or ""
    )
    return {
        "cookies": cookies,
        "cookie_text": cookie_text,
        "sessionid": sessionid,
    }


def grab_session(source_id: str, domain: str = TIKTOK_DOMAIN) -> dict[str, Any]:
    """Locate a browser source by ID and extract its TikTok cookies."""
    for s in list_sources(enrich=False):
        if s["id"] == source_id:
            return read_source(s, domain=domain)
    raise ValueError(f"Browser source not found: {source_id}")


__all__ = [
    "BrowserLocked",
    "TIKTOK_DOMAIN",
    "V20EncryptedError",
    "grab_session",
    "list_sources",
    "read_source",
]
