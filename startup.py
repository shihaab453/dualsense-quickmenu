# "Start with Windows" — registering the app to launch at login.
#
# Uses the per-user Run key in the registry (HKEY_CURRENT_USER) rather than a
# shortcut in the Startup folder: no admin rights needed, no COM required to
# build a .lnk, and it's a single string that's easy to read back and compare.
#
# The comparison matters. A registry entry records an absolute path, so moving
# or renaming the app's folder leaves a stale entry pointing at nothing — the
# app silently stops starting with Windows and the setting still shows as on.
# refresh_if_stale() is called at startup to rewrite the path when it has moved,
# so that failure mode fixes itself instead of turning into a bug report.

import os
import sys
import winreg

import logs

log = logs.get(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "DualSenseQuickMenu"


def _quote(path: str) -> str:
    return f'"{path}"'


def command() -> str:
    """The command Windows should run at login, for *this* copy of the app."""
    if getattr(sys, "frozen", False):
        return _quote(sys.executable)
    # Running from source: launch through pythonw.exe so no console window
    # appears behind the game. sys.executable is python.exe when started that
    # way, so swap in its windowed twin if it's there.
    interpreter = sys.executable
    windowed = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
    if os.path.exists(windowed):
        interpreter = windowed
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f"{_quote(interpreter)} {_quote(script)}"


def _read() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, _VALUE_NAME)
            return value
    except FileNotFoundError:
        return None  # key or value absent — the normal "off" state
    except OSError:
        log.exception("Couldn't read the Run key")
        return None


def is_enabled() -> bool:
    return _read() is not None


def enable() -> bool:
    """Registers the app to start at login. Returns whether it worked."""
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command())
    except OSError:
        log.exception("Couldn't enable start-with-Windows")
        return False
    log.info("Start with Windows enabled: %s", command())
    return True


def disable() -> bool:
    """Unregisters it. Returns whether it worked; already-off counts as fine."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        return True  # wasn't set — that's the requested end state
    except OSError:
        log.exception("Couldn't disable start-with-Windows")
        return False
    log.info("Start with Windows disabled")
    return True


def refresh_if_stale() -> None:
    """If enabled but pointing somewhere else, repoint it at this copy.

    Covers the case where the app folder was moved, renamed, or replaced by a
    newer build in a different location — otherwise it just quietly stops
    launching at login while still looking enabled."""
    current = _read()
    if current is None:
        return
    expected = command()
    if current == expected:
        return
    log.info("Start-with-Windows entry was stale (%s) — repointing to %s",
             current, expected)
    enable()
