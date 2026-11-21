"""Self-update checker, verified installer downloader, and Windows EXE self-updater (zero Qt)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from gui.version import APP_VERSION, EXE_ASSET_NAME, UPDATE_REPO

logger = logging.getLogger("tiktok-streak")

CHECK_INTERVAL_SECONDS = 12 * 3600
_ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
})
_UNSAFE_CMD_CHARS_RE = re.compile(r'[\r\n&|<>^"%!;]')


def parse_version(raw: str) -> tuple[int, int, int]:
    """Parse ``v1.2.3`` or ``1.2.3-beta`` into a 3-int tuple."""
    cleaned = str(raw or "").strip().lstrip("vV").strip()
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", cleaned)
    if not m:
        return (0, 0, 0)
    return (
        int(m.group(1) or 0),
        int(m.group(2) or 0),
        int(m.group(3) or 0),
    )


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    """Return True strictly when `latest` is semver-greater than `current`."""
    return parse_version(latest) > parse_version(current)


def _is_allowed_https_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host in _ALLOWED_DOWNLOAD_HOSTS:
        return True
    return host.endswith(".githubusercontent.com") or host.endswith(".github.com")


def _extract_sha256_digest(asset: dict[str, Any]) -> str:
    raw = str(asset.get("digest") or "").strip().lower()
    if raw.startswith("sha256:"):
        cand = raw.split(":", 1)[1].strip()
        if re.fullmatch(r"[0-9a-f]{64}", cand):
            return cand
    elif re.fullmatch(r"[0-9a-f]{64}", raw):
        return raw
    return ""


def check_for_update(
    current_version: str = APP_VERSION,
    repo: str = UPDATE_REPO,
    asset_name: str = EXE_ASSET_NAME,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Query GitHub latest release cleanly (no token required, fails safely on offline/404)."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"tiktok-streak-gui/{current_version}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "current_version": str(current_version).lstrip("vV"),
            "has_update": False,
            "status_code": exc.code,
            "error": f"GitHub HTTP {exc.code}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "current_version": str(current_version).lstrip("vV"),
            "has_update": False,
            "error": str(exc),
        }

    if not isinstance(data, dict):
        return {
            "ok": False,
            "current_version": str(current_version).lstrip("vV"),
            "has_update": False,
            "error": "Unexpected GitHub release payload.",
        }

    tag_name = str(data.get("tag_name") or "").strip()
    latest_ver = tag_name.lstrip("vV")
    html_url = str(data.get("html_url") or f"https://github.com/{repo}/releases/latest").strip()
    notes = str(data.get("body") or "").strip()
    published_at = str(data.get("published_at") or "").strip()

    chosen_asset: dict[str, Any] = {}
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        aname = str(asset.get("name") or "")
        if aname == asset_name or (not chosen_asset and aname.lower().endswith(".exe")):
            chosen_asset = asset
            if aname == asset_name:
                break

    download_url = str(chosen_asset.get("browser_download_url") or "").strip()
    if download_url and not _is_allowed_https_url(download_url):
        download_url = ""

    return {
        "ok": True,
        "current_version": str(current_version).lstrip("vV"),
        "latest_version": latest_ver,
        "tag_name": tag_name or f"v{latest_ver}",
        "has_update": bool(latest_ver and is_newer_version(latest_ver, current_version)),
        "download_url": download_url,
        "asset_name": str(chosen_asset.get("name") or asset_name),
        "asset_size": int(chosen_asset.get("size") or 0),
        "asset_sha256": _extract_sha256_digest(chosen_asset),
        "html_url": html_url if _is_allowed_https_url(html_url) else f"https://github.com/{repo}/releases/latest",
        "release_notes": notes,
        "published_at": published_at,
        "prerelease": bool(data.get("prerelease", False)),
    }


def download_update_asset(
    url: str,
    dest_path: str | Path,
    expected_sha256: str = "",
    expected_size: int = 0,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: float = 60.0,
) -> Path:
    """Stream-download update asset into `dest_path`, verifying PE header and optional SHA-256."""
    target_url = str(url or "").strip()
    if not _is_allowed_https_url(target_url):
        raise ValueError(f"Untrusted update download URL: {target_url!r}")

    out_path = Path(dest_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    if part_path.exists():
        part_path.unlink(missing_ok=True)

    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": f"tiktok-streak-gui/{APP_VERSION}"},
        method="GET",
    )
    hasher = hashlib.sha256()
    downloaded = 0

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = str(getattr(resp, "url", target_url) or target_url)
            if not _is_allowed_https_url(final_url):
                raise ValueError(f"Redirected to untrusted host: {final_url!r}")

            hdr_len = 0
            try:
                hdr_len = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                hdr_len = 0
            total = hdr_len or max(0, int(expected_size or 0))

            with open(part_path, "wb") as fh:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("Update download cancelled.")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass

        if downloaded < 1024 and not expected_size:
            raise ValueError(f"Downloaded file is unexpectedly small ({downloaded} bytes).")
        if expected_size > 0 and downloaded != int(expected_size):
            raise ValueError(
                f"Downloaded size mismatch: expected {expected_size} bytes, got {downloaded} bytes."
            )

        with open(part_path, "rb") as chk_f:
            magic = chk_f.read(2)
        if magic != b"MZ":
            raise ValueError("Downloaded update file is not a valid Windows executable (missing MZ header).")

        clean_expected = str(expected_sha256 or "").strip().lower()
        if clean_expected:
            actual_sha = hasher.hexdigest().lower()
            if actual_sha != clean_expected:
                raise ValueError(
                    f"SHA-256 checksum verification failed (expected {clean_expected[:12]}..., got {actual_sha[:12]}...)."
                )

        os.replace(part_path, out_path)
        return out_path
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def can_self_update_executable() -> bool:
    """True when running as a frozen Windows PyInstaller executable."""
    return bool(
        sys.platform == "win32"
        and getattr(sys, "frozen", False)
        and Path(sys.executable).is_file()
    )


def _assert_safe_cmd_path(path: Path) -> str:
    raw = str(path.resolve())
    if _UNSAFE_CMD_CHARS_RE.search(raw):
        raise ValueError(f"Path contains unsafe shell characters: {raw!r}")
    return raw


def launch_windows_self_update(
    new_exe_path: str | Path,
    target_exe_path: str | Path | None = None,
    pid: int | None = None,
    work_dir: str | Path | None = None,
    relaunch: bool = True,
) -> Path:
    """Spawn a detached Windows helper script that swaps the frozen .exe after exit and relaunches."""
    src_exe = Path(new_exe_path)
    if not src_exe.is_file():
        raise FileNotFoundError(f"Downloaded installer not found: {src_exe}")

    dst_exe = Path(target_exe_path or sys.executable)
    cur_pid = int(pid or os.getpid())
    bak_exe = dst_exe.with_name(dst_exe.name + ".bak")

    src_s = _assert_safe_cmd_path(src_exe)
    dst_s = _assert_safe_cmd_path(dst_exe)
    bak_s = _assert_safe_cmd_path(bak_exe)

    tmp_root = Path(work_dir) if work_dir is not None else Path(os.getenv("TEMP") or src_exe.parent)
    tmp_root.mkdir(parents=True, exist_ok=True)
    cmd_path = tmp_root / f"tiktok_streak_update_{cur_pid}.cmd"

    relaunch_line = f'start "" "{dst_s}"\r\n' if relaunch else ""
    script = (
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "SRC={src_s}"\r\n'
        f'set "DST={dst_s}"\r\n'
        f'set "BAK={bak_s}"\r\n'
        "set /a tries=0\r\n"
        ":wait_proc\r\n"
        f'tasklist /FI "PID eq {cur_pid}" 2>nul | find "{cur_pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  set /a tries+=1\r\n"
        "  if %tries% GEQ 45 goto apply_swap\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto wait_proc\r\n"
        ")\r\n"
        ":apply_swap\r\n"
        'if exist "%BAK%" del /f /q "%BAK%" >nul 2>nul\r\n'
        'if exist "%DST%" move /y "%DST%" "%BAK%" >nul 2>nul\r\n'
        'move /y "%SRC%" "%DST%" >nul 2>nul\r\n'
        "if errorlevel 1 (\r\n"
        '  if exist "%BAK%" if not exist "%DST%" move /y "%BAK%" "%DST%" >nul 2>nul\r\n'
        "  goto done\r\n"
        ")\r\n"
        'if exist "%BAK%" del /f /q "%BAK%" >nul 2>nul\r\n'
        f"{relaunch_line}"
        ":done\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )
    cmd_path.write_text(script, encoding="ascii")

    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
        subprocess.Popen(
            ["cmd.exe", "/c", str(cmd_path)],
            close_fds=True,
            creationflags=creationflags,
        )
    return cmd_path


__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "can_self_update_executable",
    "check_for_update",
    "download_update_asset",
    "is_newer_version",
    "launch_windows_self_update",
    "parse_version",
]
