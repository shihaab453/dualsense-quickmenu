# Sleep/Shutdown/Restart via the same OS-level calls Windows' own Start Menu
# power options use. The UI supplies the hold-to-confirm protection for the
# destructive actions; these functions report an OS-level failure so it can
# be shown rather than disappearing into a background process.

import ctypes
import subprocess


def sleep() -> None:
    # bHibernate=False (sleep, not hibernate), bForce=False, bWakeupEventsDisabled=False —
    # matches Windows' own Start Menu "Sleep": respects apps that veto the
    # suspend rather than force-closing them.
    if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
        raise OSError("Windows refused the sleep request")


def shut_down() -> None:
    _run_shutdown("/s")


def restart() -> None:
    _run_shutdown("/r")


def _run_shutdown(flag: str) -> None:
    result = subprocess.run(
        ["shutdown", flag, "/t", "0"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise OSError(f"Windows shutdown command failed: {detail}")
