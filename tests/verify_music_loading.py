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
    return items[offset:offset + limit], len(items)


sp.is_logged_in = lambda: state["logged_in"]
sp.get_playlists_page = playlists_page
sp.get_liked_songs_total = lambda: 1
sp.get_liked_songs_page = lambda limit=20, offset=0: (TRACKS[None], len(TRACKS[None]))
sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: (
    TRACKS[pid][offset:offset + limit], len(TRACKS[pid])
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

finish()
