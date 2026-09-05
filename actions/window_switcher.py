# Windows Alt-Tab-style window switching: list every switchable top-level
# window with its real icon, and bring a chosen one to the foreground.
#
# Genuinely different from the removed Task Switcher (see HANDOFF.md — that
# one needed a user-curated list of games; this one needs no configuration at
# all, works for any app, and is driven live by EnumWindows). Don't conflate
# the two.
#
# All raw ctypes — no pywin32. Every Win32 call below sets explicit
# argtypes/restype; HWNDs and icon/bitmap handles are pointer-sized, and this
# app has already been bitten once by ctypes silently truncating a 64-bit
# handle to 32 bits when the return type isn't set (see overlay.py's
# _force_foreground history) — not repeating that here.

import ctypes
import os
import time
from ctypes import wintypes

import psutil
from PySide6.QtGui import QImage, QPixmap

import logs

log = logs.get(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]

user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
]
user32.SendMessageTimeoutW.restype = ctypes.c_void_p
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.c_void_p]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]

gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_GW_OWNER = 4
_GCL_HICON = -14
_ICON_BIG = 1
_WM_GETICON = 0x007F
_SMTO_ABORTIFHUNG = 0x0002
_SW_RESTORE = 9

# Window classes that are always noise, never something a user would think of
# as "an app to switch to" — the desktop, the taskbar, and the invisible
# helper windows some UWP apps park alongside their real one.
_CLASS_BLOCKLIST = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
}


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


shell32.SHGetFileInfoW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW),
    wintypes.UINT, wintypes.UINT,
]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p

_SHGFI_ICON = 0x100
_SHGFI_LARGEICON = 0x0
_MONITOR_DEFAULTTONEAREST = 0x00000002


# ---- foreground-forcing, shared with overlay.py's own use for itself ----


def foreground_monitor_name():
    """Return the native display name containing the foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.szDevice or None


def foreground_window_center():
    """Return the desktop-coordinate center of the foreground window.

    Desktop coordinates can be negative when a monitor sits above or to the
    left of the primary display. Returning the coordinates unchanged lets Qt
    match them against its own per-screen logical geometries, including mixed
    DPI layouts.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (
        rect.left + (rect.right - rect.left) // 2,
        rect.top + (rect.bottom - rect.top) // 2,
    )


def force_foreground(hwnd: int) -> None:
    """Brings hwnd to the foreground, restoring it first if minimized.

    Windows normally refuses to let a background process steal focus (anti
    focus-stealing) — tapping ALT first is the long-standing workaround that
    lifts the restriction, and it isn't 100% reliable (a timing-based
    heuristic sits on top of it), hence the verify-and-retry loop. This is the
    exact mechanism overlay.py already used to force *itself* into the
    foreground when opening; generalized here to take any window, so both
    call sites share one implementation instead of two copies of the same
    Windows quirk-workaround."""
    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)

    for attempt in range(4):
        user32.keybd_event(_VK_MENU, 0, 0, None)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, None)
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.03 * (attempt + 1))


def switch_to(hwnd: int) -> bool:
    """Brings a window found via list_switchable_windows() to the foreground.
    Returns whether it actually ended up frontmost."""
    force_foreground(hwnd)
    return user32.GetForegroundWindow() == hwnd


# ---- icon extraction ----


def _get_window_icon_handle(hwnd: int):
    """An HICON for the window, or None. Tries the class icon first (fast,
    never blocks); falls back to asking the window directly via
    SendMessageTimeoutW rather than plain SendMessage — a window that's
    hung would otherwise stall this call, and by extension the whole D-pad
    thread, indefinitely."""
    icon = user32.GetClassLongPtrW(hwnd, _GCL_HICON)
    if icon:
        return icon

    result = ctypes.c_void_p()
    sent = user32.SendMessageTimeoutW(
        hwnd, _WM_GETICON, _ICON_BIG, 0,
        _SMTO_ABORTIFHUNG, 200, ctypes.byref(result),
    )
    if sent and result.value:
        return result.value
    return None


def _hicon_to_qimage(hicon, size: int) -> QImage | None:
    """Converts a live HICON into a QImage, or None on failure. Never raises
    — a bad icon shouldn't take the whole window list down, it should just
    leave that one row without an icon.

    QImage rather than QPixmap on purpose: enumeration runs on a worker
    thread, and QPixmap is a GUI-thread type. The conversion to a pixmap
    happens in the panel, on the thread allowed to do it."""
    icon_info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(icon_info)):
        return None
    try:
        bmp = BITMAP()
        if not gdi32.GetObjectW(icon_info.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bmp)):
            return None
        width, height = bmp.bmWidth, bmp.bmHeight
        if width <= 0 or height <= 0:
            return None

        mem_dc = gdi32.CreateCompatibleDC(None)
        if not mem_dc:
            return None
        try:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # negative: top-down DIB, matches QImage's own row order
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0  # BI_RGB

            buffer = ctypes.create_string_buffer(width * height * 4)
            copied = gdi32.GetDIBits(
                mem_dc, icon_info.hbmColor, 0, height,
                buffer, ctypes.byref(bmi), 0,
            )
            if not copied:
                return None
        finally:
            gdi32.DeleteDC(mem_dc)

        # Windows' 32bpp DIB byte order (B,G,R,A per pixel, little-endian) is
        # the same in-memory layout QImage.Format_ARGB32 expects on a
        # little-endian machine — confirmed against real extracted icons
        # rather than assumed; see verify_window_switcher.py.
        image = QImage(bytes(buffer), width, height, QImage.Format_ARGB32).copy()
        if size != width or size != height:
            image = image.scaled(size, size)
        return image
    finally:
        gdi32.DeleteObject(icon_info.hbmColor)
        gdi32.DeleteObject(icon_info.hbmMask)


def _get_exe_icon_handle(exe_path: str):
    """An HICON for the executable's own file icon, via the shell — the
    fallback for windows that don't answer GetClassLongPtrW/WM_GETICON at
    all. Confirmed necessary, not hypothetical: on this machine, both windows
    hosted by ApplicationFrameHost.exe (the modern-app/UWP shell process —
    e.g. the Settings app) returned no icon through either window-level path,
    since their real icon lives in packaged-app resources those APIs don't
    reach. Their exe (ApplicationFrameHost.exe itself) does have a normal
    file icon, so this at least shows *something* instead of a blank row."""
    info = SHFILEINFOW()
    result = shell32.SHGetFileInfoW(
        exe_path, 0, ctypes.byref(info), ctypes.sizeof(info),
        _SHGFI_ICON | _SHGFI_LARGEICON,
    )
    if not result or not info.hIcon:
        return None
    return info.hIcon


def get_window_icon_image(hwnd: int, size: int = 32, exe_path: str = "") -> QImage | None:
    """The window's own icon as a size x size QImage, or None if extraction
    failed every way it was tried — callers should fall back to a placeholder,
    the same way album art does when a track has none.

    Safe to call from a worker thread; see _hicon_to_qimage."""
    hicon = _get_window_icon_handle(hwnd)
    source = "window"
    if not hicon and exe_path:
        hicon = _get_exe_icon_handle(exe_path)
        source = "exe"
    if not hicon:
        return None
    try:
        return _hicon_to_qimage(hicon, size)
    except Exception:
        log.exception("Icon extraction (%s) failed for window %s", source, hwnd)
        return None
    finally:
        if source == "exe":
            # GetClassLongPtrW/WM_GETICON return a handle owned by the window;
            # SHGetFileInfo hands back one we're responsible for destroying.
            user32.DestroyIcon(hicon)


def get_window_icon(hwnd: int, size: int = 32, exe_path: str = "") -> QPixmap | None:
    """The same icon as a QPixmap. Qt main thread only — everything that runs
    on a worker should use get_window_icon_image and convert once it's back."""
    image = get_window_icon_image(hwnd, size, exe_path)
    return None if image is None else QPixmap.fromImage(image)


# ---- enumeration ----


def _is_switchable(hwnd: int, own_pid: int) -> bool:
    if not user32.IsWindowVisible(hwnd):
        return False
    if user32.GetWindowTextLengthW(hwnd) == 0:
        return False
    # A tool window (e.g. a floating palette) or one with an owner (typically
    # a dialog/popup, not a real top-level app window) isn't something a real
    # Alt-Tab shows either.
    ex_style = user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) or 0
    if ex_style & _WS_EX_TOOLWINDOW:
        return False
    if user32.GetWindow(hwnd, _GW_OWNER):
        return False

    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, 256)
    if class_buf.value in _CLASS_BLOCKLIST:
        return False

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return False

    return True


def list_switchable_windows(icon_size: int = 32) -> list[dict]:
    """Every top-level window a user could plausibly want to switch to, this
    app's own windows excluded, in Windows' own z-order (roughly most-
    recently-used-first — real Alt-Tab's ordering, not separately tracked
    here). Each entry: {"hwnd", "title", "pid", "process_name", "icon_image"}.

    Safe to call from a worker thread, which is how the panel calls it: the
    icon comes back as a QImage, and only the panel turns it into a pixmap.

    A simple heuristic, not a from-scratch reimplementation of Alt-Tab's own
    (fairly intricate) filtering — see HANDOFF's note on this before trying to
    make it stricter than it needs to be."""
    own_pid = os.getpid()
    results = []

    def callback(hwnd, _lparam):
        try:
            if not _is_switchable(hwnd, own_pid):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if not title.strip():
                return True

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_name = ""
            exe_path = ""
            try:
                process = psutil.Process(pid.value)
                process_name = process.name()
                exe_path = process.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            results.append({
                "hwnd": hwnd,
                "title": title,
                "pid": pid.value,
                "process_name": process_name,
                "icon_image": get_window_icon_image(hwnd, icon_size, exe_path),
            })
        except Exception:
            log.exception("Skipping a window during enumeration")
        return True  # keep enumerating

    user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return results
