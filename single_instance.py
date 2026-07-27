# Stops a second copy of the app from running.
#
# Two copies means two HID pollers at 100Hz fighting over the same controller,
# and two overlays both calling SetForegroundWindow — which is a bad first
# impression, and easy to trigger by accident: the app has no window, so
# double-clicking the exe a second time because "nothing happened" is the
# natural thing for someone to do.
#
# A named Windows mutex rather than a lock file or Qt's QSharedMemory: the OS
# releases it when the process dies *including on a crash*, so there's no stale
# lock to clean up and no "it says it's already running but it isn't" state to
# talk a tester out of over chat.

import ctypes
from ctypes import wintypes

import logs

log = logs.get(__name__)

# "Local\" scopes the mutex to this login session, so two different users on the
# same PC can each run their own copy.
_MUTEX_NAME = r"Local\DualSenseQuickMenu.SingleInstance"

_ERROR_ALREADY_EXISTS = 183

# Held for the process's lifetime — if this were a local, the mutex would be
# closed as soon as the function returned and the guard would do nothing.
_handle = None


def already_running() -> bool:
    """True if another copy of the app already holds the lock. Safe to call
    once, early in startup; the lock is held until the process exits."""
    global _handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not _handle:
            log.warning("Couldn't create the single-instance mutex (error %s)",
                        ctypes.get_last_error())
            return False
        return ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
    except Exception:
        # Never let this check be the reason the app won't start — failing open
        # risks two instances, failing closed risks zero.
        log.exception("Single-instance check failed; continuing without it")
        return False
