"""Self-update checker, verified installer downloader, and Windows EXE self-updater (zero Qt)."""
from __future__ import annotations

import hashlib
import html
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

CHECK_INTERVAL_SECONDS = 60
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


def _sanitize_relaunch_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Strip PyInstaller/Qt _MEI runtime variables so a relaunched onefile .exe boots cleanly."""
    src_env = dict(os.environ if env is None else env)
    out: dict[str, str] = {}
    mei_path_keys = {
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML_IMPORT_PATH",
        "QML2_IMPORT_PATH",
        "TCL_LIBRARY",
        "TK_LIBRARY",
    }
    for key, val in src_env.items():
        upper = key.upper()
        if upper in ("_MEIPASS", "_MEIPASS2") or upper.startswith("_PYI_"):
            continue
        if upper in mei_path_keys and "_MEI" in str(val).upper():
            continue
        out[key] = val
    return out


def launch_windows_self_update(
    new_exe_path: str | Path,
    target_exe_path: str | Path | None = None,
    pid: int | None = None,
    work_dir: str | Path | None = None,
    relaunch: bool = True,
    parent_pid: int | None = None,
) -> Path:
    """Spawn a hidden Windows helper script that swaps the frozen .exe after exit and relaunches."""
    src_exe = Path(new_exe_path)
    if not src_exe.is_file():
        raise FileNotFoundError(f"Downloaded installer not found: {src_exe}")

    dst_exe = Path(target_exe_path or sys.executable)
    cur_pid = int(pid or os.getpid())
    if parent_pid is not None:
        ppid = int(parent_pid) if int(parent_pid) > 4 and int(parent_pid) != cur_pid else 0
    elif getattr(sys, "frozen", False):
        raw_ppid = os.getppid()
        ppid = int(raw_ppid) if raw_ppid > 4 and raw_ppid != cur_pid else 0
    else:
        ppid = 0

    bak_exe = dst_exe.with_name(dst_exe.name + ".bak")

    src_s = _assert_safe_cmd_path(src_exe)
    dst_s = _assert_safe_cmd_path(dst_exe)
    dst_dir_s = _assert_safe_cmd_path(dst_exe.parent)
    bak_s = _assert_safe_cmd_path(bak_exe)

    tmp_root = Path(work_dir) if work_dir is not None else Path(os.getenv("TEMP") or src_exe.parent)
    tmp_root.mkdir(parents=True, exist_ok=True)
    cmd_path = tmp_root / f"tiktok_streak_update_{cur_pid}.cmd"

    wait_parent_block = ""
    if ppid > 0:
        wait_parent_block = (
            "set /a ptries=0\r\n"
            ":wait_parent_loop\r\n"
            f'tasklist /FI "PID eq {ppid}" 2>nul | find "{ppid}" >nul\r\n'
            "if not errorlevel 1 (\r\n"
            "  set /a ptries+=1\r\n"
            "  if %ptries% GEQ 15 goto apply_swap\r\n"
            "  ping -n 2 127.0.0.1 >nul\r\n"
            "  goto wait_parent_loop\r\n"
            ")\r\n"
        )

    relaunch_line = f'start "" /D "{dst_dir_s}" "{dst_s}"\r\n' if relaunch else ""
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
        "  if %tries% GEQ 45 goto wait_parent\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto wait_proc\r\n"
        ")\r\n"
        ":wait_parent\r\n"
        f"{wait_parent_block}"
        ":apply_swap\r\n"
        "set /a swap_tries=0\r\n"
        ":retry_swap\r\n"
        'if exist "%BAK%" del /f /q "%BAK%" >nul 2>nul\r\n'
        'if exist "%DST%" move /y "%DST%" "%BAK%" >nul 2>nul\r\n'
        'move /y "%SRC%" "%DST%" >nul 2>nul\r\n'
        "if errorlevel 1 (\r\n"
        '  if exist "%BAK%" if not exist "%DST%" move /y "%BAK%" "%DST%" >nul 2>nul\r\n'
        "  set /a swap_tries+=1\r\n"
        "  if %swap_tries% GEQ 15 goto done\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto retry_swap\r\n"
        ")\r\n"
        'if exist "%BAK%" del /f /q "%BAK%" >nul 2>nul\r\n'
        f"{relaunch_line}"
        ":done\r\n"
        'del "%~f0"\r\n'
    )
    cmd_path.write_text(script, encoding="ascii")

    if sys.platform == "win32":
        # Avoid DETACHED_PROCESS: in --windowed GUI apps it stalls `tasklist | find` pipes.
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
        run_cwd = dst_dir_s if dst_exe.parent.is_dir() else str(tmp_root)
        subprocess.Popen(
            ["cmd.exe", "/c", str(cmd_path)],
            cwd=run_cwd,
            env=_sanitize_relaunch_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    return cmd_path


_MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_UL_RE = re.compile(r"^\s{0,3}(?:[-*+]|•)\s+(.+)$")
_MD_OL_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+)$")
_MD_HR_RE = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
_MD_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def _format_inline_markdown(raw_text: str, *, code_bg: str, code_fg: str, link_fg: str) -> str:
    """Convert inline markdown safely to Qt-compatible HTML with literal tag escaping."""
    code_tokens: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        inner = html.escape(m.group(1), quote=True)
        idx = len(code_tokens)
        code_tokens.append(
            f'<code style="background-color:{code_bg}; color:{code_fg}; '
            f'font-family:\'Cascadia Mono\', \'Consolas\', monospace; font-size:11.5px;">'
            f"&nbsp;{inner}&nbsp;</code>"
        )
        return f"\x00MDCD{idx}\x00"

    protected = _MD_CODE_RE.sub(_stash_code, raw_text)
    escaped = html.escape(protected, quote=False)

    def _replace_link(m: re.Match[str]) -> str:
        label = m.group(1)
        href_raw = html.unescape(m.group(2)).strip()
        try:
            scheme = urllib.parse.urlparse(href_raw).scheme.lower()
        except Exception:
            scheme = ""
        if scheme not in ("https", "http"):
            return label
        safe_href = html.escape(href_raw, quote=True)
        return f'<a href="{safe_href}" style="color:{link_fg}; text-decoration:none;">{label}</a>'

    escaped = _MD_LINK_RE.sub(_replace_link, escaped)
    escaped = _MD_BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", escaped)
    escaped = _MD_ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", escaped)

    for idx, snippet in enumerate(code_tokens):
        escaped = escaped.replace(f"\x00MDCD{idx}\x00", snippet)
    return escaped


def markdown_to_update_html(raw_markdown: str, dark: bool = False) -> str:
    """Render GitHub Release Markdown cleanly into themed QTextBrowser-safe HTML."""
    fg = "#f3f3f3" if dark else "#1f1f1f"
    heading_fg = "#ffffff" if dark else "#111111"
    subhead_fg = "#d8e6f3" if dark else "#0f4c81"
    code_bg = "#32363d" if dark else "#eaeef3"
    code_fg = "#9cdcfe" if dark else "#0a3069"
    link_fg = "#60cdff" if dark else "#005fb8"
    hr_fg = "#3a3d42" if dark else "#dcdcdc"

    lines = str(raw_markdown or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    blocks: list[str] = []
    para_lines: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []

    def _fmt(txt: str) -> str:
        return _format_inline_markdown(txt, code_bg=code_bg, code_fg=code_fg, link_fg=link_fg)

    def _flush_para() -> None:
        if not para_lines:
            return
        joined = " ".join(s.strip() for s in para_lines if s.strip())
        para_lines.clear()
        if joined:
            blocks.append(f"<p>{_fmt(joined)}</p>")

    def _flush_list() -> None:
        nonlocal list_kind
        if not list_items or not list_kind:
            list_items.clear()
            list_kind = None
            return
        tag = list_kind
        inner = "".join(f"<li>{item}</li>" for item in list_items)
        blocks.append(f"<{tag}>{inner}</{tag}>")
        list_items.clear()
        list_kind = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            _flush_para()
            _flush_list()
            continue

        if _MD_HR_RE.match(stripped):
            _flush_para()
            _flush_list()
            blocks.append(f'<hr style="border:0; border-top:1px solid {hr_fg}; margin:10px 0;"/>')
            continue

        hm = _MD_HEADING_RE.match(raw_line)
        if hm:
            _flush_para()
            _flush_list()
            level = len(hm.group(1))
            title_html = _fmt(hm.group(2).strip())
            if level <= 2:
                blocks.append(f'<h2 style="color:{heading_fg};">{title_html}</h2>')
            elif level == 3:
                blocks.append(f'<h3 style="color:{heading_fg};">{title_html}</h3>')
            else:
                blocks.append(f'<h4 style="color:{subhead_fg};">{title_html}</h4>')
            continue

        um = _MD_UL_RE.match(raw_line)
        if um:
            _flush_para()
            if list_kind and list_kind != "ul":
                _flush_list()
            list_kind = "ul"
            list_items.append(_fmt(um.group(1).strip()))
            continue

        om = _MD_OL_RE.match(raw_line)
        if om:
            _flush_para()
            if list_kind and list_kind != "ol":
                _flush_list()
            list_kind = "ol"
            list_items.append(_fmt(om.group(1).strip()))
            continue

        if list_kind and raw_line.startswith(("  ", "\t")) and list_items:
            list_items[-1] = f"{list_items[-1]} {_fmt(stripped)}"
            continue

        _flush_list()
        para_lines.append(stripped)

    _flush_para()
    _flush_list()

    body_html = "\n".join(blocks)
    return (
        "<html><head><style>"
        f"body {{ color: {fg}; font-family: 'Segoe UI', sans-serif; font-size: 13px; "
        "line-height: 1.65; margin: 2px 4px; } "
        "h2 { font-size: 16px; font-weight: 700; margin: 4px 0 6px 0; } "
        "h3 { font-size: 14px; font-weight: 700; margin: 2px 0 6px 0; } "
        "h4 { font-size: 13px; font-weight: 700; margin: 10px 0 4px 0; } "
        "p { margin: 4px 0 8px 0; } "
        "ul, ol { margin: 4px 0 8px 0; -qt-list-indent: 1; } "
        "li { margin: 4px 0; } "
        f"</style></head><body>{body_html}</body></html>"
    )


__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "can_self_update_executable",
    "check_for_update",
    "download_update_asset",
    "is_newer_version",
    "launch_windows_self_update",
    "markdown_to_update_html",
    "parse_version",
]
