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
# Four things here are load-bearing and none of them fail loudly if dropped:
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
# 4. The Spotify logo SVG (icons.render_spotify_logo, loaded via
#    resources.path()) — same failure mode as the font: missing in a build,
#    the Now Playing home card's reserved logo slot just renders blank.
#
# --selftest checks all four, which is why it exists.

import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, os.path.abspath(SPECPATH))
import version as app_version


def _write_version_resource() -> str:
    """Writes the Windows version resource so right-click -> Properties ->
    Details on the exe shows the build. Generated from version.py rather than
    kept as a committed file, so the version can't be bumped in one place and
    stay stale in the other."""
    major, minor, patch, build = app_version.numeric_version()
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', {app_version.APP_NAME!r}),
      StringStruct('FileVersion', {app_version.VERSION!r}),
      StringStruct('ProductName', {app_version.APP_NAME!r}),
      StringStruct('ProductVersion', {app_version.VERSION!r}),
      StringStruct('OriginalFilename', 'DualSenseQuickMenu.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    os.makedirs(workpath, exist_ok=True)
    out_path = os.path.join(workpath, "version_info.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


version_resource = _write_version_resource()

# find_spec rather than import: locating the package shouldn't trigger
# pydualsense's import-time dlopen of hidapi.dll.
_pydualsense_dir = os.path.dirname(importlib.util.find_spec("pydualsense").origin)

binaries = [
    (os.path.join(_pydualsense_dir, "hidapi.dll"), "pydualsense"),
]

datas = [
    ("assets/fonts/Manrope.ttf", "assets/fonts"),
    ("assets/Primary_Logo_Green_RGB.svg", "assets"),
    # The obligation to reproduce dependency copyright notices applies to what
    # is *distributed*, so these have to be inside the build, not just in the
    # repo. Regenerate with tools/make_notices.py when dependencies change.
    ("LICENSE", "."),
    ("THIRD-PARTY-NOTICES.md", "."),
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
    version=version_resource,
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
