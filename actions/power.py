# Sleep/Shutdown/Restart via the same OS-level calls Windows' own Start Menu
# power options use. No confirmation step here — matching the mockup's own
# design, where pressing the row *is* the confirmation (same as the PS5).

import ctypes
import subprocess


def sleep() -> None:
    # bHibernate=False (sleep, not hibernate), bForce=False, bWakeupEventsDisabled=False —
    # matches Windows' own Start Menu "Sleep": respects apps that veto the
    # suspend rather than force-closing them.
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shut_down() -> None:
    subprocess.run(["shutdown", "/s", "/t", "0"], check=False)


def restart() -> None:
    subprocess.run(["shutdown", "/r", "/t", "0"], check=False)
