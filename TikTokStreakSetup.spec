# -*- mode: python ; coding: utf-8 -*-
"""Surgical PyInstaller spec for TikTokStreakSetup.exe.

Replaces brute-force --collect-all CLI flags to eliminate:
  - Unused macOS & Linux selenium-manager binaries (~16.7 MB)
  - Unused PySide6/Qt6 subsystems (opengl32sw.dll ~19.7 MB, QtQml/QtQuick/QtPdf/QtMultimedia/FFmpeg)
  - Unused Qt6 imageformats & non-EN/ID .qm translation files

Preserves 100% compatibility with:
  - Windows 11 Fluent UI (QtCore, QtGui, QtWidgets, QtSvg, QtSvgWidgets, QtXml + qfluentwidgets + qframelesswindow)
  - Selenium Chrome/Edge/Firefox automation (Windows selenium-manager.exe)
  - DPAPI/Credential Manager (win32crypt, win32cred, keyring), PyNaCl SealedBox, Pillow, dotenv
  - Single-file self-updater (gui/services/updater.py) & --run-bot CLI mode
"""
from __future__ import annotations

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()

# ---------------------------------------------------------------------------
# 1. Explicit data files bundled into the app
# ---------------------------------------------------------------------------
datas: list[tuple[str, str]] = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "src"), "src"),
    (str(ROOT / "main.py"), "."),
    (str(ROOT / "requirements.txt"), "."),
    (str(ROOT / "test_runner.py"), "."),
    (str(ROOT / ".github" / "workflows" / "streak.yml"), ".github/workflows"),
]
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = [
    "win32crypt",
    "win32cred",
    "pywintypes",
    "Crypto.Cipher.AES",
    "PIL",
    "dotenv",
    "selenium.webdriver.chrome.webdriver",
    "selenium.webdriver.edge.webdriver",
    "selenium.webdriver.firefox.webdriver",
]

# Collect qfluentwidgets WITHOUT multimedia (QtMultimedia+FFmpeg) or image_utils (scipy+numpy)
qfw_datas = collect_data_files("qfluentwidgets", excludes=["**/multimedia/**"])
qfw_mods = collect_submodules(
    "qfluentwidgets",
    filter=lambda name: not name.startswith("qfluentwidgets.multimedia")
    and name != "qfluentwidgets.common.image_utils",
)
datas += qfw_datas
hiddenimports += qfw_mods

for pkg in ("qframelesswindow", "keyring", "nacl"):
    p_datas, p_bins, p_mods = collect_all(pkg)
    datas += p_datas
    binaries += p_bins
    hiddenimports += p_mods

# Collect selenium selectively: keep Windows selenium-manager.exe ONLY, skip Safari/IE/WebKitGTK
sel_datas, sel_bins, sel_mods = collect_all(
    "selenium",
    filter_submodules=lambda name: not any(
        skip in name
        for skip in (
            "selenium.webdriver.safari",
            "selenium.webdriver.ie",
            "selenium.webdriver.webkitgtk",
            "selenium.webdriver.wpewebkit",
        )
    ),
)
datas += sel_datas
binaries += sel_bins
hiddenimports += sel_mods

# ---------------------------------------------------------------------------
# 2. Exclude unused Qt6 / stdlib / dev subsystems
# ---------------------------------------------------------------------------
excludes = [
    # Unused PySide6 modules (app uses ONLY QtCore, QtGui, QtWidgets, QtSvg, QtSvgWidgets, QtXml)
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtSql",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtTest",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtRemoteObjects",
    "PySide6.QtHttpServer",
    "PySide6.QtPrintSupport",
    "qfluentwidgets.multimedia",
    "qfluentwidgets.common.image_utils",
    # Unused scientific / dev / GUI / test packages (prevents dev-env pollution)
    "scipy",
    "numpy",
    "pandas",
    "pyarrow",
    "matplotlib",
    "lxml",
    "pygame",
    "colorthief",
    "IPython",
    "jupyter",
    "notebook",
    "playwright",
    "cv2",
    "torch",
    "sklearn",
    "sympy",
    "bokeh",
    "plotly",
    "seaborn",
    "statsmodels",
    "PyQt5",
    "PyQt6",
    "PIL.ImageTk",
    "PIL.ImageQt",
    "tkinter",
    "_tkinter",
    "pytest",
    "unittest",
]

a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

# ---------------------------------------------------------------------------
# 3. Post-analysis binary & data pruning (the biggest size & cold-start win)
# ---------------------------------------------------------------------------
_DROP_BINARY_EXACT = {
    "opengl32sw.dll",
    "qt6pdf.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6quick.dll",
    "qt6quickcontrols2.dll",
    "qt6quicktemplates2.dll",
    "qt6quickwidgets.dll",
    "qt6multimedia.dll",
    "qt6multimediawidgets.dll",
    "qt6opengl.dll",
    "qt6openglwidgets.dll",
    "qt6sql.dll",
    "qt6designer.dll",
    "qt6charts.dll",
    "qt6shadertools.dll",
    "qt6virtualkeyboard.dll",
    "qt6webenginecore.dll",
    "qt6printsupport.dll",
    "qt6network.dll",
}
_DROP_BINARY_PREFIXES = (
    "avcodec-",
    "avformat-",
    "avutil-",
    "swresample-",
    "swscale-",
    "qt6quick3d",
)
_KEEP_IMAGEFORMATS = {
    "qpng.dll",
    "qjpeg.dll",
    "qico.dll",
    "qsvg.dll",
    "qgif.dll",
    "qwebp.dll",
}
_KEEP_QT_LOCALES = ("_en.", "_en_", "_id.", "_id_")


def _is_non_windows_selenium_manager(norm_path: str) -> bool:
    if "selenium-manager" not in norm_path:
        return False
    return any(
        plat in norm_path
        for plat in ("/macos/", "/linux/", "/linux-x86_64/", "/linux-arm64/")
    )


def _keep_binary(entry: tuple[str, str, str]) -> bool:
    dest, src, _kind = entry
    norm = dest.replace("\\", "/").lower()
    fname = Path(norm).name
    if _is_non_windows_selenium_manager(norm):
        return False
    if fname in _DROP_BINARY_EXACT:
        return False
    if any(fname.startswith(pref) for pref in _DROP_BINARY_PREFIXES):
        return False
    if "/imageformats/" in norm and fname.endswith(".dll") and fname not in _KEEP_IMAGEFORMATS:
        return False
    if any(
        sub in norm
        for sub in (
            "/qml/",
            "/sqldrivers/",
            "/multimedia/",
            "/networkinformation/",
            "/tls/",
            "/position/",
            "/sensors/",
            "/printsupport/",
            "/virtualkeyboard/",
        )
    ):
        return False
    return True


def _keep_data(entry: tuple[str, str, str]) -> bool:
    dest, src, _kind = entry
    norm = dest.replace("\\", "/").lower()
    fname = Path(norm).name
    if _is_non_windows_selenium_manager(norm):
        return False
    if norm.endswith(".qm"):
        # Keep English and Indonesian Qt/QFluentWidgets translations only
        if not any(loc in fname for loc in _KEEP_QT_LOCALES):
            return False
    if "/qml/" in norm or "/multimedia/" in norm:
        return False
    return True


a.binaries = [b for b in a.binaries if _keep_binary(b)]
a.datas = [d for d in a.datas if _keep_data(d)]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TikTokStreakSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app_icon.ico"),
)
