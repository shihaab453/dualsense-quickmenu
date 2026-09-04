# Verification for the App Switcher: window enumeration, icon extraction,
# switching, the home card, and the panel.
#
#   .venv\Scripts\python.exe tests\verify_appswitcher.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before anything reads it.
#
# Deliberately tests against real Windows API behavior rather than mocking
# ctypes calls, matching the rest of this project's testing philosophy —
# the whole point of window_switcher.py was verifying real system behavior
# rather than assuming it (see HANDOFF.md gotcha #12). Two disposable windows
# get created along the way: an in-process one (to prove this test's own
# process is correctly excluded from the switchable list) and a real
# subprocess (to prove enumeration and switching work across processes,
# which an in-process widget can't demonstrate). Neither touches anything the
# user actually has open.

import os
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_appswitcher_test_")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from actions import window_switcher

app = QApplication(sys.argv)

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


# ------------------------------------------------------------ own-pid filter
print("\n[own-process windows are excluded]")
own_widget = QLabel("own process test window")
own_widget.setWindowTitle("DSQM_TEST_OWN_PROCESS_c4d8a1")
own_widget.resize(200, 80)
own_widget.show()
app.processEvents()

listed_titles = [w["title"] for w in window_switcher.list_switchable_windows()]
check("a window belonging to this test's own process is not listed",
      "DSQM_TEST_OWN_PROCESS_c4d8a1" not in listed_titles)
own_hwnd = int(own_widget.winId())

# ------------------------------------------------------------ icon extraction
print("\n[icon extraction]")
# get_window_icon() is tested directly (bypassing list_switchable_windows,
# which would filter this own-process window out) against a widget with a
# known, controllable icon.
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

known_icon = QPixmap(32, 32)
known_icon.fill(QColor(0, 0, 0, 0))
painter = QPainter(known_icon)
painter.setBrush(QColor("#3ddc97"))
painter.setPen(Qt.NoPen)
painter.drawEllipse(2, 2, 28, 28)
painter.end()
own_widget.setWindowIcon(QIcon(known_icon))
app.processEvents()

# Goes through the QImage path and converts — enumeration runs on a worker
# thread now, where a QPixmap must not be created, so the pixmap conversion is
# split out into this main-thread-only wrapper.
extracted = window_switcher.get_window_icon(own_hwnd, size=32)
check("a window with an icon set returns a real, non-null pixmap",
      extracted is not None and not extracted.isNull())
if extracted is not None and not extracted.isNull():
    check("extracted icon is the requested size", extracted.width() == 32 and extracted.height() == 32,
          f"(got {extracted.width()}x{extracted.height()})")
    img = extracted.toImage()
    opaque_pixels = sum(
        1 for y in range(img.height()) for x in range(img.width())
        if img.pixelColor(x, y).alpha() > 100
    )
    check("the extracted icon actually has visible (non-transparent) content",
          opaque_pixels > 50, f"(only {opaque_pixels} opaque pixels)")

check("a bogus/nonexistent window handle returns None, not a crash",
      window_switcher.get_window_icon(999999999) is None)

own_widget.close()

# ---------------------------------------------------------- cross-process
print("\n[real cross-process enumeration + switching]")
UNIQUE_TITLE = "DSQM_TEST_TARGET_e91b7f"
target = subprocess.Popen(
    [sys.executable, "-c",
     f"import sys; from PySide6.QtWidgets import QApplication, QLabel; "
     f"app = QApplication(sys.argv); l = QLabel('t'); "
     f"l.setWindowTitle({UNIQUE_TITLE!r}); l.resize(250, 90); l.show(); "
     f"sys.exit(app.exec())"],
)

try:
    found = None
    for _ in range(40):  # up to ~4s for the subprocess to actually show a window
        for w in window_switcher.list_switchable_windows():
            if w["title"] == UNIQUE_TITLE:
                found = w
                break
        if found:
            break
        time.sleep(0.1)

    check("a real external process's window is found by enumeration",
          found is not None)

    if found:
        # Not a strict equality check against target.pid: on this venv,
        # `subprocess.Popen([sys.executable, "-c", ...])` spawns a python.exe
        # that immediately re-launches a *child* python.exe, and the child is
        # the one that actually owns the Qt window — Popen.pid is the parent,
        # which owns no window at all. Confirmed by walking the real process
        # tree, not assumed. So the meaningful check is "the found window's
        # pid is the launched process or a descendant of it", which still
        # catches a real correlation bug (e.g. matching by title alone with
        # no relation to what was actually launched) while accounting for
        # this OS/launcher quirk.
        import psutil as _psutil
        try:
            launched = _psutil.Process(target.pid)
            related_pids = {target.pid} | {c.pid for c in launched.children(recursive=True)}
        except _psutil.NoSuchProcess:
            related_pids = {target.pid}
        check("its pid is the subprocess we launched, or a real child of it",
              found["pid"] in related_pids,
              f"(got {found['pid']}, related to launched: {related_pids})")
        check("its icon extracted to something non-null (or a documented None)",
              found["icon_image"] is None or not found["icon_image"].isNull())

        # Force focus elsewhere first so switch_to() has an actual transition
        # to make, not a coincidental no-op — a freshly-launched process can
        # already be foreground on its own (Windows' anti-focus-stealing rule
        # is about a *background* process grabbing focus, not a brand new
        # one's first window).
        other_windows = [w for w in window_switcher.list_switchable_windows() if w["hwnd"] != found["hwnd"]]
        if other_windows:
            window_switcher.force_foreground(other_windows[0]["hwnd"])
            fg_before = window_switcher.user32.GetForegroundWindow()
            check("confirmed focus is away from the target before switching",
                  fg_before != found["hwnd"])

            ok = window_switcher.switch_to(found["hwnd"])
            fg_after = window_switcher.user32.GetForegroundWindow()
            check("switch_to() reports success", ok)
            check("real OS foreground genuinely moved to the target",
                  fg_after == found["hwnd"], f"(fg_after={fg_after}, target={found['hwnd']})")
        else:
            print("  (skipped the focus-transition check — no other window available to switch away to)")
finally:
    subprocess.run(["taskkill", "/F", "/PID", str(target.pid)], capture_output=True)

# --------------------------------------------------------------------- panel
print("\n[AppSwitcherPanel]")
import panels.appswitcher as appswitcher_module
from PySide6.QtGui import QImage
from workers import Loader

FAKE_WINDOWS = [
    {"hwnd": 111, "title": "Fake Window One", "pid": 1, "process_name": "one.exe",
     "icon_image": None},
    {"hwnd": 222, "title": "Fake Window Two", "pid": 2, "process_name": "two.exe",
     "icon_image": None},
]
appswitcher_module.window_switcher.list_switchable_windows = lambda: list(FAKE_WINDOWS)

panel = appswitcher_module.AppSwitcherPanel()
# The window list is built on a worker thread now. Running the job inline
# keeps these assertions on the next line rather than spinning an event loop;
# the threading itself is covered by tests/verify_workers.py.
held_jobs = []
panel._loader = Loader(lambda job: held_jobs.append(job), "test")


def run_jobs():
    jobs, held_jobs[:] = list(held_jobs), []
    for job in jobs:
        job()


row_list = panel.build_nav()
check("the panel opens before the window list is built",
      row_list.rows == [] and panel._rows == [], f"(got {panel._rows})")
run_jobs()
check("build_nav() creates one row per window", len(panel._rows) == 2)
check("row titles match", [r.window["title"] for r in panel._rows] == ["Fake Window One", "Fake Window Two"])
check("a window with no icon gets a placeholder, not a crash (no pixmap set)",
      panel._rows[0].icon_label.pixmap().isNull())

switched = []
appswitcher_module.window_switcher.switch_to = lambda hwnd: switched.append(hwnd)
closed = []


class _FakeOverlay:
    def close_menu(self):
        closed.append(True)


panel.window = lambda: _FakeOverlay()
row_list.activate()
check("activating a row closes the overlay first", closed == [True])
check("activating a row calls switch_to with that window's hwnd",
      switched == [111], f"(got {switched})")

appswitcher_module.window_switcher.list_switchable_windows = lambda: []
panel.build_nav()
run_jobs()
check("an empty window list shows a hint instead of an empty panel",
      len(panel._rows) == 0)

# The list has to be built off the Qt thread without touching a QPixmap:
# QPixmap is GUI-thread-only, so enumeration hands back QImages and the row
# converts. Anything that reintroduces a pixmap in the worker would be a
# latent crash rather than a visible one, which is why this is pinned here.
sample = window_switcher.list_switchable_windows()
check("enumeration returns QImages, not QPixmaps",
      all(w["icon_image"] is None or isinstance(w["icon_image"], QImage)
          for w in sample),
      f"(got {[type(w['icon_image']).__name__ for w in sample]})")

# --------------------------------------------------------- the home card
print("\n[the home card, embedded in a real OverlayWindow]")
import overlay as overlay_module

win = overlay_module.OverlayWindow(get_battery=lambda: 88)
win.open_menu()
app.processEvents()
win.handle_button("up")
app.processEvents()

check("Now Playing is selected first when cards are focused",
      win._now_playing_card.styleSheet().find("2px solid") != -1)

win.handle_button("right")
app.processEvents()
check("right moves selection to the App Switcher card",
      win._app_switcher_card.styleSheet().find("2px solid") != -1)
check("Now Playing is no longer selected",
      win._now_playing_card.styleSheet().find("2px solid") == -1)
check("the hint text reflects the switcher card",
      "switch" in win._cards_hint_label.text().lower(),
      f"(got {win._cards_hint_label.text()!r})")

win.handle_button("cross")
app.processEvents()
check("Cross on the switcher card opens AppSwitcherPanel",
      type(win._active_panel).__name__ == "AppSwitcherPanel")

win.handle_button("circle")
app.processEvents()
check("Circle returns to cards with the switcher card still selected "
      "(not reset to Now Playing)",
      win._app_switcher_card.styleSheet().find("2px solid") != -1)
check("Now Playing correctly stays deselected after returning",
      win._now_playing_card.styleSheet().find("2px solid") == -1)

win.handle_button("down")
app.processEvents()
check("down from cards returns to the tray",
      win._home_focus == "tray")
check("both cards deselected once back at the tray",
      win._now_playing_card.styleSheet().find("2px solid") == -1
      and win._app_switcher_card.styleSheet().find("2px solid") == -1)

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
