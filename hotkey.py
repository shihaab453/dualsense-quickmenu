# A global keyboard shortcut that opens/closes the overlay from anywhere,
# even while a game or another app has focus, acting as a
# stand-in for the PS button itself.
#
# Why this needs to exist alongside the keyboard fallback overlay.py already
# has (arrow keys/Enter/Esc, wired through _KEYMAP in keyPressEvent): that
# fallback only works once the overlay window already has real OS keyboard
# focus, which normally only happens *after* it's already open. There was no
# keyboard way to open it in the first place — only a real PS button press, or
# alt-tabbing to click "Show menu" on the tray icon, which defeats the point
# of an overlay whose whole purpose is not needing to alt-tab. This closes
# that gap, so the app is fully usable — not just navigable once open — with
# no controller connected at all.
#
# Windows' RegisterHotKey needs a thread with a running message loop to
# receive WM_HOTKEY. Rather than hooking into Qt's own event loop (a frameless,
# translucent, always-on-top top-level window already doing its own
# foreground-forcing tricks felt like the wrong place to also splice in raw
# native event filtering), this runs its own tiny message loop on a background
# thread — the exact same shape as controller.py's DualSenseListener: a
# background thread and a plain callback, bridged onto the Qt main thread by
# the caller (main.py) via a Signal, the same way real controller button
# presses already are.

import ctypes
import threading
from ctypes import wintypes

import logs
import settings

log = logs.get(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000  # one WM_HOTKEY per physical press, not one per ~30ms while held
_VK_P = 0x50
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_ID = 1

# These avoid common gaming-overlay combinations such as Alt+Z (NVIDIA),
# Shift+Tab (Steam/Discord), and Win+G (Xbox Game Bar). The fallback adds
# Shift rather than choosing an unrelated key, so it remains easy to remember.
_SHORTCUTS = {
    "ctrl_alt_p": ("Ctrl+Alt+P", _MOD_CONTROL | _MOD_ALT, _VK_P),
    "ctrl_alt_shift_p": ("Ctrl+Alt+Shift+P", _MOD_CONTROL | _MOD_ALT | _MOD_SHIFT, _VK_P),
}
_AUTO = "auto"
DISPLAY_NAME = _SHORTCUTS["ctrl_alt_p"][0]

# The live listener updates this after it has tried registration. It lets the
# Settings dialog describe the shortcut actually in use without importing
# main.py, where the listener instance belongs.
_last_registration = None


def shortcut_choices():
    """(stored value, human label) choices shown in Settings."""
    return (
        (_AUTO, f"Automatic ({DISPLAY_NAME}, then Ctrl+Alt+Shift+P)"),
        ("ctrl_alt_p", DISPLAY_NAME),
        ("ctrl_alt_shift_p", "Ctrl+Alt+Shift+P"),
    )


def registration_candidates(shortcut: str):
    """Shortcut identifiers to try for a stored preference."""
    if shortcut == _AUTO:
        return ("ctrl_alt_p", "ctrl_alt_shift_p")
    return (shortcut,) if shortcut in _SHORTCUTS else registration_candidates(_AUTO)


def last_registration():
    return _last_registration


class HotkeyListener:
    """Fires on_pressed() (no arguments) whenever the global hotkey is
    pressed, from a background thread — same calling convention as
    controller.DualSenseListener.on_button, and the same expectation that the
    caller hops back to the Qt main thread before touching any widgets."""

    def __init__(self, on_pressed=None, on_registration=None, shortcut=None):
        self._on_pressed = on_pressed
        self._on_registration = on_registration
        self.shortcut = shortcut or settings.get_hotkey_shortcut()
        self.requested_display_name = (
            "Automatic" if self.shortcut == _AUTO
            else _SHORTCUTS.get(self.shortcut, _SHORTCUTS["ctrl_alt_p"])[0]
        )
        self.display_name = None
        self.used_fallback = False
        self._thread = None
        self._thread_id = None
        self.registered = False  # readable after start() settles, for diagnostics

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id is not None:
            # Unblocks the GetMessageW loop below — it has no timeout of its
            # own, so there's no other way to make stop() actually stop it.
            user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    # ---- runs on the background thread ----

    def _run(self) -> None:
        global _last_registration
        self._thread_id = kernel32.GetCurrentThreadId()
        active_shortcut = None
        candidates = registration_candidates(self.shortcut)
        for candidate in candidates:
            display_name, modifiers, virtual_key = _SHORTCUTS[candidate]
            if user32.RegisterHotKey(
                None, _HOTKEY_ID, modifiers | _MOD_NOREPEAT, virtual_key
            ):
                active_shortcut = candidate
                self.display_name = display_name
                self.used_fallback = candidate != candidates[0]
                break

        self.registered = active_shortcut is not None
        if not self.registered:
            log.warning(
                "Couldn't register a global hotkey (%s). The overlay still "
                "opens via the controller or the tray icon's Show menu.",
                ", ".join(_SHORTCUTS[item][0] for item in candidates),
            )
            _last_registration = {
                "registered": False,
                "display_name": None,
                "requested_display_name": self.requested_display_name,
                "used_fallback": False,
            }
            self._notify_registration()
            return

        _last_registration = {
            "registered": True,
            "display_name": self.display_name,
            "requested_display_name": self.requested_display_name,
            "used_fallback": self.used_fallback,
        }
        log.info("Global hotkey registered: %s", self.display_name)
        self._notify_registration()
        try:
            msg = wintypes.MSG()
            # NULL hWnd: hotkey messages land on this thread's own queue
            # rather than needing an actual window to own them.
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    if self._on_pressed:
                        self._on_pressed()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            log.info("Global hotkey unregistered")

    def _notify_registration(self) -> None:
        if self._on_registration:
            self._on_registration(_last_registration)
