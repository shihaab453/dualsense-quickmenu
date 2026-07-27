# PyInstaller build recipe. Build with:
#
#   .venv\Scripts\python.exe -m PyInstaller DualSenseQuickMenu.spec --noconfirm
#
# then verify the result before shipping it:
#
#   dist\DualSenseQuickMenu\DualSenseQuickMenu.exe --selftest
#
# (windowed builds have no console, so read the results from
# %APPDATA%\DualSenseQuickMenu\log.txt)
#
# Three things here are load-bearing and none of them fail loudly if dropped:
#
# 1. hidapi.dll. pydualsense ships this DLL inside its own package directory
#    and, at import time, appends dirname(__file__) to os.environ["PATH"] so
#    that hidapi.py's ffi.dlopen("hidapi.dll") — a bare filename, no path —
#    finds it. PyInstaller can't see that: nothing imports the DLL as a module,
#    it's only ever named in a string. So it has to be placed by hand, into a
#    "pydualsense" subdirectory, because that's where dirname(__file__) will
#    point inside the bundle. Get this wrong and the PS button silently never
#    responds.
#
# 2. The Manrope font. Loaded through resources.path(), which resolves against
#    sys._MEIPASS in a build — the file has to actually be there, or every
#    label falls back to a system font and nothing errors.
#
# 3. winrt's submodules. actions/now_playing.py catches ImportError and sets
#    _AVAILABLE = False, so a missing winrt doesn't crash — the Now Playing
#    fallback for non-Spotify players just quietly stops working.
#
# --selftest checks all three, which is why it exists.

import importlib.util
import os

from PyInstaller.utils.hooks import collect_submodules

# find_spec rather than import: locating the package shouldn't trigger
# pydualsense's import-time dlopen of hidapi.dll.
_pydualsense_dir = os.path.dirname(importlib.util.find_spec("pydualsense").origin)

binaries = [
    (os.path.join(_pydualsense_dir, "hidapi.dll"), "pydualsense"),
]

datas = [
    ("assets/fonts/Manrope.ttf", "assets/fonts"),
]

hiddenimports = (
    collect_submodules("winrt.windows.media.control")
    + collect_submodules("winrt.windows.foundation")
    + ["winrt.runtime"]
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Qt ships a lot this app never touches. Excluding the big ones keeps the
    # build from doubling in size for no benefit.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DualSenseQuickMenu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window: this is a tray app that sits behind a game. Anything
    # it would have printed goes to log.txt instead (see logs.py).
    console=False,
    icon="assets/icon.ico",
)

# One directory, not one file. A onefile build unpacks all of Qt into a temp
# directory on every single launch, which costs seconds of startup for an app
# that's meant to start at login and sit idle in the tray. Ship the dist folder
# zipped instead.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DualSenseQuickMenu",
)
