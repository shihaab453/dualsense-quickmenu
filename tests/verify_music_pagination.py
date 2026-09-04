# Verification for Spotify pagination in the Music panel.
#
#   .venv\Scripts\python.exe tests\verify_music_pagination.py
#
# Exits non-zero if anything fails.
#
# The panel used to fetch one fixed batch (6 playlists, 20 tracks) and stop, so
# a library bigger than that simply couldn't be browsed. It now loads a page at
# a time behind a selectable "Load more" row. What's worth pinning down is less
# the happy path than the edges: that the row disappears exactly when the
# collection is exhausted, that a failed page keeps the rows already loaded
# instead of blanking the list, that a retry works, and that an empty page can't
# leave a Load more row that would never load anything.
#
# Nothing here touches the network: every Spotify call the panel makes is
# replaced with a fake paged collection before the panel is constructed.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_paging_")

import actions.spotify_client as sp
from actions import album_art

# These assertions are about paging, not artwork — returning no art keeps the
# panel's normal placeholder and prevents a background network call per row.
album_art.get = lambda _url, _size, _radius, callback: callback(None)

sp.is_configured = lambda: True
sp.is_logged_in = lambda: True
sp.play_track = lambda _uri: None
sp.get_current_playback = lambda: None

from PySide6.QtWidgets import QApplication, QLabel

import panels.music as music
from nav import NavStack

failures = []


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not condition:
        failures.append(label)


def track(n):
    return {
        "id": f"track{n}",
        "name": f"Track {n}",
        "uri": f"spotify:track:track{n}",
        "artists": [{"name": "Someone"}],
        "album": {"images": []},
    }


def playlist(n):
    return {"id": f"pl{n}", "name": f"Playlist {n}", "tracks": {"total": 3}}


class FakeCollection:
    """A paged Spotify collection that can be told to fail the next fetch, so
    the failure path is exercised the same way a real network drop would."""

    def __init__(self, items):
        self.items = items
        self.fail_next = False
        self.next_page_empty = False
        self.calls = []

    def page(self, limit, offset):
        self.calls.append((limit, offset))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom: page fetch")
        if self.next_page_empty:
            self.next_page_empty = False
            return [], len(self.items)
        return self.items[offset:offset + limit], len(self.items)


# Small pages so the fixtures stay readable; production uses 20.
music._PAGE_SIZE = 2

playlists = FakeCollection([playlist(n) for n in range(5)])
liked = FakeCollection([track(n) for n in range(5)])

sp.get_playlists_page = lambda limit=20, offset=0: playlists.page(limit, offset)
sp.get_liked_songs_page = lambda limit=20, offset=0: liked.page(limit, offset)
sp.get_playlist_tracks_page = lambda pid, limit=20, offset=0: liked.page(limit, offset)
sp.get_liked_songs_total = lambda: len(liked.items)

app = QApplication(sys.argv)
panel = music.MusicPanel()
panel.nav = NavStack()


def more_row():
    rows = panel.nav.current().rows
    found = [r for r in rows if isinstance(r, music._LoadMoreRow)]
    return found[0] if found else None


def more_label():
    row = more_row()
    return None if row is None else row.findChild(QLabel).text()


def row_names():
    return [
        r.playlist_name
        for r in panel.nav.current().rows
        if isinstance(r, music._LibraryRow)
    ]


def track_names():
    return [
        r.track["name"]
        for r in panel.nav.current().rows
        if isinstance(r, music._TrackRow)
    ]


print("\n[library — first page]")
panel.nav.push(panel.build_nav())
check("first page loads one page of playlists, plus Liked Songs",
      row_names() == ["Liked Songs", "Playlist 0", "Playlist 1"], f"(got {row_names()})")
check("a Load more row is offered while playlists remain",
      more_label() == "Load more playlists", f"(got {more_label()!r})")
check("only one page was requested", playlists.calls == [(2, 0)], f"(got {playlists.calls})")

print("\n[library — loading further pages]")
nav = panel.nav.current()
first_row = nav.rows[1]
nav.move(len(nav.rows) - 1)          # select the Load more row
check("Load more is the last row", isinstance(nav.selected_row(), music._LoadMoreRow))
retired = nav.selected_row()
nav.activate()
check("second page is appended, not replaced",
      row_names() == ["Liked Songs", "Playlist 0", "Playlist 1", "Playlist 2", "Playlist 3"],
      f"(got {row_names()})")
check("selection lands on the first newly loaded row",
      panel.nav.current().selected_row().playlist_name == "Playlist 2",
      f"(got {panel.nav.current().selected_row()})")
check("the nav stack didn't grow a level", panel.nav.depth() == 1,
      f"(got {panel.nav.depth()})")
# Both of these pin down that a page is added to the list already on screen.
# Rebuilding it instead still looks right but re-creates and re-styles every
# row, which made each press cost more than the last on a long list.
check("paging keeps the same RowList", panel.nav.current() is nav)
check("rows already on screen are reused, not rebuilt",
      panel.nav.current().rows[1] is first_row)
# deleteLater() doesn't destroy the widget until control is back in the event
# loop, so the pressed row has to be unparented too — otherwise it carries on
# painting and the new page's first row renders on top of it.
check("the pressed Load more row leaves the screen immediately",
      retired.parent() is None and not retired.isVisible())

nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("the last page loads the remainder",
      row_names() == ["Liked Songs"] + [f"Playlist {n}" for n in range(5)],
      f"(got {row_names()})")
check("Load more disappears once the library is exhausted", more_row() is None)

print("\n[library — a page that fails]")
playlists.items = [playlist(n) for n in range(5)]
panel.nav.clear()
panel.nav.push(panel.build_nav())
playlists.fail_next = True
nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("a failed page keeps the playlists already loaded",
      row_names() == ["Liked Songs", "Playlist 0", "Playlist 1"], f"(got {row_names()})")
check("the row says the fetch failed and can be retried",
      more_label() == "Couldn't load more playlists · press to retry",
      f"(got {more_label()!r})")
nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("retrying after a failure loads the page",
      row_names() == ["Liked Songs", "Playlist 0", "Playlist 1", "Playlist 2", "Playlist 3"],
      f"(got {row_names()})")
check("the label goes back to normal once it succeeds",
      more_label() == "Load more playlists", f"(got {more_label()!r})")

print("\n[library — an empty page despite a larger reported total]")
panel.nav.clear()
panel.nav.push(panel.build_nav())
playlists.next_page_empty = True
nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("an empty page removes the Load more row instead of looping forever",
      more_row() is None, f"(got {more_label()!r})")

print("\n[songs — paging a tracklist]")
panel.nav.clear()
panel.nav.push(panel.build_nav())
panel.nav.current().activate()       # Liked Songs -> Songs view
check("the songs view pushed a nav level", panel.nav.depth() == 2,
      f"(got {panel.nav.depth()})")
check("first page of tracks is shown",
      track_names() == ["Track 0", "Track 1"], f"(got {track_names()})")
check("a Load more row is offered while tracks remain",
      more_label() == "Load more songs", f"(got {more_label()!r})")

nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("the next page of tracks is appended",
      track_names() == ["Track 0", "Track 1", "Track 2", "Track 3"], f"(got {track_names()})")
check("paging a tracklist doesn't stack up nav levels", panel.nav.depth() == 2,
      f"(got {panel.nav.depth()})")
check("selection lands on the first newly loaded track",
      panel.nav.current().selected_row().track["name"] == "Track 2",
      f"(got {panel.nav.current().selected_row()})")

liked.fail_next = True
nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("a failed track page keeps the tracks already loaded",
      track_names() == ["Track 0", "Track 1", "Track 2", "Track 3"], f"(got {track_names()})")
check("the track row reports the failure",
      more_label() == "Couldn't load more songs · press to retry", f"(got {more_label()!r})")

nav = panel.nav.current()
nav.move(len(nav.rows) - 1)
nav.activate()
check("retrying loads the final track",
      track_names() == [f"Track {n}" for n in range(5)], f"(got {track_names()})")
check("Load more disappears once the tracklist is exhausted", more_row() is None)

print("\n[songs — reopening a playlist starts from the first page]")
panel.nav.pop()
panel.nav.clear()
panel.nav.push(panel.build_nav())
panel.nav.current().activate()
check("a reopened tracklist is back to one page",
      track_names() == ["Track 0", "Track 1"], f"(got {track_names()})")
check("the refetch started at offset 0", liked.calls[-1] == (2, 0),
      f"(got {liked.calls[-1]})")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
else:
    print("All checks passed.")
print("=" * 60)
sys.exit(1 if failures else 0)
