# DualSense Quick Menu — a PS5-style Control Center overlay for Windows.
# Copyright (C) 2026 shihaab453. All rights reserved except as granted in
# LICENSE.
#
# Licensed under the PolyForm Strict License 1.0.0 with additional permissions
# — see LICENSE. In short: you may read this code, run the app for your own
# personal non-commercial use, and modify it for yourself or to contribute back.
# You may NOT distribute or publish the software or any modified version, and
# you may not remove the author attribution or present this work as your own.
#
# THE SOFTWARE COMES WITHOUT ANY WARRANTY OR CONDITION, AND THE LICENSOR IS NOT
# LIABLE FOR ANY DAMAGES ARISING FROM ITS USE. See LICENSE for the full terms.
#
# ---------------------------------------------------------------------------
#
# Entry point: wires the controller listener (background thread) to the
# overlay window (main thread) and parks the app in the system tray.
#
# Run normally:        python main.py
# Open menu at start:  python main.py --demo      (testing without a controller)
# Verify a build:      DualSenseQuickMenu.exe --selftest

import os
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

import diagnostics
import hotkey
import logs
import resources
import settings
import settings_window
import single_instance
import startup
import version
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


class InputBridge(QObject):
    """Carries background-thread input events onto the Qt main thread — real
    controller presses (DualSenseListener) and the global hotkey
    (hotkey.HotkeyListener) alike.

    Qt widgets must only ever be touched from the main thread. Emitting a Qt
    signal from another thread automatically queues the call onto the main
    thread — so the listener thread emits, and the overlay receives safely.
    """

    button_pressed = Signal(str)
    connection_changed = Signal(bool)
    hotkey_pressed = Signal()


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

    emit(f"{version.APP_NAME} {version.VERSION}")
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

    try:
        from actions import window_switcher
        window_count = len(window_switcher.list_switchable_windows())
        check("window enumeration works (Alt-Tab-style switcher)", True,
              f"{window_count} switchable windows found")
    except Exception as e:
        check("window enumeration works (Alt-Tab-style switcher)", False,
              f"{type(e).__name__}: {e}")

    passed = all(results)
    emit("all checks passed" if passed else "SELFTEST FAILED")
    return 0 if passed else 1


def _run_first_launch(tray: QSystemTrayIcon) -> None:
    """What happens the very first time the app is ever started.

    Without this, launching it does nothing visible: there's no window, just a
    new icon in a tray that's often collapsed behind the ^ arrow. Someone who
    hasn't been told it's a tray app has no way to know it worked, and the thing
    they need to do next (connect Spotify) is in a window they don't know
    exists. So: say it's running, and open that window.
    """
    log.info("First launch — showing the welcome notification and opening Settings")
    tray.showMessage(
        f"{version.APP_NAME} is running",
        # The Borderless line is here rather than only in the README because
        # testers don't read READMEs, and "the overlay doesn't show over my
        # game" is the most likely report — it's a Windows limitation, not a bug,
        # so the cheapest fix is telling people before they hit it.
        "It lives in your system tray — press the PS button on your controller "
        f"to open it, or {hotkey.DISPLAY_NAME} if you don't have one handy. "
        "Set your games to Borderless (not Fullscreen) so the overlay can "
        "draw on top.",
        QIcon(render_app_icon(64)),
        15000,
    )
    # Deferred rather than called directly: the tray notification should be on
    # screen before a window steals attention, and Qt hasn't drawn it yet at
    # this point in startup.
    QTimer.singleShot(1200, settings_window.open_settings)
    settings.mark_launched()


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

    # If "Start with Windows" is on but its recorded path has moved (folder
    # renamed, new build extracted elsewhere), repoint it — otherwise it stops
    # launching at login while still showing as enabled.
    startup.refresh_if_stale()

    if single_instance.already_running():
        # Told, not silently ignored: the app has no window, so someone who
        # double-clicked twice would otherwise see nothing happen a second time
        # and reasonably conclude it's broken. This also teaches them where it
        # actually lives.
        log.info("Another instance is already running — showing a notice and exiting")
        box = QMessageBox()
        box.setWindowTitle(version.APP_NAME)
        box.setIcon(QMessageBox.Information)
        box.setText(f"{version.APP_NAME} is already running.")
        box.setInformativeText(
            "Look for the blue “PS” icon in your system tray (bottom-right of "
            "the taskbar, possibly under the ^ arrow). Right-click it for the "
            "menu and settings."
        )
        box.setWindowIcon(QIcon(render_app_icon(64)))
        box.exec()
        sys.exit(0)

    bridge = InputBridge()
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

    # The global hotkey stands in for the PS button itself: handle_button("ps")
    # already opens the overlay if it's closed and closes it if it's open,
    # exactly what a real PS press does, so the hotkey needs no logic of its
    # own beyond triggering that same call.
    hotkey_listener = hotkey.HotkeyListener(on_pressed=bridge.hotkey_pressed.emit)
    bridge.hotkey_pressed.connect(lambda: overlay.handle_button("ps"))

    # Lets Settings -> Copy diagnostics state whether a controller is actually
    # connected. Registered as a probe rather than imported, so diagnostics.py
    # doesn't need to reach back into main.
    diagnostics.register_controller_probe(
        lambda: (listener.connected, listener.battery_percent)
    )
    diagnostics.register_hotkey_probe(lambda: hotkey_listener.registered)

    tray = QSystemTrayIcon(_make_tray_icon())
    tray.setToolTip(f"{version.APP_NAME} {version.VERSION}")
    tray_menu = QMenu()
    # The hotkey is mentioned in the label itself (not bound as this QAction's
    # own Qt shortcut, which would only fire while the tray menu has focus) —
    # this is purely where someone would discover the real, global one exists.
    tray_menu.addAction(f"Show menu ({hotkey.DISPLAY_NAME})").triggered.connect(overlay.open_menu)
    # First-run setup (the Spotify client ID) can't happen on the D-pad-driven
    # overlay — see settings_window's module docstring.
    tray_menu.addAction("Settings…").triggered.connect(settings_window.open_settings)
    tray_menu.addAction("Quit").triggered.connect(app.quit)
    tray.setContextMenu(tray_menu)
    tray.show()

    if settings.is_first_run():
        _run_first_launch(tray)

    if "--demo" in sys.argv:
        overlay.open_menu()

    listener.start()
    hotkey_listener.start()
    exit_code = app.exec()
    listener.stop()
    hotkey_listener.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
