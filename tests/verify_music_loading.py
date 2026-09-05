# Verification for the Music panel loading its data in the background.
#
#   .venv\Scripts\python.exe tests\verify_music_loading.py
#
# Exits non-zero if anything fails.
#
# The panel used to fetch everything inline in build_nav(), so opening Music
# froze the whole overlay for as long as Spotify took to answer. Now it opens
# immediately and fills in when the answer arrives, which introduces the
# failure modes this file is about: a panel stuck on "Loading…" because the
# answer was empty rather than pending, and a slow answer for a playlist the
# user has already left landing on top of the one they're looking at.
#
# Jobs are held in a queue here rather than run on a real thread, so a load can
# be left deliberately in flight while the test navigates somewhere else. The
# threading itself is covered by tests/verify_workers.py.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_loading_")

import actions.spotify_client as sp
from actions import album_art

album_art.get = lambda _url, _size, _radius, callback: callback(None)

sp.is_configured = lambda: True
sp.is_logged_in = lambda: True
sp.play_track = lambda _uri: None
sp.get_current_playback = lambda: None


class FakeSubmit:
    """Stands in for spotify_client.submit. Runs jobs inline by default; while
    `defer` is on it parks them so a load can be left mid-flight."""

    def __init__(self):
        self.jobs = []
        self.defer = False

    def __call__(self, job):
        if self.defer:
            self.jobs.append(job)
        else:
            job()

    def run_all(self):
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


submit = FakeSubmit()
sp.submit = submit

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel

import panels.music as music
from nav import NavStack

def track(n):
    return {
        "id": f"track{n}",
        "name": f"Track {n}",
        "uri": f"spotify:track:track{n}",
        "artists": [{"name": "Someone"}],
        "album": {"images": []},
    }


PLAYLISTS = [
    {"id": "pl-a", "name": "Playlist A", "tracks": {"total": 2}},
    {"id": "pl-b", "name": "Playlist B", "tracks": {"total": 2}},
]
TRACKS = {
    "pl-a": [track(0), track(1)],
    "pl-b": [track(8), track(9)],
    None: [track(5)],
}

state = {"playlists": PLAYLISTS, "fail": False, "logged_in": True}


def playlists_page(limit=20, offset=0):
    if state["fail"]:
        raise RuntimeError("boom: playlists")
    items = state["playlists"]
    page = items[offset:offset + limit]
    return page, len(items), len(page)


sp.is_logged_in = lambda: state["logged_in"]
sp.get_playlists_page = playlists_page
sp.get_liked_songs_total = lambda: 1
sp.get_liked_songs_page = lambda limit=20, offset=0: (
    TRACKS[None], len(TRACKS[None]), len(TRACKS[None])
)
sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: (
    TRACKS[pid][offset:offset + limit],
    len(TRACKS[pid]),
    len(TRACKS[pid][offset:offset + limit]),
)

app = QApplication(sys.argv)
panel = music.MusicPanel()
panel.nav = NavStack()


def rows():
    return panel.nav.current().rows


def row_names():
    return [r.playlist_name for r in rows() if isinstance(r, music._LibraryRow)]


def track_names():
    return [r.track["name"] for r in rows() if isinstance(r, music._TrackRow)]


def visible_message(container):
    labels = [
        container.itemAt(i).widget()
        for i in range(container.count())
        if isinstance(container.itemAt(i).widget(), QLabel)
    ]
    return labels[0].text() if labels else None


def open_panel():
    panel.nav.clear()
    panel.nav.push(panel.build_nav())


print("\n[opening the panel doesn't wait for Spotify]")
submit.defer = True
open_panel()
check("the panel opened without the answer", panel.nav.depth() == 1)
check("it says it's loading",
      visible_message(panel._library_rows_container) == "Loading…",
      f"(got {visible_message(panel._library_rows_container)!r})")
check("nothing is selectable while it loads", rows() == [], f"(got {rows()})")
submit.run_all()
check("the library appears once the answer arrives",
      row_names() == ["Liked Songs", "Playlist A", "Playlist B"], f"(got {row_names()})")
check("the placeholder is gone",
      visible_message(panel._library_rows_container) is None)

print("\n[the panel resizes once the rows arrive]")
# The overlay sizes this panel from outside, on the press that opens it — at
# which point the panel is one "Loading…" line tall. Nothing switches views
# when the rows land, so without asking for a fresh measurement the list would
# stay clipped to the height of the placeholder it replaced.
panel._library_loaded = False
panel._library_playlists = []
submit.defer = True
open_panel()
app.processEvents()
loading_height = panel._view_stack.height()
submit.run_all()
app.processEvents()
loaded_height = panel._view_stack.height()
check("the panel grew to fit the library", loaded_height > loading_height,
      f"(loading {loading_height}px, loaded {loaded_height}px)")
check("to exactly what the view asks for",
      loaded_height == panel._library_view.sizeHint().height(),
      f"(got {loaded_height}, wanted {panel._library_view.sizeHint().height()})")

print("\n[reopening shows last time's library straight away]")
submit.defer = True
open_panel()
check("the rows are there before the refresh lands",
      row_names() == ["Liked Songs", "Playlist A", "Playlist B"], f"(got {row_names()})")
state["playlists"] = PLAYLISTS + [{"id": "pl-c", "name": "Playlist C", "tracks": {"total": 1}}]
submit.run_all()
check("and the refresh brings in what changed",
      row_names() == ["Liked Songs", "Playlist A", "Playlist B", "Playlist C"],
      f"(got {row_names()})")
state["playlists"] = PLAYLISTS

print("\n[a slow playlist can't land under a different one]")
# Open Playlist A, leave before it answers, open Playlist B. This is the
# failure the loader's staleness handling exists for: A's tracks arriving
# under B's heading, which looks exactly like the app showing the wrong data.
submit.defer = True
open_panel()
submit.run_all()
submit.defer = True
nav = panel.nav.current()
nav.move(1)                                   # Playlist A
nav.activate()
check("the tracklist opened while still loading",
      panel.nav.depth() == 2 and track_names() == [], f"(got {track_names()})")
panel.nav.pop()                               # Circle, back to the library
panel.nav.current().on_enter()
panel.nav.current().move(1)
panel.nav.current().move(1)                   # Playlist B
panel.nav.current().activate()
submit.run_all()                              # both answers arrive now
check("the songs shown are the ones that were asked for last",
      track_names() == ["Track 8", "Track 9"], f"(got {track_names()})")
check("the panel agrees which playlist it's showing",
      panel._songs_header.text() == "Playlist B", f"(got {panel._songs_header.text()!r})")

print("\n[an empty answer is an answer, not a pending one]")
submit.defer = False
TRACKS["pl-b"] = []
panel.nav.pop()
panel.nav.current().on_enter()
panel.nav.current().move(2)
panel.nav.current().activate()
check("an empty playlist says so rather than loading forever",
      visible_message(panel._songs_rows_container) == "There's nothing in here yet.",
      f"(got {visible_message(panel._songs_rows_container)!r})")
TRACKS["pl-b"] = [track(8), track(9)]

state["playlists"] = []
open_panel()
check("an account with no playlists still gets Liked Songs",
      row_names() == ["Liked Songs"], f"(got {row_names()})")
state["playlists"] = PLAYLISTS

print("\n[a press arriving mid-rebuild doesn't corrupt the list]")
# Rebuilding a list deletes the old row widgets and then measures, and
# measuring pumps the Qt event loop (fit_scroll_to_content) — so a controller
# press queued while a refresh was in flight gets delivered halfway through
# the rebuild, against rows that are already on their way out. Emptying the
# nav level before the rebuild is what makes that press a no-op; without it
# the press navigates away mid-rebuild and the library ends up empty.
submit.defer = False
open_panel()                                  # fills the cache
submit.defer = True
open_panel()                                  # cached rows on screen, refresh pending
nav = panel.nav.current()
nav.move(2)                                   # sit on a row the refresh will delete
state["playlists"] = [PLAYLISTS[0]]           # refresh returns a shorter list
crashed = []


def press_during_rebuild():
    try:
        nav.activate()
        nav.move(1)
        nav.selected_row()
    except Exception as e:  # the crash this guards against
        crashed.append(e)


original_fit = music.fit_scroll_to_content


fired = []


def fit_and_press(scroll, *args, **kwargs):
    # Stands in for the real event-loop pump: same reentrancy, deterministic.
    # Once only — a real press queue drains, it doesn't re-enter forever.
    original_fit(scroll, *args, **kwargs)
    if fired:
        return
    fired.append(True)
    # The rows the rebuild replaced were deleteLater()'d, and a real nested
    # event loop actually destroys them. processEvents() alone doesn't, so
    # without this the press would find its old rows still alive and the test
    # would pass whether or not the code is safe.
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    press_during_rebuild()


music.fit_scroll_to_content = fit_and_press
try:
    submit.run_all()                          # the refresh lands under the user
finally:
    music.fit_scroll_to_content = original_fit
    submit.defer = False
check("the press itself is survivable", not crashed, f"(got {crashed})")
check("and the rebuild still finished correctly",
      row_names() == ["Liked Songs", "Playlist A"], f"(got {row_names()})")
state["playlists"] = PLAYLISTS

print("\n[a failed load says so instead of pretending to load]")
panel._library_loaded = False
panel._library_playlists = []
state["fail"] = True
open_panel()
message = visible_message(panel._library_rows_container)
check("the failure is on screen", message is not None and "Couldn't reach Spotify" in message,
      f"(got {message!r})")
check("and it tells the user how to retry", message is not None and "again" in message,
      f"(got {message!r})")
state["fail"] = False

print("\n[a stale token turns the library into the login prompt]")
panel._library_loaded = False
panel._library_playlists = []
state["logged_in"] = False
open_panel()
check("the login row is what the D-pad has", rows() == [panel._login_row],
      f"(got {rows()})")
check("and the panel switched to the logged-out view",
      panel._view_stack.currentWidget() is panel._logged_out_view)
state["logged_in"] = True

print("\n[an in-progress browser login can be cancelled]")
original_login_async = sp.login_async
login_callbacks = []


class FakeLoginAttempt:
    def __init__(self):
        self.cancel_count = 0

    def cancel(self):
        self.cancel_count += 1
        return True


fake_login_attempt = FakeLoginAttempt()
sp.login_async = lambda callback: (
    login_callbacks.append(callback) or fake_login_attempt
)
panel._start_login()
check("login is marked in progress", panel._logging_in)
check("the action changes to cancellation",
      panel._login_row._label.text() == "Cancel Spotify login")
panel._start_login()
check("pressing the row again requests cancellation",
      fake_login_attempt.cancel_count == 1)
login_callbacks[0](False, "Spotify login was cancelled. Press Cross to try again.")
check("the cancelled login clears its in-progress state", not panel._logging_in)
check("the login action is restored for retry",
      panel._login_row._label.text() == "Log in with Spotify")
check("the cancellation result is visible",
      "cancelled" in panel._status_label.text().lower())
sp.login_async = original_login_async

print("\n[logout clears account data and cancels stale panel work]")
state["logged_in"] = True
submit.defer = True
panel._library_loaded = False
panel._start_library_load()
check("sanity: an old-account load is queued", len(submit.jobs) == 1)
panel._library_playlists = list(PLAYLISTS)
panel._liked_songs_total = 7
panel._library_offset = 2
panel._library_total = 2
panel._library_loaded = True
panel._song_tracks = [track(42)]
panel._song_offset = 1
panel._song_total = 1
panel._songs_cache_id = "old-playlist"
panel._songs_loaded = True
panel._pending_track = track(42)
panel._current_track_id = "track42"
panel._detail_title.setText("Old Account Song")
panel._detail_artist.setText("Old Account Artist")
sp._playlist_name_cache["old-playlist"] = "Old Account Playlist"
album_art._cache["old-account-art"] = object()
art_callbacks = []
album_art._loader._pending["old-account-url"] = [(36, 8, art_callbacks.append)]
sp.forget_login()
check("library metadata is cleared",
      panel._library_playlists == [] and panel._liked_songs_total == 0)
check("track metadata is cleared",
      panel._song_tracks == [] and panel._songs_cache_id is music._NO_PLAYLIST)
check("detail metadata is cleared",
      panel._pending_track is None and panel._current_track_id is None
      and panel._detail_title.text() == "" and panel._detail_artist.text() == "")
check("module-level playlist names are cleared", sp._playlist_name_cache == {})
check("decoded album artwork is cleared", len(album_art._cache) == 0)
check("pending artwork completes empty and is forgotten",
      art_callbacks == [None] and album_art._loader._pending == {})
album_art._loader._on_downloaded("old-account-url", b"late bytes")
check("a late artwork download cannot repopulate the cache",
      "old-account-url" not in album_art._cache)
submit.run_all()
check("a queued old-account result cannot refill the library",
      panel._library_playlists == [], f"(got {panel._library_playlists})")
submit.defer = False

print("\n[regressions found by the 2026-09-04 external review]")
# Every one of these shipped through a green run of this very suite. They are
# grouped here so the shape stays obvious: each is about work that was queued
# and then *not* completed in the way the panel assumed. See
# REVIEW-2026-09-04.md and its probe script.

# -- a press is not a query: it must not be dropped for a later press --
presses = []
sp.next_track = lambda: presses.append("next")
submit.defer = True
open_panel()
submit.run_all()
panel._current_track_id = "t1"
panel._pending_track = {"id": "t1", "name": "T", "uri": "spotify:track:t1",
                        "artists": [], "album": {"images": []}}
submit.defer = True
panel._on_tile_activated(0, panel._next_tile)
panel._on_tile_activated(0, panel._next_tile)
submit.run_all()
check("two quick Next presses both run", presses == ["next", "next"],
      f"(got {presses})")

# -- leaving a playlist mid-page must not disable paging for good --
# Two playlists this file has not opened before. The panel caches the last
# tracklist it loaded, so reusing an earlier one would start already complete:
# there would be no "Load more" row to press, and this would quietly test
# nothing while passing. That is the same shape of mistake that let the bug
# ship, so it is worth stating.
# Long enough to need a second page at the real page size. Shrinking
# _PAGE_SIZE instead would page the *library* too, and the presses below would
# land on the library's own "Load more" row rather than on a playlist.
TRACKS["pl-c"] = [track(n) for n in range(music._PAGE_SIZE + 5)]
TRACKS["pl-d"] = [track(n) for n in range(music._PAGE_SIZE + 5)]
state["playlists"] = PLAYLISTS + [
    {"id": "pl-c", "name": "Playlist C", "tracks": {"total": len(TRACKS["pl-c"])}},
    {"id": "pl-d", "name": "Playlist D", "tracks": {"total": len(TRACKS["pl-d"])}},
]
submit.defer = False
open_panel()
nav = panel.nav.current()
nav.move(3)                                    # Playlist C
nav.activate()
paging_row = panel.nav.current().rows[-1]
check("sanity: we are in a tracklist that offers another page",
      isinstance(paging_row, music._LoadMoreRow)
      and panel._songs_header.text() == "Playlist C",
      f"(showing {panel._songs_header.text()!r}, last row {paging_row})")

submit.defer = True
songs = panel.nav.current()
songs.move(len(songs.rows) - 1)
songs.activate()                               # ask for C's next page
check("sanity: that request is in flight", panel._songs_paging)
panel.nav.pop()                                # leave before it lands
panel.nav.current().on_enter()
panel.nav.current().move(1)                    # Playlist D
panel.nav.current().activate()
submit.run_all()                               # C's page is superseded, never delivered
check("abandoning a page request doesn't latch paging off",
      not panel._songs_paging and not panel._library_paging,
      f"(songs={panel._songs_paging}, library={panel._library_paging})")

# The latch didn't make paging slow, it made the panel refuse to ask at all.
submit.jobs.clear()
nav_d = panel.nav.current()
nav_d.move(len(nav_d.rows) - 1)
nav_d.activate()
check("and the next playlist can still ask for a page",
      len(submit.jobs) == 1, f"(scheduled {len(submit.jobs)} request(s))")
submit.run_all()
state["playlists"] = PLAYLISTS
submit.defer = False

# -- a refresh keeps the user on the same item, not the same row number --
# The panel stays open throughout: cached rows are on screen, the user moves,
# and the refresh lands underneath them. Reopening a closed panel starting at
# the top is correct, so that is deliberately not what is tested here.
submit.defer = False
open_panel()
submit.defer = True
open_panel()                                   # cached rows, refresh pending
nav = panel.nav.current()
nav.move(1)
was = nav.selected_row().playlist_name
state["playlists"] = [{"id": "pl-new", "name": "Brand New", "tracks": {"total": 1}}] + PLAYLISTS
submit.run_all()                               # the refresh reorders the list
still = panel.nav.current().selected_row()
check("a refresh that reorders the list keeps the same playlist selected",
      getattr(still, "playlist_name", None) == was,
      f"(was on {was!r}, now on {getattr(still, 'playlist_name', None)!r})")
state["playlists"] = PLAYLISTS
submit.defer = False

# -- a failing lookup must still complete, or the caller waits forever --
import overlay as overlay_module

state["logged_in"] = True
card = overlay_module._NowPlayingCard()
sp.is_logged_in = lambda: (_ for _ in ()).throw(RuntimeError("token check blew up"))
submit.defer = True
card.refresh()
try:
    submit.run_all()
except Exception:
    pass                                        # the real worker swallows it
check("a lookup that raises still clears the in-flight flag",
      not card._refreshing, f"(got {card._refreshing})")
sp.is_logged_in = lambda: state["logged_in"]
submit.jobs.clear()
card.refresh()
check("so the card can refresh again afterwards", len(submit.jobs) == 1,
      f"(scheduled {len(submit.jobs)} job(s))")
submit.jobs.clear()
submit.defer = False

print("\n[a page containing an unavailable track]")
# Spotify returns removed or region-blocked entries as an item with no track
# inside. The client filters those out, and the panel used to advance its
# offset by the number of rows it got rather than the number of entries
# Spotify handed over - so the next request overlapped the previous one and
# the same songs appeared twice.
# Long enough to need a second page at the real page size. Shrinking
# _PAGE_SIZE would page the library too and the presses below would land on
# its "Load more" row - the same trap as the paging-latch test above.
REAL = [track(n) for n in range(music._PAGE_SIZE + 5)]


def gappy_page(pid, limit=20, offset=0):
    entries = REAL[offset:offset + limit]
    # The first entry of the first page is unavailable, so it yields no row
    # while still consuming a slot in Spotify's numbering.
    displayable = [t for i, t in enumerate(entries) if not (offset == 0 and i == 0)]
    return displayable, len(REAL), len(entries)


TRACKS["pl-gap"] = REAL
state["playlists"] = PLAYLISTS + [
    {"id": "pl-gap", "name": "Gappy", "tracks": {"total": len(REAL)}}
]
sp.get_playlist_tracks_page = gappy_page
submit.defer = False
open_panel()
nav = panel.nav.current()
nav.move(3)
check("sanity: we opened the playlist, not a Load more row",
      isinstance(nav.selected_row(), music._LibraryRow)
      and nav.selected_row().playlist_name == "Gappy",
      f"(selected {nav.selected_row()})")
nav.activate()
for _ in range(4):
    rows_now = panel.nav.current().rows
    if not rows_now or not isinstance(rows_now[-1], music._LoadMoreRow):
        break
    panel.nav.current().move(len(rows_now) - 1)
    panel.nav.current().activate()
names = track_names()
check("no track is listed twice across pages",
      len(names) == len(set(names)), f"(got {len(names)} rows, {len(set(names))} distinct)")
check("and paging still reached the end of the playlist",
      names == [t["name"] for t in REAL[1:]],
      f"(got {len(names)} of an expected {len(REAL) - 1})")

music._PAGE_SIZE = 20
state["playlists"] = PLAYLISTS
sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: (
    TRACKS[pid][offset:offset + limit],
    len(TRACKS[pid]),
    len(TRACKS[pid][offset:offset + limit]),
)

finish()
