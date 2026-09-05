# Verification for per-open screen selection and viewport-aware panel sizing.
#
#   .venv\Scripts\python.exe tests\verify_panel_anchor.py
#
# Exits non-zero if anything fails.
#
# Qt exposes screen geometry in logical pixels. A 1920x1080 display at 150%
# scaling is therefore 1280x720 to the app. Music's preferred 1500px panel
# used to overflow it by 220px. These checks cover that case, a 200%-style
# logical viewport, negative monitor coordinates, monitor changes between
# opens, tall scroll content, and restoration on a larger display.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_anchor_")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QLabel

import overlay as overlay_module
from overlay import OverlayWindow
from panels.base import fit_scroll_to_content

app = QApplication(sys.argv)

real_user32 = overlay_module.window_switcher.user32


class _FakeUser32:
    @staticmethod
    def GetForegroundWindow():
        return 123

    @staticmethod
    def MonitorFromWindow(_hwnd, _flags):
        return 456

    @staticmethod
    def GetMonitorInfoW(_monitor, info_pointer):
        info_pointer._obj.szDevice = r"\\.\DISPLAY_TEST"
        return True

    @staticmethod
    def GetWindowRect(_hwnd, rect_pointer):
        rect = rect_pointer._obj
        rect.left, rect.top = -2560, -200
        rect.right, rect.bottom = 0, 1240
        return True


overlay_module.window_switcher.user32 = _FakeUser32()
check("reads the foreground monitor's native display name",
      overlay_module.window_switcher.foreground_monitor_name() == r"\\.\DISPLAY_TEST")
check("preserves negative native coordinates in the fallback center",
      overlay_module.window_switcher.foreground_window_center() == (-1280, 520))
overlay_module.window_switcher.user32 = real_user32


class _ScreenGeometry:
    class _GeometrySignal:
        def connect(self, _callback):
            pass

    def __init__(self, rect):
        self._rect = QRect(rect)
        self.geometryChanged = self._GeometrySignal()

    def geometry(self):
        return QRect(self._rect)


selected_screen = [_ScreenGeometry(QRect(-1920, -120, 1920, 1080))]
selection_count = [0]


def select_screen():
    selection_count[0] += 1
    return selected_screen[0]


overlay_module.window_switcher.force_foreground = lambda _hwnd: None
win = OverlayWindow(get_battery=lambda: 88, screen_selector=select_screen)

print("\n[screen geometry refresh]")
win._refresh_screen_geometry()
check("accepts negative coordinates from a monitor left of the primary",
      win.geometry() == QRect(-1920, -120, 1920, 1080),
      f"(got {win.geometry()})")

selected_screen[0] = _ScreenGeometry(QRect(2560, 200, 1280, 720))
before_open = selection_count[0]
win.open_menu()
check("open_menu reselects the target screen",
      selection_count[0] == before_open + 1,
      f"(selector calls before={before_open}, after={selection_count[0]})")
check("uses the new monitor's full geometry",
      win.geometry() == QRect(2560, 200, 1280, 720),
      f"(got {win.geometry()})")

refreshes = []
real_refresh = win._refresh_screen_geometry
win._refresh_screen_geometry = lambda: refreshes.append("refresh")
hotplug_screen = _ScreenGeometry(QRect(-1280, 0, 1280, 720))
win._on_screen_added(hotplug_screen)
app.processEvents()
check("replugging a display schedules a live geometry refresh",
      hotplug_screen in win._watched_screens and refreshes == ["refresh"],
      f"(watched={hotplug_screen in win._watched_screens}, refreshes={refreshes})")
win._on_screen_removed(hotplug_screen)
app.processEvents()
check("unplugging a display schedules a live geometry refresh",
      hotplug_screen not in win._watched_screens and refreshes == ["refresh", "refresh"],
      f"(watched={hotplug_screen in win._watched_screens}, refreshes={refreshes})")
win._refresh_screen_geometry = real_refresh

# Open Music (anchor="left", preferred width 1500). Its content does not
# matter for horizontal geometry, so Spotify does not need to be stubbed.
win.handle_button("right")   # tray index 1 == music
win.handle_button("cross")
app.processEvents()

panel = win._active_panel
check("Music panel is open and left-anchored",
      panel is not None and getattr(panel, "anchor", None) == "left",
      f"(got {type(panel).__name__ if panel else None}, "
      f"anchor={getattr(panel, 'anchor', None)!r})")
check("Music keeps a 1500px preferred width", panel.preferred_width == 1500,
      f"(got {panel.preferred_width})")

MARGIN = overlay_module._LEFT_ANCHOR_MARGIN
EDGE = overlay_module._PANEL_EDGE_MARGIN
TOP = overlay_module._PANEL_TOP_MARGIN
PANEL_W = panel.preferred_width


def relayout_at(width: int, height: int = 1000):
    win.setGeometry(0, 0, width, height)
    win._relayout()
    app.processEvents()
    left_gap = panel.x()
    right_gap = width - (panel.x() + panel.width())
    return left_gap, right_gap


print(f"\n[left-anchored panel, margin={MARGIN}px, preferred width={PANEL_W}px]")

print("\n  -- wide screen: mockup offset preserved --")
left, right = relayout_at(1920)
check("holds the mockup's left margin on a wide screen", left == MARGIN,
      f"(left={left}, want {MARGIN})")
check("has real margin on the right too", right > 0, f"(right={right})")

print("\n  -- the reported case: 1707px screen --")
left, right = relayout_at(1707)
check("no longer flush against the right edge", right > 0,
      f"(right={right} — old formula gave 0 here)")
check("left and right gaps are close to balanced",
      abs(left - right) <= 1, f"(left={left}, right={right})")
check("neither gap is negative", left >= 0 and right >= 0,
      f"(left={left}, right={right})")

print("\n  -- 1920x1080 at 150% scaling: 1280x720 logical --")
left, right = relayout_at(1280, 720)
check("shrinks Music to the logical viewport",
      panel.width() == 1280 - 2 * EDGE,
      f"(got {panel.width()})")
check("keeps equal edge margins instead of overflowing",
      left == EDGE and right == EDGE,
      f"(left={left}, right={right})")
check("keeps the panel vertically contained at 720 logical pixels",
      panel.y() >= TOP and panel.y() + panel.height() <= 720,
      f"(panel={panel.geometry()})")

print("\n  -- 1920x1080 at 200% scaling: 960x540 logical --")
left, right = relayout_at(960, 540)
check("fits the narrower 200%-style logical viewport",
      panel.width() == 960 - 2 * EDGE and left == EDGE and right == EDGE,
      f"(panel={panel.geometry()}, left={left}, right={right})")
check("keeps the panel vertically contained at 540 logical pixels",
      panel.y() >= TOP and panel.y() + panel.height() <= 540,
      f"(panel={panel.geometry()})")

print("\n  -- moving back to a larger monitor --")
left, right = relayout_at(1920, 1080)
check("restores Music's preferred width", panel.width() == PANEL_W,
      f"(got {panel.width()})")
check("restores the left anchor", left == MARGIN, f"(left={left})")

print("\n[Music's tall row list yields height before the panel clips]")
panel._view_stack.setCurrentWidget(panel._library_view)
scroll = panel._library_scroll
rows = panel._library_rows_container
for i in range(20):
    rows.addWidget(QLabel(f"Row {i}"))
fit_scroll_to_content(scroll)
panel._view_stack.setFixedHeight(panel._library_view.sizeHint().height())
panel.body.invalidate()
panel.layout().invalidate()
preferred_scroll_height = scroll.height()
relayout_at(960, 540)
check("Music stays between the top margin and tray content boundary",
      panel.y() >= TOP and panel.y() + panel.height() <= 540,
      f"(panel={panel.geometry()})")
check("the visible row list shrinks to make that possible",
      scroll.height() < preferred_scroll_height,
      f"(scroll={scroll.height()}, preferred={preferred_scroll_height})")
scroll_bottom = scroll.mapTo(panel, QPoint(0, scroll.height())).y()
check("the shrunken row list remains inside the visible panel",
      scroll_bottom <= panel.height(),
      f"(scroll bottom={scroll_bottom}, panel height={panel.height()})")
relayout_at(1920, 1080)
check("the row list restores on a taller display",
      scroll.height() == preferred_scroll_height,
      f"(scroll={scroll.height()}, preferred={preferred_scroll_height})")

print("\n[center-anchored panels remain centered]")
win.close_menu()
selected_screen[0] = _ScreenGeometry(QRect(0, 0, 1920, 1080))
win.open_menu()
for _ in range(3):
    win.handle_button("right")   # power (anchor="center")
win.handle_button("cross")
app.processEvents()
power = win._active_panel
check("Power panel uses the default center anchor",
      power is not None and getattr(power, "anchor", "center") == "center",
      f"(got {type(power).__name__ if power else None})")
win.setGeometry(0, 0, 1920, 1000)
win._relayout()
app.processEvents()
expected_center_x = (1920 - power.width()) // 2
check("center anchor still centers exactly", power.x() == expected_center_x,
      f"(got {power.x()}, want {expected_center_x})")

win.close_menu()

finish()
