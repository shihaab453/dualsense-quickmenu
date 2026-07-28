# Verification for linking displayed metadata back to Spotify.
#
#   .venv\Scripts\python.exe tests\verify_spotify_links.py
#
# Exits non-zero if anything fails.
#
# Spotify's design guidelines require that displayed metadata always links back
# to the service, and that users are sent to the Spotify application where it's
# available. So these assert the spotify: URI is preferred over the https one,
# and — the part that's easy to get wrong — that Now Playing names Spotify only
# when the track genuinely came from Spotify, not when it came from the Windows
# media session fallback, which reports whatever any player is doing. Claiming
# another service's track is "on Spotify" would be worse than not attributing
# it at all.
#
# Nothing here opens a real browser: QDesktopServices.openUrl is stubbed and
# records what it was asked to open.

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_links_")

import actions.spotify_client as sp

SPOTIFY_TRACK = {
    "id": "4cOdK2wGLETKBW3PvgPWqT",
    "name": "Never Gonna Give You Up",
    "uri": "spotify:track:4cOdK2wGLETKBW3PvgPWqT",
    "artists": [{"name": "Rick Astley"}],
    "album": {"images": [{"url": "http://example.invalid/art.jpg"}]},
    "external_urls": {"spotify": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"},
}

sp.is_configured = lambda: True
sp.is_logged_in = lambda: True
sp.get_current_playback = lambda: {"item": SPOTIFY_TRACK, "is_playing": True}

from PySide6.QtCore import QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

import panels.base as base

opened = []
QDesktopServices.openUrl = lambda url: (opened.append(url.toString()), True)[1]

from overlay import OverlayWindow

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


app = QApplication(sys.argv)
overlay = OverlayWindow(get_battery=lambda: 88)

print("\n[link selection]")
app_uri, web_url = sp.links_for(SPOTIFY_TRACK)
check("prefers the spotify: app URI", app_uri == SPOTIFY_TRACK["uri"], f"(got {app_uri})")
check("keeps the https fallback", web_url == SPOTIFY_TRACK["external_urls"]["spotify"])
check("handles an item with no links", sp.links_for({}) == (None, None))
check("handles a non-dict safely", sp.links_for(None) == (None, None))


def open_now_playing():
    overlay.open_menu()
    overlay.handle_button("up")      # focus the home cards
    overlay.handle_button("cross")   # open Now Playing
    QTimer.singleShot(400, check_now_playing_spotify)


def check_now_playing_spotify():
    panel = overlay._active_panel
    print("\n[Now Playing — track came from Spotify]")
    check("heading names Spotify", panel.heading.text() == "Now playing on Spotify",
          f"(got {panel.heading.text()!r})")
    check("link row is visible", panel._open_row.isVisible())

    opened.clear()
    overlay.handle_button("cross")   # activate the link row
    check("opens the spotify: app URI first", opened[:1] == [SPOTIFY_TRACK["uri"]],
          f"(opened={opened})")
    check("overlay closed so the app isn't hidden behind it", not overlay.isVisible())
    QTimer.singleShot(50, check_now_playing_fallback)


def check_now_playing_fallback():
    # Now simulate Spotify being unavailable, so the panel falls back to the
    # Windows media session showing some other player's track.
    sp.get_current_playback = lambda: None
    import actions.now_playing as np
    np.get = lambda: {"title": "Some Podcast", "artist": "Not Spotify"}

    overlay.open_menu()
    overlay.handle_button("up")
    overlay.handle_button("cross")
    QTimer.singleShot(400, after_fallback)


def after_fallback():
    panel = overlay._active_panel
    print("\n[Now Playing — fallback to another player]")
    check("heading does NOT claim Spotify", panel.heading.text() == "Now playing",
          f"(got {panel.heading.text()!r})")
    check("link row is hidden", not panel._open_row.isVisible())
    check("the other player's track still shows",
          "Some Podcast" in panel._song_label.text(),
          f"(got {panel._song_label.text()!r})")
    overlay.close_menu()
    QTimer.singleShot(50, check_music_detail)


def check_music_detail():
    print("\n[Music Detail view]")
    sp.get_current_playback = lambda: {"item": SPOTIFY_TRACK, "is_playing": True}
    sp.get_liked_songs_total = lambda: 1
    sp.get_playlists = lambda limit=6: []
    sp.get_liked_songs = lambda limit=20: [SPOTIFY_TRACK]
    sp.start_playback = lambda **kw: None
    sp.is_liked = lambda tid: False

    overlay.open_menu()
    overlay.handle_button("right")
    overlay.handle_button("right")   # music
    overlay.handle_button("cross")
    QTimer.singleShot(300, drill_to_detail)


def drill_to_detail():
    overlay.handle_button("cross")   # Liked Songs -> Songs
    QTimer.singleShot(300, open_track)


def open_track():
    overlay.handle_button("cross")   # first track -> Detail
    QTimer.singleShot(400, after_detail)


def after_detail():
    panel = overlay._active_panel
    check("detail view has an open-in-Spotify tile", hasattr(panel, "_open_tile"))
    check("it's the last control in the row", panel._tiles[-1] is panel._open_tile)
    icon = panel._open_tile.pixmap().toImage()
    visible = sum(
        1
        for y in range(icon.height())
        for x in range(icon.width())
        if icon.pixelColor(x, y).alpha() > 0
    )
    check("its icon renders (not blank)", visible > 0, f"{visible} visible pixels")

    opened.clear()
    panel._open_current_in_spotify()
    check("opens the track's spotify: URI", opened[:1] == [SPOTIFY_TRACK["uri"]],
          f"(opened={opened})")
    finish()


def finish():
    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
    else:
        print("All checks passed.")
    print("=" * 60)
    app.exit(1 if failures else 0)


QTimer.singleShot(300, open_now_playing)
sys.exit(app.exec())
