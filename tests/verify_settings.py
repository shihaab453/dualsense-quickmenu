# Verification for the settings store, the Settings window, and the
# bring-your-own-Spotify-client-ID flow.
#
#   .venv\Scripts\python.exe tests\verify_settings.py
#
# Exits non-zero if anything fails. Everything is redirected into a throwaway
# temp folder by monkeypatching settings.data_dir() BEFORE anything reads it,
# so this never reads or deletes the real
# %APPDATA%\DualSenseQuickMenu\spotify_token.json — keep that property if you
# add checks here, or you'll log yourself out of Spotify every test run.
#
# Follows the project's standalone-script testing pattern (see HANDOFF.md):
# construct OverlayWindow for real, drive it via handle_button(), assert on
# real widget state.

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _harness import check, finish

import settings

_TMP = tempfile.mkdtemp(prefix="dsqm_verify_")
settings.data_dir = lambda: _TMP

import logs
import settings_window
from actions import spotify_client as sp

# This script only checks which Music rows are shown. It does not test Spotify
# authentication, so never let a temporary client ID start PKCE or open a
# browser while constructing the panel below.
sp.is_logged_in = lambda: False
sp.login_async = lambda on_done: on_done(False, "Spotify login disabled in this test")
# OverlayWindow refreshes its home-card data whenever the menu opens. Keep
# that unrelated background fetch local when this script later reports a
# configured, logged-in state to exercise Music's library view.
sp.get_now_playing_summary_async = lambda on_done: on_done(None)

# This script opens the Power panel to confirm the tray still maps to the right
# panels after the Task Switcher was removed. Neutered before any panel can be
# constructed, per HANDOFF.md's testing note — panels are built lazily, so
# patching the module attribute now is enough, and nothing here can suspend or
# reboot the machine.
from actions import power as _power

_power.sleep = lambda: None
_power.shut_down = lambda: None
_power.restart = lambda: None

# Routed into the temp dir by the data_dir patch above, so the last section can
# check that panel failures really do reach the log file.
logs.setup()

# ---------------------------------------------------------------- settings.py
print("\n[settings store]")
check("fresh install has no client ID", settings.get_spotify_client_id() == "")

settings.set_spotify_client_id("0a1b2c3d4e5f60718293a4b5c6d7e8f9")
check(
    "client ID round-trips",
    settings.get_spotify_client_id() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
)
check("settings.json written to the temp dir", os.path.exists(settings.settings_path()))

# Whitespace is the most likely paste artifact.
settings.set_spotify_client_id("  0a1b2c3d4e5f60718293a4b5c6d7e8f9\n")
check(
    "pasted whitespace is stripped",
    settings.get_spotify_client_id() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
)

# A corrupt file must degrade to defaults, not raise.
with open(settings.settings_path(), "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("corrupt settings.json falls back to defaults",
      settings.load()["spotify_client_id"] == "")
check("corrupt settings.json doesn't crash get_spotify_client_id",
      settings.get_spotify_client_id() == "")

# An unrecognised key (e.g. pinned_games, left behind by the removed Task
# Switcher) must be carried through rather than making the file unreadable.
with open(settings.settings_path(), "w", encoding="utf-8") as f:
    json.dump({"spotify_client_id": "f" * 32, "pinned_games": [{"name": "old"}]}, f)
check("a leftover key from a removed feature is harmless",
      settings.get_spotify_client_id() == "f" * 32)
settings.set_spotify_client_id("0a1b2c3d4e5f60718293a4b5c6d7e8f9")

# ------------------------------------------------------------- spotify_client
print("\n[spotify_client with no client ID]")
settings.set_spotify_client_id("")
check("is_configured() False when unset", sp.is_configured() is False)
check("is_logged_in() returns False instead of raising", sp.is_logged_in() is False)
try:
    sp._auth_manager()
    check("_auth_manager raises NotConfigured", False, "(it returned instead)")
except sp.NotConfigured:
    check("_auth_manager raises NotConfigured", True)
except Exception as e:
    check("_auth_manager raises NotConfigured", False, f"(raised {type(e).__name__})")

check("token cache path follows settings.data_dir", sp._cache_path().startswith(_TMP))
# forget_login must be safe when there's no token file at all.
sp.forget_login()
check("forget_login on a fresh install is a no-op", True)

# ------------------------------------------------------------------- Qt layer
print("\n[overlay / Music panel / Settings window]")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from overlay import OverlayWindow

app = QApplication(sys.argv)
overlay = OverlayWindow(get_battery=lambda: 88)

opened = []
settings_window.open_settings = lambda: opened.append(True)


def open_music():
    overlay.open_menu()
    overlay.handle_button("right")   # tray index 1 == music
    overlay.handle_button("cross")


def after_music_open():
    panel = overlay._active_panel
    check("Music panel opened", panel is not None and type(panel).__name__ == "MusicPanel")
    check("setup row visible when no client ID", panel._setup_row.isVisible())
    check("login row hidden when no client ID", not panel._login_row.isVisible())
    check(
        "status text explains the setup step",
        "one-time setup" in panel._status_label.text(),
        f"(got {panel._status_label.text()!r})",
    )

    # Cross on the setup row should close the overlay and open Settings.
    overlay.handle_button("cross")
    check("activating setup row opened Settings", opened == [True], f"(opened={opened})")
    check("overlay closed itself first", not overlay.isVisible())

    QTimer.singleShot(50, check_configured_state)


def check_configured_state():
    # With an ID saved, the same panel should offer the browser login instead.
    settings.set_spotify_client_id("0a1b2c3d4e5f60718293a4b5c6d7e8f9")
    check("is_configured() True once saved", sp.is_configured() is True)
    open_music()
    QTimer.singleShot(120, after_configured_open)


def after_configured_open():
    panel = overlay._active_panel
    check("login row visible once configured", panel._login_row.isVisible())
    check("setup row hidden once configured", not panel._setup_row.isVisible())
    QTimer.singleShot(20, check_library_errors_logged)


def check_library_errors_logged():
    # A failing Spotify call used to leave an empty list and no trace at all.
    # Force one and confirm it now reaches the log file.
    sp.is_logged_in = lambda: True
    sp.get_liked_songs_total = lambda: (_ for _ in ()).throw(RuntimeError("boom: liked total"))
    sp.get_playlists_page = lambda limit=20, offset=0: (_ for _ in ()).throw(
        RuntimeError("boom: playlists")
    )
    open_music()
    QTimer.singleShot(150, after_library_errors)


def after_library_errors():
    panel = overlay._active_panel
    check("library still renders despite failures", panel is not None)
    with open(logs.log_path(), "r", encoding="utf-8") as f:
        text = f.read()
    check("failed playlist fetch is logged",
          "Couldn't fetch the user's playlists" in text)
    check("failed liked-count fetch is logged",
          "Couldn't read the Liked Songs count" in text)
    check("the underlying exception is in the log", "boom: playlists" in text)
    overlay.close_menu()
    QTimer.singleShot(20, check_tray)


def check_tray():
    # The tray lost its Task Switcher icon when that feature was removed, and
    # every panel's index is derived from the same list — so this pins down that
    # the remaining icons still map to the right panels.
    import overlay as overlay_module

    keys = [key for key, _label in overlay_module._TRAY_ICONS]
    check("tray no longer offers a Task Switcher", "switcher" not in keys,
          f"(got {keys})")
    check("tray is home/music/sound/power",
          keys == ["home", "music", "sound", "power"], f"(got {keys})")

    overlay.open_menu()
    for _ in range(3):
        overlay.handle_button("right")  # last icon is now Power
    overlay.handle_button("cross")
    QTimer.singleShot(120, after_power_open)


def after_power_open():
    panel = overlay._active_panel
    check("the last tray icon opens Power", type(panel).__name__ == "PowerPanel",
          f"(got {type(panel).__name__})")
    overlay.close_menu()
    QTimer.singleShot(20, check_settings_window)


def check_settings_window():
    # Constructed directly, never shown — no window flashes on screen.
    win = settings_window.SettingsWindow()
    win.reload()
    check("settings window prefills the saved client ID",
          win._client_id_field.text() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9")
    check("settings window no longer has a games list",
          not hasattr(win, "_games_list"))
    check("hotkey choice defaults to Automatic",
          win._hotkey_combo.currentData() == "auto",
          f"(got {win._hotkey_combo.currentData()!r})")
    win._hotkey_combo.setCurrentIndex(win._hotkey_combo.findData("ctrl_alt_shift_p"))
    check("selected hotkey choice is saved",
          settings.get_hotkey_shortcut() == "ctrl_alt_shift_p")

    # A too-short ID must be rejected without touching what's stored.
    win._client_id_field.setText("not-a-real-client-id")
    win._save_client_id()
    check("short ID is rejected", "doesn't look like a client ID" in win._spotify_status.text(),
          f"(got {win._spotify_status.text()!r})")
    check("rejected ID was not saved",
          settings.get_spotify_client_id() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9")

    # Length alone is not enough: a Spotify client ID uses lowercase hex.
    win._client_id_field.setText("g" * 32)
    win._save_client_id()
    check("non-hex ID is rejected", "letters a-f" in win._spotify_status.text(),
          f"(got {win._spotify_status.text()!r})")
    check("non-hex ID was not saved",
          settings.get_spotify_client_id() == "0a1b2c3d4e5f60718293a4b5c6d7e8f9")

    # A valid-shaped, different ID saves and reports back.
    win._client_id_field.setText("ffffffffffffffffffffffffffffffff")
    win._save_client_id()
    check("valid ID saves", settings.get_spotify_client_id() == "f" * 32)
    check("save reports back to the user", "Saved" in win._spotify_status.text(),
          f"(got {win._spotify_status.text()!r})")

    # Clearing it is allowed, and turns Spotify back off.
    win._client_id_field.setText("")
    win._save_client_id()
    check("clearing the ID is allowed", settings.get_spotify_client_id() == "")
    check("cleared state reported", "off" in win._spotify_status.text(),
          f"(got {win._spotify_status.text()!r})")

    win.deleteLater()
    finish(app)


QTimer.singleShot(200, open_music)
QTimer.singleShot(400, after_music_open)
sys.exit(app.exec())
