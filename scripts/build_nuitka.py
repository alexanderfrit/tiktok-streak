"""Reproducible Nuitka cached-onefile build & benchmark script for TikTokStreakSetup.

Produces a native C-compiled single-file Windows executable that:
  - Extracts once into %LOCALAPPDATA%\\TikTokStreak\\<version> (--onefile-cache-mode=cached)
    eliminating PyInstaller --onefile's repeated %TEMP%\\_MEI* extraction & Defender rescan
  - Stays 100% compatible with gui/services/updater.py in-place single-EXE self-updates
  - Excludes unused Qt6 subsystems and non-Windows selenium-manager binaries

Usage:
    python scripts/build_nuitka.py [--output-dir dist-nuitka] [--skip-benchmark]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.version import APP_VERSION  # noqa: E402


def _find_windows_selenium_manager() -> Path:
    import selenium

    sel_root = Path(selenium.__file__).resolve().parent
    candidate = sel_root / "webdriver" / "common" / "windows" / "selenium-manager.exe"
    if not candidate.is_file():
        raise FileNotFoundError(f"Windows selenium-manager.exe not found at {candidate}")
    return candidate


def build_nuitka(output_dir: Path, exe_name: str = "TikTokStreakSetup-nuitka.exe") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    sel_mgr = _find_windows_selenium_manager()

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--onefile-cache-mode=cached",
        f"--onefile-tempdir-spec={{CACHE_DIR}}/TikTokStreak/{APP_VERSION}",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ROOT / 'assets' / 'app_icon.ico'}",
        f"--output-dir={output_dir}",
        f"--output-filename={exe_name}",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,styles,imageformats,iconengines",
        "--noinclude-qt-plugins=qml,multimedia,sql,sensors,positioning,bluetooth,nfc,webengine,network,printsupport,virtualkeyboard",
        "--nofollow-import-to=qfluentwidgets.multimedia",
        "--nofollow-import-to=qfluentwidgets.common.image_utils",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=numpy",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=pyarrow",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=lxml",
        "--nofollow-import-to=pygame",
        "--nofollow-import-to=colorthief",
        "--nofollow-import-to=selenium.webdriver.safari",
        "--nofollow-import-to=selenium.webdriver.ie",
        "--nofollow-import-to=selenium.webdriver.webkitgtk",
        "--nofollow-import-to=selenium.webdriver.wpewebkit",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=pytest",
        "--include-package=qfluentwidgets",
        "--include-package=qframelesswindow",
        "--include-package=keyring",
        "--include-package=nacl",
        "--include-package=selenium.webdriver.chrome",
        "--include-package=selenium.webdriver.edge",
        "--include-package=selenium.webdriver.firefox",
        "--include-module=win32crypt",
        "--include-module=win32cred",
        "--include-module=pywintypes",
        "--include-module=Crypto.Cipher.AES",
        "--include-module=PIL",
        "--include-module=dotenv",
        f"--include-data-dir={ROOT / 'assets'}=assets",
        f"--include-data-dir={ROOT / 'src'}=src",
        f"--include-data-files={ROOT / 'main.py'}=main.py",
        f"--include-data-files={ROOT / 'requirements.txt'}=requirements.txt",
        f"--include-data-files={ROOT / 'test_runner.py'}=test_runner.py",
        f"--include-data-files={ROOT / '.github' / 'workflows' / 'streak.yml'}=.github/workflows/streak.yml",
        f"--include-data-files={sel_mgr}=selenium/webdriver/common/windows/selenium-manager.exe",
        str(ROOT / "run_gui.py"),
    ]

    print("Running Nuitka build:")
    print(" ", " ".join(cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    exe_path = output_dir / exe_name
    if not exe_path.is_file():
        raise FileNotFoundError(f"Expected Nuitka executable not found: {exe_path}")
    return exe_path


def verify_and_benchmark(exe_path: Path) -> None:
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\n[Nuitka Artifact] {exe_path} ({size_mb:.2f} MB)")

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"

    with tempfile.TemporaryDirectory(prefix="ts_nuitka_smoke_") as tmp:
        # Cold run (first extraction into cached tempdir)
        t0 = time.perf_counter()
        subprocess.run(
            [str(exe_path), "--smoke-test", "--config-dir", tmp],
            env=env,
            check=True,
        )
        cold_ms = (time.perf_counter() - t0) * 1000.0

        # Warm run (reuses cached extraction)
        t1 = time.perf_counter()
        subprocess.run(
            [str(exe_path), "--smoke-test", "--config-dir", tmp],
            env=env,
            check=True,
        )
        warm_ms = (time.perf_counter() - t1) * 1000.0

        # Verify --run-bot CLI path responds cleanly
        proc = subprocess.run(
            [str(exe_path), "--run-bot", "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        if "--run-bot" not in (proc.stdout + proc.stderr):
            raise RuntimeError("Unexpected --run-bot --help output")

    print(f"[Nuitka Benchmark] Cold --smoke-test: {cold_ms:.0f} ms | Warm --smoke-test: {warm_ms:.0f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build & benchmark TikTokStreakSetup via Nuitka.")
    parser.add_argument("--output-dir", default="dist-nuitka", help="Directory for compiled output")
    parser.add_argument("--exe-name", default="TikTokStreakSetup-nuitka.exe", help="Output .exe filename")
    parser.add_argument("--skip-benchmark", action="store_true", help="Skip smoke-test & startup benchmark")
    args = parser.parse_args()

    out_dir = (ROOT / args.output_dir).resolve()
    exe_path = build_nuitka(out_dir, exe_name=args.exe_name)
    if not args.skip_benchmark:
        verify_and_benchmark(exe_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
