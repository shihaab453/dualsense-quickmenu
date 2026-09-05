# Verification for the Sound panel telling the truth about what it did.
#
#   .venv\Scripts\python.exe tests\verify_sound.py
#
# Exits non-zero if anything fails.
#
# Every control on this panel is a call into pycaw, and every one of them can
# fail: no microphone plugged in, a device removed while the menu is open, an
# exclusive-mode application holding the endpoint. There are two halves to
# getting that right and they were fixed at different times.
#
# The *read* half came first: a level that cannot be read must not render as
# 0%, and a toggle that cannot be read must not render as "off", because in
# both cases the user takes a state the app never established as fact.
#
# The *write* half is what this suite was added for. A failed
# SetMasterVolumeLevelScalar or SetMute was caught, logged, and followed by a
# refresh that repainted the value already on screen. Nothing on screen
# changed, which is exactly what a press that did nothing looks like — and for
# the mic mute, the user walks away believing they are muted while the
# microphone is still live. Logging is not telling the user.
#
# pycaw is never touched here: the rows take their accessors as arguments, so
# a fake that raises on demand is enough and this runs anywhere.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_sound_")

from PySide6.QtWidgets import QApplication

import panels.sound as sound

app = QApplication(sys.argv)


class Device:
    """A volume level and a mute switch, with either side made to fail on
    demand. `writes` counts attempts, so a check can tell "refused to try"
    apart from "tried and failed"."""

    def __init__(self):
        self.percent = 40
        self.muted = False
        self.read_fails = False
        self.write_fails = False
        self.writes = 0

    def get_percent(self):
        if self.read_fails:
            raise OSError("device gone")
        return self.percent

    def change_percent(self, delta):
        self.writes += 1
        if self.write_fails:
            raise OSError("endpoint held by another application")
        self.percent = max(0, min(100, self.percent + delta))
        return self.percent

    def get_muted(self):
        if self.read_fails:
            raise OSError("device gone")
        return self.muted

    def toggle_muted(self):
        self.writes += 1
        if self.write_fails:
            raise OSError("endpoint held by another application")
        self.muted = not self.muted
        return self.muted


def volume_row(device):
    return sound._VolumeRow(
        "sound", "Master Volume", device.get_percent, device.change_percent
    )


def toggle_row(device):
    return sound._ToggleRow(
        "mic", "Mute Microphone", device.get_muted, device.toggle_muted
    )


def status_of(row):
    """What the row's failure note says, or None when it isn't showing."""
    return row._status.text() if row._status.isVisible() else None


print("\n[a volume change that works says nothing]")
device = Device()
row = volume_row(device)
row.show()
app.processEvents()
row.adjust(10)
check("the level moved", device.percent == 50, f"(got {device.percent})")
check("and it is what the row shows", row._percent.text() == "50%",
      f"(got {row._percent.text()!r})")
check("no failure note appears on a press that worked",
      status_of(row) is None, f"(got {status_of(row)!r})")

print("\n[a volume change that fails does not look like one that worked]")
device.write_fails = True
row.adjust(10)
check("the level really didn't move", device.percent == 50, f"(got {device.percent})")
check("the row still shows the true level, not the one asked for",
      row._percent.text() == "50%", f"(got {row._percent.text()!r})")
check("and the row says the press didn't take", status_of(row) is not None,
      f"(got {status_of(row)!r})")

print("\n[the note clears once something works again]")
device.write_fails = False
row.adjust(-10)
check("the level moved", device.percent == 40, f"(got {device.percent})")
check("and the failure note is gone", status_of(row) is None,
      f"(got {status_of(row)!r})")

print("\n[reopening the panel is a clean slate]")
# build_nav() refreshes every row, which is the only thing that clears a note
# left behind by a press the user has since navigated away from.
device.write_fails = True
row.adjust(10)
check("sanity: the note is showing", status_of(row) is not None)
row.refresh()
check("a refresh clears it", status_of(row) is None, f"(got {status_of(row)!r})")
device.write_fails = False

print("\n[a mic mute that fails does not leave the user thinking they're muted]")
device = Device()
row = toggle_row(device)
row.show()
app.processEvents()
row.toggle()
check("sanity: a working toggle mutes", device.muted is True, f"(got {device.muted})")
check("and says nothing about failing", status_of(row) is None,
      f"(got {status_of(row)!r})")

device.write_fails = True
row.toggle()
check("the mic really didn't change state", device.muted is True,
      f"(got {device.muted})")
check("the row says the switch didn't take", status_of(row) is not None,
      f"(got {status_of(row)!r})")

device.write_fails = False
row.toggle()
check("a later toggle works", device.muted is False, f"(got {device.muted})")
check("and clears the note", status_of(row) is None, f"(got {status_of(row)!r})")

print("\n[the read failures stay fixed]")
# Guards the earlier half of this work: these are what the write notes were
# built alongside, and it would be easy to disturb them.
device = Device()
device.read_fails = True
row = volume_row(device)
row.show()
app.processEvents()
check("an unreadable level shows '--', not 0%", row._percent.text() == "--",
      f"(got {row._percent.text()!r})")
check("and the row is disabled rather than looking adjustable",
      not row.isEnabled(), f"(got enabled={row.isEnabled()})")

toggle = toggle_row(device)
toggle.show()
app.processEvents()
check("an unreadable toggle is disabled rather than reading as off",
      not toggle.isEnabled(), f"(got enabled={toggle.isEnabled()})")

print("\n[a write failure is reported even when the level can still be read]")
# The two halves are independent: a device that reads fine but refuses writes
# must not be disabled (it is still adjustable in principle), and must still
# say when a press did not take.
device = Device()
device.write_fails = True
row = volume_row(device)
row.show()
app.processEvents()
row.adjust(5)
check("the row stays enabled", row.isEnabled(), f"(got enabled={row.isEnabled()})")
check("the level shown is the real one", row._percent.text() == "40%",
      f"(got {row._percent.text()!r})")
check("the write was actually attempted", device.writes == 1,
      f"(got {device.writes})")
check("and the failure is on screen", status_of(row) is not None,
      f"(got {status_of(row)!r})")

finish()
