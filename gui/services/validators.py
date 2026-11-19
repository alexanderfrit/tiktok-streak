"""Input validation, normalization, CSV helpers, photo preparation, and cron math.

Zero Qt or Selenium dependencies.
"""
from __future__ import annotations

import base64
import csv
import io
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.]{1,24}$")
_PROFILE_URL_RE = re.compile(
    r"^(?:https?://)?(?:(?:www|m)\.)?tiktok\.com/@([A-Za-z0-9_.]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_VIDEO_OR_SHORT_URL_RE = re.compile(
    r"(?:/(?:video|v|photo)/|vt\.tiktok\.com|vm\.tiktok\.com|tiktok\.com/t/)",
    re.IGNORECASE,
)
_SHORT_HOST_RE = re.compile(
    r"^(?:https?://)?(?:vt\.tiktok\.com|vm\.tiktok\.com|(?:www\.|m\.)?tiktok\.com/t/)",
    re.IGNORECASE,
)
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TELEGRAM_TOKEN_RE = re.compile(r"^(\d{6,15}):([A-Za-z0-9_-]{20,60})$")
_TIME_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

_CSV_HEADERS = {"username", "handle", "friend", "friends", "user"}


def normalize_friends(raw: str) -> tuple[list[str], list[str]]:
    """Normalize raw friend handles/URLs into deduplicated handles and warnings.

    Accepts ``@name``, ``name``, or ``https://www.tiktok.com/@name`` separated by
    commas, semicolons, or newlines. Preserves first-seen order and warns on
    suspicious/skipped items (spaces/display names, video URLs, duplicates).
    """
    if not raw or not str(raw).strip():
        return [], []

    # Split on commas, semicolons, and newlines first so multi-word display names stay intact.
    raw_chunks = re.split(r"[\r\n,;]+", str(raw))
    tokens: list[str] = []
    for chunk in raw_chunks:
        c = chunk.strip()
        if not c:
            continue
        if any(ws in c for ws in (" ", "\t")):
            parts = c.split()
            if all(p.startswith("@") or p.lower().startswith(("http://", "https://", "tiktok.com/")) for p in parts):
                tokens.extend(parts)
            else:
                tokens.append(c)
        else:
            tokens.append(c)

    handles: list[str] = []
    seen_lower: set[str] = set()
    warnings: list[str] = []

    for tok in tokens:
        if any(ws in tok for ws in (" ", "\t")):
            warnings.append(
                f"Skipped '{tok}': contains spaces (enter TikTok @username, not display name)."
            )
            continue

        candidate = tok
        if "tiktok.com" in tok.lower() or tok.lower().startswith(("http://", "https://")):
            if _VIDEO_OR_SHORT_URL_RE.search(tok) and "/@" not in tok:
                warnings.append(f"Skipped '{tok}': looks like a video/short link, not a profile @username.")
                continue
            m = _PROFILE_URL_RE.match(tok)
            if not m:
                warnings.append(f"Skipped '{tok}': could not extract @username from URL.")
                continue
            if "/video/" in tok.lower() or "/photo/" in tok.lower():
                warnings.append(
                    f"Extracted @{m.group(1)} from video/photo URL '{tok}' — verify this is the intended friend."
                )
            candidate = m.group(1)
        else:
            candidate = candidate.lstrip("@").strip()

        if not candidate:
            continue

        if not _HANDLE_RE.match(candidate) or candidate in (".", ".."):
            warnings.append(
                f"Skipped '{tok}': invalid TikTok username (use 1-24 letters, digits, '_' or '.')."
            )
            continue

        low = candidate.lower()
        if low in seen_lower:
            warnings.append(f"Removed duplicate handle: @{candidate}")
            continue
        seen_lower.add(low)
        handles.append(candidate)

    return handles, warnings


def import_friends_csv(path: str | Path) -> list[str]:
    """Load and normalize unique TikTok handles from a CSV or line-delimited file."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Friends CSV not found: {file_path}")

    handles: list[str] = []
    seen: set[str] = set()
    with file_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if not row:
                continue
            cell = (row[0] or "").strip()
            if not cell:
                continue
            if row_idx == 0 and cell.lower().lstrip("@") in _CSV_HEADERS:
                continue
            parsed, _ = normalize_friends(cell)
            for h in parsed:
                low = h.lower()
                if low not in seen:
                    seen.add(low)
                    handles.append(h)
    return handles


def export_friends_csv(path: str | Path, friends: Iterable[str]) -> Path:
    """Write normalized unique friend handles to ``friends.csv`` atomically."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    raw = "\n".join(str(x) for x in friends if str(x).strip())
    normalized, _ = normalize_friends(raw)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=".friends_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Username"])
            for handle in normalized:
                writer.writerow([handle])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, file_path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
    return file_path


def parse_aweme_id(url: str) -> str | None:
    """Extract the numeric TikTok video/photo ID without importing selenium."""
    if not url:
        return None
    cleaned = str(url).strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return cleaned
    m = re.search(r"/(?:video|v|photo)/(\d{6,})", cleaned)
    if m:
        return m.group(1)
    m = re.search(r"(\d{15,})", cleaned)
    return m.group(1) if m else None


def _resolve_short_tiktok_url(url: str, timeout: float = 10.0) -> str:
    """Follow redirects on a vt.tiktok.com / vm.tiktok.com link."""
    target = url.strip()
    if not target.lower().startswith(("http://", "https://")):
        target = "https://" + target
    req = urllib.request.Request(
        target,
        headers={
            "User-Agent": _CHROME_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl() or target
        if parse_aweme_id(final_url):
            return final_url
        body = resp.read(65536).decode("utf-8", "ignore")
        m = re.search(
            r"https?://(?:www\.|m\.)?tiktok\.com/@[^/\"'\s]+/(?:video|photo)/\d{6,}",
            body,
        )
        if m:
            return m.group(0)
        return final_url


def validate_video_url(url: str, resolve_short: bool = False) -> dict:
    """Validate a TikTok video URL (optionally resolving vt/vm short links)."""
    raw = str(url or "").strip()
    if not raw:
        return {
            "valid": False,
            "ok": False,
            "aweme_id": None,
            "normalized_url": "",
            "is_short_link": False,
            "error": "Video URL is empty.",
        }

    is_short = bool(_SHORT_HOST_RE.match(raw))
    aweme_id = parse_aweme_id(raw)
    if aweme_id:
        return {
            "valid": True,
            "ok": True,
            "aweme_id": aweme_id,
            "normalized_url": raw,
            "is_short_link": is_short,
            "error": None,
        }

    if is_short:
        if not resolve_short:
            return {
                "valid": False,
                "ok": False,
                "aweme_id": None,
                "normalized_url": raw,
                "is_short_link": True,
                "error": "Short TikTok links (vt.tiktok.com / vm.tiktok.com) must be resolved to a full /video/<id> URL.",
            }
        try:
            resolved = _resolve_short_tiktok_url(raw)
        except Exception as exc:
            return {
                "valid": False,
                "ok": False,
                "aweme_id": None,
                "normalized_url": raw,
                "is_short_link": True,
                "error": f"Failed to resolve short TikTok link: {exc}",
            }
        resolved_id = parse_aweme_id(resolved)
        if resolved_id:
            clean_url = resolved.split("?", 1)[0] if "/video/" in resolved or "/photo/" in resolved else resolved
            return {
                "valid": True,
                "ok": True,
                "aweme_id": resolved_id,
                "normalized_url": clean_url,
                "is_short_link": True,
                "error": None,
            }
        return {
            "valid": False,
            "ok": False,
            "aweme_id": None,
            "normalized_url": resolved,
            "is_short_link": True,
            "error": "Could not extract a TikTok video ID from the resolved URL.",
        }

    return {
        "valid": False,
        "ok": False,
        "aweme_id": None,
        "normalized_url": raw,
        "is_short_link": False,
        "error": "Invalid TikTok video URL (expected tiktok.com/@user/video/<id> or numeric video ID).",
    }


def _flatten_to_rgb(img):
    """Apply EXIF transpose and composite alpha transparency onto white RGB."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        return bg.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def prepare_photo_secret(path: str | Path, max_bytes: int = 45000) -> str:
    """Convert an image into a compact JPEG data URL suitable for GitHub secrets."""
    from PIL import Image

    img_path = Path(path)
    if not img_path.is_file():
        raise FileNotFoundError(f"Photo file not found: {img_path}")
    if max_bytes < 512:
        raise ValueError("max_bytes must be at least 512 bytes")

    with Image.open(img_path) as raw_img:
        rgb = _flatten_to_rgb(raw_img)

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    if max(rgb.size) > 720:
        rgb.thumbnail((720, 720), resampling)

    qualities = (85, 76, 68, 58, 48, 38, 28)
    working = rgb
    for _scale_step in range(5):
        for quality in qualities:
            buf = io.BytesIO()
            working.save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"
            if len(data_url.encode("ascii")) <= max_bytes:
                return data_url

        new_w = max(96, int(working.width * 0.75))
        new_h = max(96, int(working.height * 0.75))
        if (new_w, new_h) == working.size:
            break
        working = working.resize((new_w, new_h), resampling)

    raise ValueError(f"Unable to compress photo under {max_bytes} bytes.")


def validate_repo_name(name: str) -> dict:
    """Validate a GitHub repository name (not full owner/name)."""
    cleaned = str(name or "").strip()
    if not cleaned:
        return {"valid": False, "ok": False, "name": "", "error": "Repository name is required."}
    if cleaned in (".", ".."):
        return {"valid": False, "ok": False, "name": cleaned, "error": "Repository name cannot be '.' or '..'."}
    if cleaned.lower().endswith(".git"):
        return {"valid": False, "ok": False, "name": cleaned, "error": "Repository name must not end with '.git'."}
    if not _REPO_NAME_RE.match(cleaned):
        return {
            "valid": False,
            "ok": False,
            "name": cleaned,
            "error": "Repository name may only contain letters, digits, '.', '_', and '-' (max 100 chars).",
        }
    return {"valid": True, "ok": True, "name": cleaned, "error": None}


def validate_telegram_token(token: str) -> dict:
    """Validate a BotFather Telegram bot token format."""
    cleaned = str(token or "").strip()
    if not cleaned:
        return {"valid": False, "ok": False, "token": "", "bot_id": None, "error": "Bot token is empty."}
    m = _TELEGRAM_TOKEN_RE.match(cleaned)
    if not m:
        return {
            "valid": False,
            "ok": False,
            "token": cleaned,
            "bot_id": None,
            "error": "Invalid Telegram bot token format (expected <digits>:<secret>).",
        }
    return {"valid": True, "ok": True, "token": cleaned, "bot_id": m.group(1), "error": None}


TIMEZONE_CHOICES: tuple[dict[str, Any], ...] = (
    {"id": "Asia/Jakarta", "short": "WIB", "label": "WIB — Jakarta / Western Indonesia (UTC+7)", "offset_minutes": 420},
    {"id": "Asia/Makassar", "short": "WITA", "label": "WITA — Makassar / Central Indonesia (UTC+8)", "offset_minutes": 480},
    {"id": "Asia/Jayapura", "short": "WIT", "label": "WIT — Jayapura / Eastern Indonesia (UTC+9)", "offset_minutes": 540},
    {"id": "Asia/Singapore", "short": "SGT", "label": "SGT — Singapore (UTC+8)", "offset_minutes": 480},
    {"id": "Asia/Kuala_Lumpur", "short": "MYT", "label": "MYT — Malaysia (UTC+8)", "offset_minutes": 480},
    {"id": "Asia/Bangkok", "short": "ICT", "label": "ICT — Bangkok / Indochina (UTC+7)", "offset_minutes": 420},
    {"id": "Asia/Manila", "short": "PHT", "label": "PHT — Manila (UTC+8)", "offset_minutes": 480},
    {"id": "Asia/Tokyo", "short": "JST", "label": "JST — Tokyo (UTC+9)", "offset_minutes": 540},
    {"id": "Asia/Seoul", "short": "KST", "label": "KST — Seoul (UTC+9)", "offset_minutes": 540},
    {"id": "Asia/Kolkata", "short": "IST", "label": "IST — India (UTC+5:30)", "offset_minutes": 330},
    {"id": "Asia/Dubai", "short": "GST", "label": "GST — Dubai (UTC+4)", "offset_minutes": 240},
    {"id": "UTC", "short": "UTC", "label": "UTC — Coordinated Universal Time (UTC+0)", "offset_minutes": 0},
    {"id": "Europe/London", "short": "GMT", "label": "GMT/BST — London (UTC+0)", "offset_minutes": 0},
    {"id": "Europe/Berlin", "short": "CET", "label": "CET — Central Europe (UTC+1)", "offset_minutes": 60},
    {"id": "America/New_York", "short": "ET", "label": "ET — US Eastern (UTC-5)", "offset_minutes": -300},
    {"id": "America/Chicago", "short": "CT", "label": "CT — US Central (UTC-6)", "offset_minutes": -360},
    {"id": "America/Denver", "short": "MT", "label": "MT — US Mountain (UTC-7)", "offset_minutes": -420},
    {"id": "America/Los_Angeles", "short": "PT", "label": "PT — US Pacific (UTC-8)", "offset_minutes": -480},
    {"id": "America/Sao_Paulo", "short": "BRT", "label": "BRT — Sao Paulo (UTC-3)", "offset_minutes": -180},
    {"id": "Australia/Sydney", "short": "AEST", "label": "AEST — Sydney (UTC+10)", "offset_minutes": 600},
    {"id": "Pacific/Auckland", "short": "NZST", "label": "NZST — Auckland (UTC+12)", "offset_minutes": 720},
)

_TIMEZONE_BY_ID: dict[str, dict[str, Any]] = {
    tz["id"].lower(): tz for tz in TIMEZONE_CHOICES
}
_DEFAULT_TIMEZONE_INFO: dict[str, Any] = _TIMEZONE_BY_ID["asia/jakarta"]


def get_timezone_info(tz_id: str | None) -> dict[str, Any]:
    """Look up a timezone preset by ID (case-insensitively), defaulting to Asia/Jakarta."""
    key = str(tz_id or "").strip().lower()
    entry = _TIMEZONE_BY_ID.get(key, _DEFAULT_TIMEZONE_INFO)
    return dict(entry)


def detect_local_timezone() -> str:
    """Detect the closest timezone preset ID from the host system UTC offset."""
    try:
        offset = datetime.now().astimezone().utcoffset()
        if offset is None:
            return "Asia/Jakarta"
        offset_minutes = int(round(offset.total_seconds() / 60))
    except Exception:
        return "Asia/Jakarta"

    for tz in TIMEZONE_CHOICES:
        if tz["offset_minutes"] == offset_minutes:
            return str(tz["id"])
    return "Asia/Jakarta"


def _parse_hhmm(value: str) -> tuple[int, int]:
    m = _TIME_HHMM_RE.match(str(value or "").strip())
    if not m:
        raise ValueError(f"Invalid HH:MM time: {value!r} (expected 00:00-23:59)")
    return int(m.group(1)), int(m.group(2))


def _split_time_items(times_hhmm: Sequence[str] | str) -> list[str]:
    if isinstance(times_hhmm, str):
        return [x.strip() for x in re.split(r"[\r\n,;]+", times_hhmm) if x.strip()]
    return [str(x).strip() for x in times_hhmm if str(x).strip()]


def _split_cron_items(crons: Sequence[str] | str) -> list[str]:
    if isinstance(crons, str):
        raw_lines = crons.splitlines()
    else:
        raw_lines = []
        for item in crons:
            raw_lines.extend(str(item or "").splitlines())
    return [" ".join(line.strip().split()) for line in raw_lines if line.strip()]


def _parse_simple_daily_cron(cron_line: str) -> tuple[int, list[int]] | None:
    parts = " ".join(str(cron_line or "").strip().split()).split(" ")
    if len(parts) != 5:
        return None
    minute_s, hour_s, dom, mon, dow = parts
    if dom != "*" or mon != "*" or dow != "*" or not minute_s.isdigit():
        return None
    minute = int(minute_s)
    if not (0 <= minute <= 59):
        return None
    hour_tokens = [h.strip() for h in hour_s.split(",") if h.strip()]
    if not hour_tokens or not all(h.isdigit() and 0 <= int(h) <= 23 for h in hour_tokens):
        return None
    return minute, [int(h) for h in hour_tokens]


def times_to_utc_crons(times_hhmm: Sequence[str] | str, tz_id: str = "Asia/Jakarta") -> list[str]:
    """Convert daily HH:MM times in ``tz_id`` into sorted 5-field UTC cron strings.

    Times that map to the same UTC minute are grouped into a single
    ``"{utc_m} {hours_csv} * * *"`` entry; mixed minutes emit multiple cron lines.
    """
    items = _split_time_items(times_hhmm)
    if not items:
        raise ValueError("At least one daily time (HH:MM) is required.")

    offset_minutes = int(get_timezone_info(tz_id)["offset_minutes"])
    hours_by_minute: dict[int, set[int]] = {}
    for item in items:
        hh, mm = _parse_hhmm(item)
        total_min = (hh * 60 + mm - offset_minutes) % (24 * 60)
        utc_h, utc_m = divmod(total_min, 60)
        hours_by_minute.setdefault(utc_m, set()).add(utc_h)

    crons: list[str] = []
    for utc_m in sorted(hours_by_minute):
        hours_csv = ",".join(str(h) for h in sorted(hours_by_minute[utc_m]))
        crons.append(f"{utc_m} {hours_csv} * * *")
    return crons


def crons_to_local_times(crons: Sequence[str] | str, tz_id: str = "Asia/Jakarta") -> list[str]:
    """Parse standard daily ``M H * * *`` UTC cron entries back into sorted local ``HH:MM`` strings."""
    lines = _split_cron_items(crons)
    if not lines:
        return []

    offset_minutes = int(get_timezone_info(tz_id)["offset_minutes"])
    local_pairs: set[tuple[int, int]] = set()
    for line in lines:
        parsed = _parse_simple_daily_cron(line)
        if parsed is None:
            continue
        minute, hours = parsed
        for utc_h in hours:
            total_min = (utc_h * 60 + minute + offset_minutes) % (24 * 60)
            lh, lm = divmod(total_min, 60)
            local_pairs.add((lh, lm))

    return [f"{lh:02d}:{lm:02d}" for lh, lm in sorted(local_pairs)]


def format_schedule_summary(
    crons: Sequence[str] | str,
    tz_id: str = "Asia/Jakarta",
    lang: str = "en",
) -> str:
    """Format one or more UTC cron expressions into a human-readable schedule summary."""
    lines = _split_cron_items(crons)
    is_id = str(lang or "en").lower().startswith("id")
    if not lines:
        return "Jadwal tidak valid" if is_id else "Invalid schedule"

    tz_info = get_timezone_info(tz_id)
    offset_minutes = int(tz_info["offset_minutes"])
    short = str(tz_info["short"])

    parsed_entries = [_parse_simple_daily_cron(line) for line in lines]
    if all(entry is not None for entry in parsed_entries):
        utc_pairs: set[tuple[int, int]] = set()
        local_pairs: set[tuple[int, int]] = set()
        for entry in parsed_entries:
            assert entry is not None
            minute, hours = entry
            for utc_h in hours:
                utc_pairs.add((utc_h, minute))
                total_min = (utc_h * 60 + minute + offset_minutes) % (24 * 60)
                lh, lm = divmod(total_min, 60)
                local_pairs.add((lh, lm))

        local_str = ", ".join(f"{lh:02d}:{lm:02d}" for lh, lm in sorted(local_pairs))
        utc_str = ", ".join(f"{uh:02d}:{um:02d}" for uh, um in sorted(utc_pairs))
        prefix = "Setiap hari pukul" if is_id else "Daily at"
        if short == "UTC":
            return f"{prefix} {local_str} UTC"
        return f"{prefix} {local_str} {short} ({utc_str} UTC)"

    return f"Cron UTC: {', '.join(lines)}"


def format_utc_timestamp(iso_str: str, tz_id: str = "Asia/Jakarta") -> str:
    """Format an ISO-8601 UTC timestamp string into the user's selected timezone."""
    cleaned = str(iso_str or "").strip()
    if not cleaned:
        return ""
    norm = cleaned[:-1] + "+00:00" if cleaned.upper().endswith("Z") else cleaned
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError:
        return cleaned

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    tz_info = get_timezone_info(tz_id)
    target_tz = timezone(timedelta(minutes=int(tz_info["offset_minutes"])))
    local_dt = dt.astimezone(target_tz)
    return f"{local_dt:%Y-%m-%d %H:%M} {tz_info['short']}"


def times_to_utc_cron(times_hhmm: Sequence[str] | str) -> str:
    """Convert daily WIB (UTC+7) HH:MM times into a 5-field UTC cron expression."""
    if isinstance(times_hhmm, str):
        items = [x.strip() for x in times_hhmm.split(",") if x.strip()]
    else:
        items = [str(x).strip() for x in times_hhmm if str(x).strip()]

    if not items:
        raise ValueError("At least one daily time (HH:MM WIB) is required.")

    utc_pairs: set[tuple[int, int]] = set()
    for item in items:
        hh, mm = _parse_hhmm(item)
        total_min = (hh * 60 + mm - 7 * 60) % (24 * 60)
        utc_h, utc_m = divmod(total_min, 60)
        utc_pairs.add((utc_h, utc_m))

    minutes = {m for _h, m in utc_pairs}
    if len(minutes) > 1:
        raise ValueError(
            "All daily cloud times must share the same minute (e.g. 08:00 and 20:00) "
            "to form a single GitHub Actions cron expression."
        )

    utc_m = next(iter(minutes))
    hours = sorted(h for h, _m in utc_pairs)
    hours_str = ",".join(str(h) for h in hours)
    return f"{utc_m} {hours_str} * * *"


def cron_to_wib_summary(cron: str, lang: str = "en") -> str:
    """Format a 5-field UTC cron string as a human-readable WIB (UTC+7) summary."""
    cleaned = " ".join(str(cron or "").strip().split())
    parts = cleaned.split(" ")
    is_id = str(lang or "en").lower().startswith("id")

    if len(parts) != 5:
        return cleaned or ("Jadwal tidak valid" if is_id else "Invalid schedule")

    minute_s, hour_s, dom, mon, dow = parts
    if dom == "*" and mon == "*" and dow == "*" and minute_s.isdigit():
        minute = int(minute_s)
        hour_tokens = [h.strip() for h in hour_s.split(",") if h.strip()]
        if 0 <= minute <= 59 and hour_tokens and all(h.isdigit() and 0 <= int(h) <= 23 for h in hour_tokens):
            wib_times = []
            for h_tok in hour_tokens:
                total_min = (int(h_tok) * 60 + minute + 7 * 60) % (24 * 60)
                wh, wm = divmod(total_min, 60)
                wib_times.append((wh, wm))
            wib_times = sorted(set(wib_times))
            formatted = ", ".join(f"{wh:02d}:{wm:02d}" for wh, wm in wib_times)
            if is_id:
                return f"Setiap hari pukul {formatted} WIB"
            return f"Daily at {formatted} WIB"

    return f"Cron UTC: {cleaned}" if not is_id else f"Cron UTC: {cleaned}"


__all__ = [
    "TIMEZONE_CHOICES",
    "cron_to_wib_summary",
    "crons_to_local_times",
    "detect_local_timezone",
    "export_friends_csv",
    "format_schedule_summary",
    "format_utc_timestamp",
    "get_timezone_info",
    "import_friends_csv",
    "normalize_friends",
    "parse_aweme_id",
    "prepare_photo_secret",
    "times_to_utc_cron",
    "times_to_utc_crons",
    "validate_repo_name",
    "validate_telegram_token",
    "validate_video_url",
]
