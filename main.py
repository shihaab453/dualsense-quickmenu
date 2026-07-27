# Entry point: wires the controller listener (background thread) to the
# overlay window (main thread) and parks the app in the system tray.
#
# Run normally:        python main.py
# Open menu at start:  python main.py --demo   (for testing without a controller)

import os
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import logs
import settings_window
from controller import DualSenseListener
from overlay import OverlayWindow

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "Manrope.ttf")


def _load_app_font() -> None:
    """Bundles the design mockup's actual font (Manrope, a free Google Font)
    instead of falling back to a system font — the .ttf lives in
    assets/fonts/ rather than requiring the user to install anything.
    Silently does nothing if the file is ever missing; every stylesheet in
    this app already has a sans-serif fallback in its font-family list."""
    if os.path.exists(_FONT_PATH):
        QFontDatabase.addApplicationFont(_FONT_PATH)


class ControllerBridge(QObject):
    """Carries controller events onto the Qt main thread.

    Qt widgets must only ever be touched from the main thread. Emitting a Qt
    signal from another thread automatically queues the call onto the main
    thread — so the listener thread emits, and the overlay receives safely.
    """

    button_pressed = Signal(str)
    connection_changed = Signal(bool)


def _make_tray_icon() -> QIcon:
    # Drawn in code so the app doesn't need to ship an image file.
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#2d6ff2"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(26)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "PS")
    painter.end()
    return QIcon(pixmap)


def main() -> None:
    # First, before anything that could fail: under pythonw.exe there's no
    # console, so without this an early crash leaves no trace at all.
    logs.setup()

    app = QApplication(sys.argv)
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
