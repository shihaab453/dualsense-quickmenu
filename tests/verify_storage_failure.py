# Verification for what happens when %APPDATA% can't be written to.
#
#   .venv\Scripts\python.exe tests\verify_storage_failure.py
#
# Exits non-zero if anything fails. Why this suite exists: settings.save() used
# to call os.makedirs() and open() with no error handling at all, and
# settings.mark_launched() runs during startup before app.exec(). On a machine
# whose data folder is unwritable - locked-down permissions, a full disk, a
# redirected AppData pointing at a folder that is gone - the very first launch
# raised OSError and the process ended. Under pythonw.exe that is a silent
# failure: no console, no window, and at the time no log either.
#
# Everything here runs against throwaway temp paths, like every other suite.
# The two ways of making a location unwritable are both real filesystem states
# rather than a monkeypatched os module, because the point is to exercise the
# same OSError the operating system would actually raise:
#
#   1. A data folder whose *parent* is a file. os.makedirs() cannot create it.
#      This is the "the folder cannot even be made" case.
#   2. A perfectly good folder in which settings.json.tmp already exists as a
#      *directory*. The folder is writable, the write itself is not. This is
#      the "it exists but the write fails" case, and it is the one that only
#      shows up after the app has been running a while.
#
# Follows the project's standalone-script testing pattern (see HANDOFF.md).

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _harness import check, finish

import settings

# A real, writable folder. Set before logs.setup() so this suite's own log
# output lands in the temp folder rather than in the user's real one - and so
# the tracebacks settings.py records on a failed write go to a file instead of
# burying the PASS/FAIL lines in stderr.
_GOOD = tempfile.mkdtemp(prefix="dsqm_storage_ok_")
settings.data_dir = lambda: _GOOD

import logs

logs.setup()

# Case 1: a folder that can never be created, because its parent is a file.
_BLOCKER = os.path.join(tempfile.mkdtemp(prefix="dsqm_storage_bad_"), "not-a-folder")
with open(_BLOCKER, "w", encoding="utf-8") as f:
    f.write("This is a file, so nothing can be created inside it.")
_UNWRITABLE = os.path.join(_BLOCKER, "DualSenseQuickMenu")


def _point_at(path: str) -> None:
    settings.data_dir = lambda: path


# ------------------------------------------------- a folder that cannot exist
print("\n[data folder cannot be created]")
_point_at(_UNWRITABLE)

reason = settings.check_writable()
check("check_writable reports a reason", bool(reason), f"(got {reason!r})")
check(
    "the reason names the folder, so the user knows where to look",
    _UNWRITABLE in reason,
)
check("storage_error repeats it", settings.storage_error() == reason)

raised = None
try:
    saved = settings.save(settings.load())
except OSError as exc:  # the bug this suite exists for
    raised = exc
    saved = None
check("save() does not raise", raised is None, f"(raised {raised!r})")
check("save() reports the failure instead", saved is False, f"(got {saved!r})")

# The point of the in-memory fallback: the app is usable for this session.
check(
    "set_spotify_client_id reports it wasn't written",
    settings.set_spotify_client_id("0a1b2c3d4e5f60718293a4b5c6d7e8f9") is False,
)
check(
    "but the client ID is in force for this session",
    settings.get_spotify_client_id() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
)
check(
    "mark_launched reports it wasn't written",
    settings.mark_launched() is False,
)
check(
    "and first run doesn't repeat while the app is open",
    settings.is_first_run() is False,
)
check("nothing was created on disk", not os.path.exists(_UNWRITABLE))

raised = None
try:
    settings.load()
except OSError as exc:
    raised = exc
check("load() still works", raised is None, f"(raised {raised!r})")

# ------------------------------------------------------- a writable folder
print("\n[a writable folder]")
_point_at(_GOOD)

check("check_writable is happy", settings.check_writable() == "")
check("the probe file is cleaned up", not os.path.exists(os.path.join(_GOOD, ".write-check")))

check(
    "saving works again",
    settings.set_spotify_client_id("ffffffffffffffffffffffffffffffff") is True,
)
check("storage_error is cleared", settings.storage_error() == "")
check("settings.json exists", os.path.exists(settings.settings_path()))
with open(settings.settings_path(), "r", encoding="utf-8") as f:
    on_disk = json.load(f)
check(
    "and holds the value",
    on_disk.get("spotify_client_id") == "ffffffffffffffffffffffffffffffff",
    f"(got {on_disk.get('spotify_client_id')!r})",
)

# --------------------------------------------- the folder is fine, the write isn't
print("\n[folder writable, write blocked]")
# settings.save() writes settings.json.tmp and renames it. A directory sitting
# on that exact name makes the write fail with the folder itself untouched.
_TMP_BLOCKER = settings.settings_path() + ".tmp"
os.makedirs(_TMP_BLOCKER, exist_ok=True)

check(
    "a blocked write is reported, not raised",
    settings.set_hotkey_shortcut("ctrl+alt+m") is False,
)
check("storage_error is set again", bool(settings.storage_error()))
check(
    "the new shortcut is in force this session",
    settings.get_hotkey_shortcut() == "ctrl+alt+m",
)
with open(settings.settings_path(), "r", encoding="utf-8") as f:
    on_disk = json.load(f)
check(
    "but the file on disk is untouched",
    on_disk.get("hotkey_shortcut", "auto") == "auto",
    f"(got {on_disk.get('hotkey_shortcut', 'auto')!r})",
)
check(
    "check_writable still passes, because the folder really is writable",
    settings.check_writable() == "",
)

os.rmdir(_TMP_BLOCKER)
check("once unblocked, the save works", settings.set_hotkey_shortcut("ctrl+alt+m") is True)
with open(settings.settings_path(), "r", encoding="utf-8") as f:
    on_disk = json.load(f)
check(
    "and reaches disk",
    on_disk.get("hotkey_shortcut") == "ctrl+alt+m",
    f"(got {on_disk.get('hotkey_shortcut')!r})",
)
check("storage_error is cleared", settings.storage_error() == "")

# ------------------------------------------------------------- Spotify login
print("\n[spotify auth on an unwritable folder]")
from actions import spotify_client as sp

_point_at(_UNWRITABLE)
settings.set_spotify_client_id("0a1b2c3d4e5f60718293a4b5c6d7e8f9")
raised = None
try:
    # Building the auth manager creates the folder the token is cached in.
    # Nothing here goes near the network: this is a constructor.
    sp._auth_manager()
except OSError as exc:
    raised = exc
check(
    "building the Spotify auth manager doesn't raise",
    raised is None,
    f"(raised {raised!r})",
)

# ------------------------------------------------------- the Settings window
print("\n[settings window]")
# Neutered before the window is built: reload() asks whether there is a cached
# token, and this suite is not about Spotify. See HANDOFF.md's note on patching
# module attributes before construction.
sp.is_logged_in = lambda: False
sp.has_cached_token = lambda: False

from PySide6.QtWidgets import QApplication
import settings_window

app = QApplication(sys.argv)

_point_at(_GOOD)
window = settings_window.SettingsWindow()
window.reload()
# isHidden(), not isVisible(): the window itself is never shown in this suite,
# and every child of an unshown window reports isVisible() False regardless of
# its own state. isHidden() is the widget's own flag, which is what is being
# checked here.
check("no banner when the folder is writable", window._storage_banner.isHidden())

_point_at(_UNWRITABLE)
window.reload()
check("the banner appears when it isn't", not window._storage_banner.isHidden())
check(
    "and it names the folder",
    _UNWRITABLE in window._storage_banner.text(),
    f"(got {window._storage_banner.text()[:60]!r}...)",
)

# The window must not claim a save that didn't happen.
window._hotkey_combo.setCurrentIndex(
    1 if window._hotkey_combo.count() > 1 else 0
)
window._on_hotkey_changed()
check(
    "a failed shortcut save doesn't say 'Saved'",
    "Saved" not in window._hotkey_status.text(),
    f"(got {window._hotkey_status.text()!r})",
)

window._client_id_field.setText("0a1b2c3d4e5f60718293a4b5c6d7e8f9")
window._save_client_id()
check(
    "a failed client ID save doesn't say 'Saved'",
    "Saved" not in window._spotify_status.text(),
    f"(got {window._spotify_status.text()!r})",
)
check(
    "and it says the value only lasts this session",
    "this session only" in window._spotify_status.text().lower(),
    f"(got {window._spotify_status.text()!r})",
)

# No event loop was ever started, so this exits directly rather than through
# app.exit() - see the note on finish() in _harness.py.
finish()
