# Verification for the global hotkey (hotkey.py) that opens/closes the
# overlay without a controller — Ctrl+Alt+P, standing in for the PS button.
#
#   .venv\Scripts\python.exe tests\verify_hotkey.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before anything reads it.
#
# This test genuinely simulates a physical key-press via keybd_event and
# confirms it fires through the real Windows WM_HOTKEY path — checking only
# that RegisterHotKey() returned success wouldn't prove the callback actually
# reaches the app, which is the part that matters. Ctrl+Alt+Space was the
# original choice; registration for it genuinely failed
# (ERROR_HOTKEY_ALREADY_REGISTERED, confirmed via ctypes.get_last_error(),
# not assumed) on real hardware during development — something else already
# owns it — which is why the default is Ctrl+Alt+P instead. If this test
# starts failing with the same error, something on the test machine has
# claimed Ctrl+Alt+P; that's an environment conflict, not a code bug.

import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_hotkey_test_")

import hotkey

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
_VK_CONTROL, _VK_MENU, _VK_P = 0x11, 0x12, 0x50
_KEYEVENTF_KEYUP = 0x0002


def press_ctrl_alt_p() -> None:
    for vk in (_VK_CONTROL, _VK_MENU, _VK_P):
        user32.keybd_event(vk, 0, 0, None)
    time.sleep(0.05)
    for vk in (_VK_P, _VK_MENU, _VK_CONTROL):
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, None)


def wait_until(predicate, timeout=1.5, step=0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# --------------------------------------------------- registration + real press
print("\n[registration and a real simulated key-press]")
fire_count = [0]
listener = hotkey.HotkeyListener(
    on_pressed=lambda: fire_count.__setitem__(0, fire_count[0] + 1),
    shortcut="ctrl_alt_p",
)
listener.start()

check("hotkey registers successfully", wait_until(lambda: listener.registered))

press_ctrl_alt_p()
check("a real Ctrl+Alt+P press fires the callback through the actual OS path",
      wait_until(lambda: fire_count[0] == 1), f"(fired {fire_count[0]} times)")

# MOD_NOREPEAT means a held key shouldn't spam-fire; a second, distinct press
# is the practically-testable version of "fires once per press, not a burst".
press_ctrl_alt_p()
check("a second distinct press fires exactly once more, not a burst",
      wait_until(lambda: fire_count[0] == 2), f"(count now {fire_count[0]})")

# ------------------------------------------------------ automatic fallback
print("\n[automatic fallback]")
fallback = hotkey.HotkeyListener(on_pressed=lambda: None, shortcut="auto")
fallback.start()
check("automatic mode registers its fallback when Ctrl+Alt+P is occupied",
      wait_until(lambda: fallback.registered), f"(got {fallback.display_name!r})")
check("the automatic fallback is Ctrl+Alt+Shift+P",
      fallback.display_name == "Ctrl+Alt+Shift+P", f"(got {fallback.display_name!r})")
fallback.stop()

# ------------------------------------------------------- conflict handling
print("\n[a conflicting explicit shortcut is handled gracefully]")
second = hotkey.HotkeyListener(on_pressed=lambda: None, shortcut="ctrl_alt_p")
second.start()
wait_until(lambda: second._thread_id is not None)
time.sleep(0.3)  # let its _run() actually attempt RegisterHotKey and fail
check("an explicitly selected conflicting shortcut fails, doesn't raise",
      second.registered is False)
second.stop()

# ------------------------------------------------------------- clean shutdown
print("\n[clean shutdown actually unregisters]")
listener.stop()
check("the background thread exits after stop()", not listener._thread.is_alive())

listener2 = hotkey.HotkeyListener(on_pressed=lambda: None)
listener2.start()
wait_until(lambda: listener2._thread_id is not None)
time.sleep(0.3)
check("the combo is free again after a clean stop (proves UnregisterHotKey ran)",
      listener2.registered is True)
listener2.stop()

# -------------------------------------------------------- full app integration
print("\n[full integration: a real press through the actual app wiring]")
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from overlay import OverlayWindow


class _Bridge(QObject):
    hotkey_pressed = Signal()


app = QApplication(sys.argv)
QFontDatabase.addApplicationFont(os.path.join(_ROOT, "assets", "fonts", "Manrope.ttf"))
overlay = OverlayWindow(get_battery=lambda: 88)
bridge = _Bridge()
# Exactly main.py's own wiring: hotkey_pressed -> handle_button("ps"), which
# already opens if closed and closes if open — no separate logic needed.
bridge.hotkey_pressed.connect(lambda: overlay.handle_button("ps"))
app_listener = hotkey.HotkeyListener(
    on_pressed=bridge.hotkey_pressed.emit,
    shortcut="ctrl_alt_p",
)
app_listener.start()


def integration_step1():
    check("hotkey registered for the integration check",
          wait_until(lambda: app_listener.registered))
    check("overlay starts closed", not overlay.isVisible())
    press_ctrl_alt_p()
    QTimer.singleShot(400, integration_step2)


def integration_step2():
    check("a real press opened the overlay, same as a real PS press would",
          overlay.isVisible())
    press_ctrl_alt_p()
    QTimer.singleShot(400, integration_step3)


def integration_step3():
    check("pressing it again closed the overlay", not overlay.isVisible())
    app_listener.stop()

    # -------------------------------------------------------- diagnostics
    print("\n[diagnostics integration]")
    import diagnostics

    check("hotkey_registered() is None before any probe is registered",
          diagnostics.hotkey_registered() is None)
    diagnostics.register_hotkey_probe(lambda: True)
    check("hotkey_registered() reflects a registered probe",
          diagnostics.hotkey_registered() is True)
    diagnostics.register_hotkey_probe(lambda: False)
    check("hotkey_registered() reflects a failed probe",
          diagnostics.hotkey_registered() is False)

    def raising_probe():
        raise RuntimeError("boom")

    diagnostics.register_hotkey_probe(raising_probe)
    check("a probe that raises degrades to None rather than crashing",
          diagnostics.hotkey_registered() is None)

    diagnostics.register_hotkey_probe(lambda: True)
    report_text = diagnostics.report()
    check("the diagnostics report mentions the hotkey by name",
          hotkey.DISPLAY_NAME in report_text, f"(got report: {report_text!r})")

    finish()


def finish():
    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
    else:
        print("All checks passed.")
    print("=" * 60)
    app.exit(1 if failures else 0)


QTimer.singleShot(500, integration_step1)
sys.exit(app.exec())
