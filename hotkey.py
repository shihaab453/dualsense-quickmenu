# A global keyboard shortcut that opens/closes the overlay from anywhere,
# even while a game or another app has focus — Ctrl+Alt+Space, acting as a
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
_MOD_NOREPEAT = 0x4000  # one WM_HOTKEY per physical press, not one per ~30ms while held
_VK_P = 0x50
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_ID = 1

# Ctrl+Alt+P. Chosen to avoid the common overlay-toggle combos gamers are
# likely to already have bound to something else — Alt+Z (NVIDIA), Shift+Tab
# (Steam/Discord), Win+G (Xbox Game Bar). Ctrl+Alt+Space was the first choice,
# but registration genuinely failed (ERROR_HOTKEY_ALREADY_REGISTERED) on real
# hardware during testing — something else already owns it — confirmed with
# ctypes.get_last_error(), not assumed from a hunch.
DISPLAY_NAME = "Ctrl+Alt+P"


class HotkeyListener:
    """Fires on_pressed() (no arguments) whenever the global hotkey is
    pressed, from a background thread — same calling convention as
    controller.DualSenseListener.on_button, and the same expectation that the
    caller hops back to the Qt main thread before touching any widgets."""

    def __init__(self, on_pressed=None):
        self._on_pressed = on_pressed
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
        self._thread_id = kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(
            None, _HOTKEY_ID, _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, _VK_P
        ))
        if not self.registered:
            # Not fatal — the app works fine without it, just without this
            # one extra way to open it. Most likely cause: another running
            # app already claimed Ctrl+Alt+Space.
            log.warning(
                "Couldn't register the global hotkey (%s) — it may already be "
                "bound by another app. The overlay still opens normally via "
                "the controller or the tray icon's Show menu.",
                DISPLAY_NAME,
            )
            return

        log.info("Global hotkey registered: %s", DISPLAY_NAME)
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
