# Verification for linking displayed metadata back to Spotify.
#
#   .venv\Scripts\python.exe tests\verify_spotify_links.py
#
# Exits non-zero if anything fails.
#
# Spotify's design guidelines require that displayed metadata always links back
# to the service, and that users are sent to the Spotify application where it's
# available. So these assert the spotify: URI is preferred over the https one,
# and - the part that's easy to get wrong - that attribution tracks the
# *content*, not which lookup produced it.
#
# That second half was rewritten on 2026-09-06 and the direction of the
# assertions flipped. It used to check that the Windows media-session fallback
# was never labelled "on Spotify", because that reading reported whatever any
# player was doing and claiming a browser's track was Spotify's would be worse
# than not attributing it at all. actions/now_playing.py now filters that
# reading by the session's owning application, so it returns Spotify or
# nothing - which makes the fallback Spotify's own content, and withholding
# the mark from it the error instead. The case that reading used to cover is
# still here, as "something that isn't Spotify is playing": the panel shows
# nothing at all rather than showing it unattributed.
#
# Nothing here opens a real browser: QDesktopServices.openUrl is stubbed and
# records what it was asked to open.

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _harness import check, finish

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_links_")

import actions.spotify_client as sp
from actions import album_art

# The assertions cover Spotify links, not artwork fetching. Returning no art
# keeps the panel's normal placeholder and prevents a background network call.
album_art.get = lambda _url, _size, _radius, callback: callback(None)
# The Detail-view navigation below should never control a real player.
sp.play_track = lambda _uri: None

SPOTIFY_PLAYLIST = {
    "id": "37i9dQZF1DXcBWIGoYBM5M",
    "name": "Today's Top Hits",
    "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
    "external_urls": {
        "spotify": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    },
    "tracks": {"total": 1},
}

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
import panels.music as music

opened = []
QDesktopServices.openUrl = lambda url: (opened.append(url.toString()), True)[1]

from overlay import OverlayWindow

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
    check("the Spotify mark is shown", panel._spotify_logo.isVisible())

    opened.clear()
    overlay.handle_button("cross")   # activate the link row
    check("opens the spotify: app URI first", opened[:1] == [SPOTIFY_TRACK["uri"]],
          f"(opened={opened})")
    check("overlay closed so the app isn't hidden behind it", not overlay.isVisible())
    QTimer.singleShot(50, check_now_playing_fallback)


def check_now_playing_fallback():
    # Spotify's Web API unavailable (logged out, expired token, no network),
    # so the panel falls back to the Windows media session. That reading is
    # Spotify-only since actions/now_playing.py started filtering by the
    # session's owning application - so what comes back here is still
    # Spotify's content, just read a different way.
    sp.get_current_playback = lambda: None
    import actions.now_playing as np
    np.get = lambda: {"title": "Nocturne in E Flat", "artist": "Chopin"}

    overlay.open_menu()
    overlay.handle_button("up")
    overlay.handle_button("cross")
    QTimer.singleShot(400, after_fallback)


def after_fallback():
    panel = overlay._active_panel
    print("\n[Now Playing - read from Spotify's Windows media session]")
    # This block asserted the opposite until 2026-09-06, and was right to:
    # the fallback could then be a browser or VLC, and crediting Spotify for
    # that is what their guidelines prohibit. Once the reading became
    # Spotify-only, withholding attribution became the error instead - it is
    # their metadata on screen either way, and metadata has to carry the mark
    # and link back regardless of which lookup produced it.
    check("the track shows", "Nocturne in E Flat" in panel._song_label.text(),
          f"(got {panel._song_label.text()!r})")
    check("heading attributes it to Spotify, because it is theirs",
          panel.heading.text() == "Now playing on Spotify",
          f"(got {panel.heading.text()!r})")
    check("the Spotify mark is shown", panel._spotify_logo.isVisible())
    check("and a link back to the service is offered",
          panel._open_row.isVisible())
    overlay.close_menu()
    QTimer.singleShot(50, check_non_spotify_player)


def check_non_spotify_player():
    # A browser or another player owning the media session. now_playing.get()
    # returns None for those (see tests/verify_media_session.py), so the panel
    # has nothing to show - which is the whole point: another service's stream
    # never reaches the screen, rather than reaching it unattributed.
    sp.get_current_playback = lambda: None
    import actions.now_playing as np
    np.get = lambda: None

    overlay.open_menu()
    overlay.handle_button("up")
    overlay.handle_button("cross")
    QTimer.singleShot(400, after_non_spotify_player)


def after_non_spotify_player():
    panel = overlay._active_panel
    print("\n[Now Playing - something that isn't Spotify is playing]")
    check("the panel says nothing is playing",
          "Nothing playing" in panel._song_label.text(),
          f"(got {panel._song_label.text()!r})")
    check("the heading makes no claim about Spotify",
          panel.heading.text() == "Now playing",
          f"(got {panel.heading.text()!r})")
    check("no mark, because there is no Spotify content to attribute",
          not panel._spotify_logo.isVisible())
    check("and no link row", not panel._open_row.isVisible())
    overlay.close_menu()
    QTimer.singleShot(50, check_music_detail)


def check_music_detail():
    print("\n[Music Detail view]")
    sp.get_current_playback = lambda: {"item": SPOTIFY_TRACK, "is_playing": True}
    sp.get_liked_songs_total = lambda: 1
    # The Music panel browses through the paged API (offset + total), so
    # stub those rather than the single-shot wrappers — anything left
    # unstubbed would reach the real network and break test hermeticity.
    # (items, total, entries consumed) - see get_liked_songs_page.
    sp.get_playlists_page = lambda limit=20, offset=0: ([], 0, 0)
    sp.get_liked_songs_page = lambda limit=20, offset=0: ([SPOTIFY_TRACK], 1, 1)
    sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: ([], 0, 0)
    sp.start_playback = lambda **kw: None
    sp.is_liked = lambda tid: False

    overlay.open_menu()
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

    # Both mark checks come before the open-in-Spotify press below, because
    # that press deliberately closes the overlay - and isVisible() is false
    # for everything once it does, which would make these pass without
    # meaning anything.
    check("Music shows the Spotify mark while browsing their content",
          panel._spotify_logo.isVisible())
    panel._show_logged_out()
    check("and takes it off a view with no Spotify content on it",
          panel._spotify_logo.isHidden())

    opened.clear()
    panel._open_current_in_spotify()
    check("opens the track's spotify: URI", opened[:1] == [SPOTIFY_TRACK["uri"]],
          f"(opened={opened})")
    overlay.close_menu()
    QTimer.singleShot(50, check_playlist_link)


def check_playlist_link():
    # A playlist's name and track count are displayed metadata too, and the
    # library row used to discard the link that would take you back to it.
    print("\n[Music — a playlist links back to Spotify]")
    sp.get_playlists_page = lambda limit=20, offset=0: ([SPOTIFY_PLAYLIST], 1, 1)
    sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: ([SPOTIFY_TRACK], 1, 1)
    overlay.open_menu()
    overlay.handle_button("right")   # music
    overlay.handle_button("cross")
    QTimer.singleShot(300, open_the_playlist)


def open_the_playlist():
    overlay.handle_button("down")    # Liked Songs -> the playlist
    overlay.handle_button("cross")   # open its tracklist
    QTimer.singleShot(300, after_playlist_open)


def after_playlist_open():
    panel = overlay._active_panel
    rows = panel.nav.current().rows
    links = [r for r in rows if isinstance(r, music._OpenPlaylistRow)]
    check("the playlist offers a way back to Spotify", len(links) == 1,
          f"(rows={rows})")
    check("the cursor still lands on a track, not on the link",
          isinstance(panel.nav.current().selected_row(), music._TrackRow),
          f"(selected {panel.nav.current().selected_row()})")

    opened.clear()
    panel.nav.current().move(-1)     # up onto the link row
    overlay.handle_button("cross")
    check("opens the playlist's spotify: URI",
          opened[:1] == [SPOTIFY_PLAYLIST["uri"]], f"(opened={opened})")
    QTimer.singleShot(50, check_liked_songs)


def check_liked_songs():
    # Liked Songs is a library section rather than a playlist: it has no page
    # of its own, so offering a link that could only fail would be worse than
    # offering none.
    overlay.open_menu()
    overlay.handle_button("right")
    overlay.handle_button("cross")
    QTimer.singleShot(300, open_liked_songs)


def open_liked_songs():
    overlay.handle_button("cross")   # Liked Songs is the first row
    QTimer.singleShot(300, after_liked_songs)


def after_liked_songs():
    panel = overlay._active_panel
    rows = panel.nav.current().rows
    print("\n[Music — Liked Songs has no playlist page to link to]")
    check("no link row is offered for it",
          not any(isinstance(r, music._OpenPlaylistRow) for r in rows),
          f"(rows={rows})")
    check("its tracks are still listed",
          any(isinstance(r, music._TrackRow) for r in rows), f"(rows={rows})")
    finish(app)


QTimer.singleShot(300, open_now_playing)
sys.exit(app.exec())
