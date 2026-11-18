"""Windows Task Scheduler (`schtasks.exe`) wrapper for daily local bot runs.

Supports both frozen PyInstaller builds (``TikTokStreakSetup.exe --run-bot``)
and source execution (``pythonw.exe run_gui.py --run-bot``). Zero Qt dependencies.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("tiktok-streak")

TASK_PREFIX = "TikTokStreakDaily_"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class SchedulerError(RuntimeError):
    """Raised when Windows Task Scheduler operations fail."""


def _normalize_hhmm(value: str) -> str:
    m = _TIME_RE.match(str(value or "").strip())
    if not m:
        raise SchedulerError(f"Invalid daily time {value!r}; expected 24-hour HH:MM (e.g. '08:00').")
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def build_bot_command(run_gui_path: str | Path | None = None) -> str:
    """Build the `/TR` command line for scheduled headless bot execution."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --run-bot'

    exe_path = Path(sys.executable).resolve()
    pythonw = exe_path.with_name("pythonw.exe")
    launcher = pythonw if pythonw.is_file() else exe_path

    if run_gui_path is not None:
        script_path = Path(run_gui_path).resolve()
    else:
        script_path = (Path(__file__).resolve().parents[2] / "run_gui.py").resolve()

    return f'"{launcher}" "{script_path}" --run-bot'


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    if sys.platform != "win32":
        raise SchedulerError("Windows Task Scheduler (schtasks) is only supported on Windows.")
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        return subprocess.run(["schtasks", *args], **kwargs)
    except FileNotFoundError as exc:
        raise SchedulerError("schtasks.exe was not found on PATH.") from exc
    except OSError as exc:
        raise SchedulerError(f"Failed executing schtasks: {exc}") from exc


def list_tasks(prefix: str = TASK_PREFIX) -> list[dict[str, str]]:
    """List scheduled Windows tasks whose names start with `prefix`."""
    if sys.platform != "win32":
        return []

    proc = _run_schtasks(["/Query", "/FO", "CSV", "/NH"])
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # No tasks at all is not fatal, though schtasks normally returns 0.
        if "cannot find" in err.lower() or "no tasks" in err.lower():
            return []
        raise SchedulerError(f"schtasks /Query failed ({proc.returncode}): {err}")

    tasks: list[dict[str, str]] = []
    reader = csv.reader(io.StringIO(proc.stdout or ""))
    for row in reader:
        if not row:
            continue
        raw_name = row[0].strip()
        clean_name = raw_name.lstrip("\\")
        if not clean_name.startswith(prefix):
            continue
        tasks.append({
            "name": clean_name,
            "next_run": row[1].strip() if len(row) > 1 else "",
            "status": row[2].strip() if len(row) > 2 else "",
        })
    return tasks


def remove_all_tasks(prefix: str = TASK_PREFIX) -> list[str]:
    """Delete all scheduled Windows tasks matching `prefix`."""
    if sys.platform != "win32":
        raise SchedulerError("Windows Task Scheduler (schtasks) is only supported on Windows.")

    existing = list_tasks(prefix=prefix)
    removed: list[str] = []
    for task in existing:
        name = task["name"]
        proc = _run_schtasks(["/Delete", "/F", "/TN", name])
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise SchedulerError(f"schtasks /Delete failed for '{name}' ({proc.returncode}): {err}")
        removed.append(name)
    return removed


def sync_daily_tasks(
    times_hhmm: Iterable[str],
    command: str | None = None,
    prefix: str = TASK_PREFIX,
) -> dict[str, Any]:
    """Replace existing `prefix*` tasks with daily tasks for each HH:MM time."""
    if sys.platform != "win32":
        raise SchedulerError("Windows Task Scheduler (schtasks) is only supported on Windows.")

    normalized_times = sorted({_normalize_hhmm(t) for t in times_hhmm if str(t).strip()})
    cmd = command or build_bot_command()
    if len(cmd) > 261:
        raise SchedulerError(
            f"Scheduled command exceeds Windows schtasks 261-character limit ({len(cmd)} chars)."
        )

    removed = remove_all_tasks(prefix=prefix)
    created: list[dict[str, str]] = []
    for hhmm in normalized_times:
        task_name = f"{prefix}{hhmm.replace(':', '')}"
        proc = _run_schtasks([
            "/Create",
            "/F",
            "/SC",
            "DAILY",
            "/ST",
            hhmm,
            "/TN",
            task_name,
            "/TR",
            cmd,
        ])
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise SchedulerError(
                f"schtasks /Create failed for '{task_name}' at {hhmm} ({proc.returncode}): {err}"
            )
        created.append({"name": task_name, "time": hhmm})

    return {
        "ok": True,
        "created": created,
        "removed": removed,
        "command": cmd,
    }


class WindowsScheduler:
    """Object-oriented convenience facade around the schtasks functions."""

    def __init__(self, prefix: str = TASK_PREFIX) -> None:
        self.prefix = prefix

    def build_command(self, run_gui_path: str | Path | None = None) -> str:
        return build_bot_command(run_gui_path=run_gui_path)

    def list_tasks(self) -> list[dict[str, str]]:
        return list_tasks(prefix=self.prefix)

    def sync_daily_tasks(self, times_hhmm: Iterable[str], command: str | None = None) -> dict[str, Any]:
        return sync_daily_tasks(times_hhmm, command=command, prefix=self.prefix)

    def remove_all_tasks(self) -> list[str]:
        return remove_all_tasks(prefix=self.prefix)


__all__ = [
    "SchedulerError",
    "TASK_PREFIX",
    "WindowsScheduler",
    "build_bot_command",
    "list_tasks",
    "remove_all_tasks",
    "sync_daily_tasks",
]
