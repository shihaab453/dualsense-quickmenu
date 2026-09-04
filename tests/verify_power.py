# Verification for Power-panel safeguards. This never invokes Windows power
# commands: every action is replaced before PowerPanel is constructed.
#
#   .venv\Scripts\python.exe tests\verify_power.py

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from actions import power
import panels.power as power_panel

calls = []
power.sleep = lambda: calls.append("sleep")
power.shut_down = lambda: calls.append("shutdown")
power.restart = lambda: calls.append("restart")

# Keep this verification fast while production retains its one-second hold.
power_panel._HOLD_SECONDS = 0.04

held = {"cross": False}


app = QApplication(sys.argv)
panel = power_panel.PowerPanel(is_cross_held=lambda: held["cross"])
nav = panel.build_nav()

print("\n[sleep]")
nav.activate()
check("Sleep still runs on a single Cross press", calls == ["sleep"], f"(got {calls})")

print("\n[Shut Down confirmation]")
nav.move(1)
nav.activate()
check("Shut Down does not run on the initial press", calls == ["sleep"], f"(got {calls})")
check("hold progress is visible", not panel._status.isHidden() and "Keep holding" in panel._status.text())
panel._advance_confirmation()
check("releasing Cross cancels Shut Down", panel._confirming_row is None and calls == ["sleep"])

held["cross"] = True
nav.activate()


def after_shutdown():
    check("holding Cross executes Shut Down", calls == ["sleep", "shutdown"], f"(got {calls})")

    print("\n[Restart confirmation]")
    nav.move(1)
    nav.activate()
    QTimer.singleShot(100, after_restart)


def after_restart():
    check("holding Cross executes Restart", calls == ["sleep", "shutdown", "restart"], f"(got {calls})")

    def failed_restart():
        raise OSError("access denied")

    panel._rows[2].action = failed_restart
    panel._run_action(panel._rows[2])
    check("a Windows action failure is shown", "access denied" in panel._status.text(),
          f"(got {panel._status.text()!r})")
    finish(app)


QTimer.singleShot(100, after_shutdown)
sys.exit(app.exec())
