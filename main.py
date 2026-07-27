# Entry point: wires the controller listener (background thread) to the
# overlay window (main thread) and parks the app in the system tray.
#
# Run normally:        python main.py
# Open menu at start:  python main.py --demo      (testing without a controller)
# Verify a build:      DualSenseQuickMenu.exe --selftest

import os
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import logs
import resources
import settings
import settings_window
from controller import DualSenseListener
from icons import render_app_icon
from overlay import OverlayWindow

log = logs.get(__name__)

# Via resources.path, not dirname(__file__): in a packaged build the assets are
# unpacked somewhere else entirely, and the difference is silent — the app just
# renders in a fallback font.
_FONT_PATH = resources.path("assets", "fonts", "Manrope.ttf")


def _load_app_font() -> bool:
    """Bundles the design mockup's actual font (Manrope, a free Google Font)
    instead of falling back to a system font — the .ttf lives in
    assets/fonts/ rather than requiring the user to install anything.
    Degrades to the system font if the file is ever missing; every stylesheet
    in this app already has a sans-serif fallback in its font-family list.
    Returns whether it actually loaded, for --selftest."""
    if not os.path.exists(_FONT_PATH):
        log.warning("Bundled font missing at %s — falling back to system font", _FONT_PATH)
        return False
    if QFontDatabase.addApplicationFont(_FONT_PATH) == -1:
        log.warning("Qt refused to load the bundled font at %s", _FONT_PATH)
        return False
    return True


class ControllerBridge(QObject):
    """Carries controller events onto the Qt main thread.

    Qt widgets must only ever be touched from the main thread. Emitting a Qt
    signal from another thread automatically queues the call onto the main
    thread — so the listener thread emits, and the overlay receives safely.
    """

    button_pressed = Signal(str)
    connection_changed = Signal(bool)


def _make_tray_icon() -> QIcon:
    # Drawn in code (icons.render_app_icon) so the app doesn't need to ship an
    # image file, and so the tray icon and the .exe icon can't drift apart.
    return QIcon(render_app_icon(64))


def _selftest() -> int:
    """Checks the things that break when the app is *packaged* rather than run
    from source, and that are silent when they break.

    Run the built exe with --selftest to verify a build before shipping it:
    a missing QtSvg plugin makes every icon in the app render blank, a missing
    font file downgrades all text to a system fallback, and an unbundled
    hidapi.dll means the PS button never responds — none of which raise.

    Returns a process exit code: 0 if everything checks out."""
    from icons import render_icon

    results = []

    def emit(line: str) -> None:
        # Goes to the log as well as the console, because a windowed build has
        # no console at all — sys.stdout is None there, and a bare print()
        # would raise rather than being ignored. Reading log.txt is how you
        # check a packaged build.
        log.info("selftest: %s", line)
        if sys.stdout is not None:
            print(line)

    def check(label: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        emit(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")

    emit(f"frozen={resources.is_frozen()}  assets={resources.base_dir()}")

    check("bundled Manrope font loads", _load_app_font())

    # An icon that renders as a completely transparent pixmap is exactly what a
    # missing QtSvg plugin produces — no exception, just nothing.
    pixmap = render_icon("music", "white", 26)
    opaque_pixels = 0
    if not pixmap.isNull():
        image = pixmap.toImage()
        opaque_pixels = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )
    check("SVG icons render (QtSvg present)", opaque_pixels > 0,
          f"{opaque_pixels} visible pixels")

    try:
        import pydualsense  # noqa: F401
        check("pydualsense + hidapi.dll load", True)
    except Exception as e:
        check("pydualsense + hidapi.dll load", False, f"{type(e).__name__}: {e}")

    # winrt powers the Now Playing fallback for non-Spotify players, and
    # actions/now_playing.py deliberately swallows its ImportError — so if
    # PyInstaller misses it, the feature just quietly stops existing.
    from actions import now_playing
    check("winrt media session available", now_playing._AVAILABLE)

    try:
        settings.load()
        check("settings readable", True)
    except Exception as e:
        check("settings readable", False, f"{type(e).__name__}: {e}")

    check("log file writable", os.path.exists(logs.log_path()))

    passed = all(results)
    emit("all checks passed" if passed else "SELFTEST FAILED")
    return 0 if passed else 1


def main() -> None:
    # First, before anything that could fail: under pythonw.exe there's no
    # console, so without this an early crash leaves no trace at all.
    logs.setup()

    app = QApplication(sys.argv)

    if "--selftest" in sys.argv:
        # Needs a QApplication (Qt refuses to render a pixmap without one) but
        # no window, tray icon or controller thread.
        sys.exit(_selftest())
    # We live in the tray: closing/hiding the overlay must not quit the app.
    app.setQuitOnLastWindowClosed(False)
    _load_app_font()

    bridge = ControllerBridge()
    listener = DualSenseListener(
        on_button=bridge.button_pressed.emit,
        on_connection_change=bridge.connection_changed.emit,
    )
    overlay = OverlayWindow(
        get_battery=lambda: listener.battery_percent,
        is_held=lambda name: listener.held.get(name, False),
    )
    bridge.button_pressed.connect(overlay.handle_button)
    bridge.connection_changed.connect(overlay.set_controller_connected)

    tray = QSystemTrayIcon(_make_tray_icon())
    tray.setToolTip("DualSense Quick Menu")
    tray_menu = QMenu()
    tray_menu.addAction("Show menu").triggered.connect(overlay.open_menu)
    # First-run setup (Spotify client ID, Task Switcher games) can't happen on
    # the D-pad-driven overlay — see settings_window's module docstring.
    tray_menu.addAction("Settings…").triggered.connect(settings_window.open_settings)
    tray_menu.addAction("Quit").triggered.connect(app.quit)
    tray.setContextMenu(tray_menu)
    tray.show()

    if "--demo" in sys.argv:
        overlay.open_menu()

    listener.start()
    exit_code = app.exec()
    listener.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
