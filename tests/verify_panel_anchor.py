# Verification for OverlayWindow._relayout's "left"-anchored panel
# positioning (currently only Music uses anchor="left"; every other panel
# is anchor="center" and unaffected by this).
#
#   .venv\Scripts\python.exe tests\verify_panel_anchor.py
#
# Exits non-zero if anything fails.
#
# This exists because of a real bug found on a real screen: on a 1707px-wide
# screen, the Music panel (1500px wide) sat flush against the right edge with
# a 207px gap on the left and 0px on the right — visibly lopsided. The old
# clamp `min(_LEFT_ANCHOR_MARGIN, w - panel.width())` was supposed to fall
# back to something "center-ish" once the screen was too narrow for the
# mockup's 210px left offset, but the expression it used actually flushes the
# panel's right edge against the screen's right edge instead. This asserts
# the corrected formula centers the panel in that case, using the exact
# reported width as one of the cases.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_anchor_")

from PySide6.QtWidgets import QApplication

import overlay as overlay_module
from overlay import OverlayWindow

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


app = QApplication(sys.argv)
win = OverlayWindow(get_battery=lambda: 88)

# Open Music (anchor="left", width=1500). Its content doesn't matter here —
# Panel.setFixedWidth() means panel.width() is 1500 regardless of whether
# Spotify is configured, logged in, or showing the library, so there's no
# need to stub Spotify calls just to check geometry.
win.open_menu()
win.handle_button("right")   # tray index 1 == music
win.handle_button("cross")
app.processEvents()

panel = win._active_panel
check("Music panel is open and left-anchored",
      panel is not None and getattr(panel, "anchor", None) == "left",
      f"(got {type(panel).__name__ if panel else None}, "
      f"anchor={getattr(panel, 'anchor', None)!r})")
check("Music panel width is the fixed 1500px", panel.width() == 1500,
      f"(got {panel.width()})")

MARGIN = overlay_module._LEFT_ANCHOR_MARGIN
PANEL_W = panel.width()


def relayout_at(width: int, height: int = 1000):
    win.setGeometry(0, 0, width, height)
    win._relayout()
    app.processEvents()
    left_gap = panel.x()
    right_gap = width - (panel.x() + panel.width())
    return left_gap, right_gap


print(f"\n[left-anchored panel, margin={MARGIN}px, panel width={PANEL_W}px]")

print("\n  -- wide screen: mockup offset preserved --")
left, right = relayout_at(1920)
check("holds the mockup's left margin on a wide screen", left == MARGIN,
      f"(left={left}, want {MARGIN})")
check("has real margin on the right too", right > 0, f"(right={right})")

print("\n  -- exactly at the boundary --")
boundary = PANEL_W + MARGIN
left, right = relayout_at(boundary)
check(f"at exactly panel+margin ({boundary}px), still uses the margin",
      left == MARGIN, f"(left={left})")
check("right gap is zero here, not negative", right == 0, f"(right={right})")

print("\n  -- the reported case: 1707px screen --")
left, right = relayout_at(1707)
check("no longer flush against the right edge", right > 0,
      f"(right={right} — old formula gave 0 here)")
check("left and right gaps are close to balanced",
      abs(left - right) <= 1, f"(left={left}, right={right})")
check("neither gap is negative", left >= 0 and right >= 0,
      f"(left={left}, right={right})")

print("\n  -- one pixel narrower than the boundary --")
left, right = relayout_at(boundary - 1)
check("centers rather than using the full margin", left < MARGIN,
      f"(left={left})")
check("both gaps stay non-negative", left >= 0 and right >= 0,
      f"(left={left}, right={right})")

print("\n  -- panel wider than the screen (extreme case) --")
left, right = relayout_at(PANEL_W - 400)
check("never moves the panel to a negative x", left == 0, f"(left={left})")

print("\n[center-anchored panels are unaffected]")
win.close_menu()
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

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
else:
    print("All checks passed.")
print("=" * 60)
sys.exit(1 if failures else 0)
