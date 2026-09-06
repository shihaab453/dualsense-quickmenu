# Verification for the redesigned Now Playing home card: real-data rendering,
# the empty state, the collapsed/selected height change, the playing-bars vs
# paused-icon indicator and its timer lifecycle, sibling bottom-alignment, and
# resolve_context_name's album-vs-playlist resolution.
#
#   .venv\Scripts\python.exe tests\verify_now_playing_card.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before anything reads it, and stubs every Spotify network call before
# constructing OverlayWindow — panels/the card build lazily, so patching the
# module attribute early is enough (see HANDOFF's testing-pattern note).

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

settings.data_dir = lambda: tempfile.mkdtemp(prefix="dsqm_nowplaying_")

import actions.spotify_client as sp
from actions import album_art
from workers import Loader

# This test supplies fake playback data. Keep the corresponding album-art
# requests local too, leaving the panel's placeholder in place.
album_art.get = lambda _url, _size, _radius, callback: callback(None)

# ------------------------------------------------- resolve_context_name unit
print("\n[resolve_context_name]")


class _FakeClient:
    def __init__(self, playlist_name="My Playlist", raise_on_playlist=False):
        self.calls = 0
        self._name = playlist_name
        self._raise = raise_on_playlist

    def playlist(self, playlist_id, fields=None):
        self.calls += 1
        if self._raise:
            raise RuntimeError("network hiccup")
        return {"name": self._name}


_real_get_client = sp.get_client

album_playback = {
    "context": {"type": "album", "uri": "spotify:album:abc123"},
    "item": {"album": {"name": "Test Album"}},
}
check("album name comes from the embedded track data, no API call needed",
      sp.resolve_context_name(album_playback) == "Test Album")

sp._playlist_name_cache.clear()
fake = _FakeClient(playlist_name="Chill Vibes")
sp.get_client = lambda: fake
playlist_playback = {"context": {"type": "playlist", "uri": "spotify:playlist:xyz789"}}
check("playlist name resolves via a real call",
      sp.resolve_context_name(playlist_playback) == "Chill Vibes")
check("that call actually happened", fake.calls == 1)
sp.resolve_context_name(playlist_playback)
check("a second lookup for the same playlist is cached, not re-fetched",
      fake.calls == 1, f"(calls={fake.calls})")

sp._playlist_name_cache.clear()
sp.get_client = lambda: _FakeClient(raise_on_playlist=True)
check("a failed playlist lookup returns None rather than raising",
      sp.resolve_context_name(playlist_playback) is None)

check("no context (Liked Songs / a queued track / radio) is None, not a guess",
      sp.resolve_context_name({"context": None}) is None)
check("an unhandled context type (e.g. a podcast show) is None",
      sp.resolve_context_name({"context": {"type": "show", "uri": "spotify:show:1"}}) is None)

sp.get_client = _real_get_client
# Kept before the stubs replace it, so the logged-out path can be checked
# against the real wrapper further down.
_real_summary_async = sp.get_now_playing_summary_async

# --------------------------------------------------------- get_now_playing_*
print("\n[get_now_playing_summary]")
sp.get_current_playback = lambda: None
check("nothing playing anywhere returns None", sp.get_now_playing_summary() is None)

sp.get_current_playback = lambda: {"is_playing": True}  # no "item" key
check("a payload with no item returns None", sp.get_now_playing_summary() is None)

sp.get_current_playback = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
check("a failing playback lookup returns None, not an exception",
      sp.get_now_playing_summary() is None)

REAL_TRACK = {
    "name": "Test Song",
    "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
    "album": {"images": [{"url": "http://example.invalid/small.jpg"},
                          {"url": "http://example.invalid/large.jpg"}]},
}
sp.get_current_playback = lambda: {
    "is_playing": True, "item": REAL_TRACK,
    "context": {"type": "album", "uri": "spotify:album:1"},
}
REAL_TRACK["album"]["name"] = "Test Album"
summary = sp.get_now_playing_summary()
check("title comes through", summary["title"] == "Test Song")
check("artists are joined", summary["artists"] == "Artist One, Artist Two")
check("art_url picks the largest image (first in Spotify's list) — the "
      "home card displays it well above thumbnail size, so the smallest "
      "image Spotify offers renders visibly blurry",
      summary["art_url"] == "http://example.invalid/small.jpg")
check("is_playing comes through", summary["is_playing"] is True)
check("source_name resolves via the album", summary["source_name"] == "Test Album")

captured = []
# The async wrapper checks this itself now, so that a token refresh happens on
# the worker rather than on the menu-open press.
sp.is_logged_in = lambda: True
sp.get_now_playing_summary_async(captured.append)
import time
for _ in range(50):
    if captured:
        break
    time.sleep(0.02)
check("the async wrapper delivers the same data via a background thread",
      captured and captured[0]["title"] == "Test Song", f"(got {captured})")

# ------------------------------------------------------------------ the card
print("\n[the card, embedded in a real OverlayWindow]")
sp.get_now_playing_summary_async = lambda on_done: on_done(dict(summary))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import overlay as overlay_module
from overlay import OverlayWindow

app = QApplication(sys.argv)
win = OverlayWindow(get_battery=lambda: 88)
card = win._now_playing_card

win.open_menu()
app.processEvents()


def shows(label, expected: str) -> bool:
    """Whether `label` is displaying `expected`, tolerating the card's
    single-line elision.

    Not the same as comparing text() directly. _NowPlayingCard._elide truncates
    against QFontMetrics of the label's *font*, and the default font is a
    property of the Qt platform plugin: on the offscreen plugin (a headless
    runner, or anyone running these with QT_QPA_PLATFORM=offscreen) the same
    string measures wider than it does on a real desktop, so "Nothing playing"
    arrives as "Nothing play…" and an equality check fails on a card that is
    behaving perfectly.

    Deliberately does not call _elide to build the expected string: a test that
    re-runs the code under test and compares the answer to itself would pass
    however wrong that code was. This checks the weaker, honest property - the
    label shows `expected`, or a truncation of it - which is what these callers
    actually mean. The one check that is specifically *about* elision asserts
    on text() directly, and should stay that way."""
    actual = label.text()
    if actual == expected:
        return True
    # The stem has to be non-empty: elidedText returns a bare "…" when the
    # available width is tiny, and "" is a prefix of everything, so without
    # this the helper would wave through any expected string at all.
    stem = actual[:-1]
    return bool(stem) and actual.endswith("…") and expected.startswith(stem)


check("real data reaches the collapsed card", "Test Song" in card._title_label.text())
check("caption names Spotify when data is real",
      card._caption_label.text() == "Now playing on Spotify")
check("the playing indicator reflects is_playing=True", card._indicator._playing is True)
check("the equalizer timer is running while playing", card._indicator._timer.isActive())
check("detail (artist/source) is hidden while not selected",
      not card._detail_widget.isVisible())
collapsed_height = card.height()

print("\n  -- selecting the card --")
win.handle_button("up")
app.processEvents()
check("detail becomes visible once selected", card._detail_widget.isVisible())
check("artist text is populated", "Artist One" in card._artist_label.text())
check("source text is populated", shows(card._source_label, "From Test Album"),
      f"(got {card._source_label.text()!r})")
check("the card grows once selected", card.height() > collapsed_height,
      f"(collapsed={collapsed_height}, selected={card.height()})")
# It used to say "Press □ for Pause", which this check pinned in place even
# though Square is not mapped in controller.py - the hint advertised an action
# that did nothing. A test asserting a promise the app cannot keep is worse
# than no test, so this now asserts the honest one.
check("the hint offers an action that actually works",
      win._cards_hint_label.text() == "Press Cross to open",
      f"(got {win._cards_hint_label.text()!r})")
check("the hint label is visible", win._cards_hint_label.isVisible())
check("the tray label is hidden while cards are focused",
      not win._tray_label.isVisible())

print("\n  -- sibling bottom-alignment --")
cards_lay = win._cards_widget.layout()
all_bottom = all(
    cards_lay.itemAt(i).alignment() & overlay_module.Qt.AlignBottom
    for i in range(cards_lay.count())
)
check("every card in the row is bottom-aligned", all_bottom,
      "(needed once the real card's height can diverge from its 3 decorative siblings)")

print("\n  -- eliding --")
long_summary = dict(summary)
long_summary["title"] = "A Very Long Song Title That Will Not Possibly Fit In The Card"
sp.get_now_playing_summary_async = lambda on_done: on_done(long_summary)
card.refresh()
app.processEvents()
check("a long title is elided with a trailing ellipsis",
      card._title_label.text().endswith("…"), f"(got {card._title_label.text()!r})")
check("the elided text is shorter than the original",
      len(card._title_label.text()) < len(long_summary["title"]))

print("\n  -- no source (e.g. Liked Songs / no context) --")
no_source = dict(summary)
no_source["source_name"] = None
sp.get_now_playing_summary_async = lambda on_done: on_done(no_source)
card.refresh()
app.processEvents()
check('omits "From None" when there is no resolved source',
      card._source_label.text() == "", f"(got {card._source_label.text()!r})")

print("\n  -- refresh() guards against overlapping calls --")
call_count = [0]


def counting_async(on_done):
    call_count[0] += 1
    # deliberately don't call on_done — simulates a call still in flight


sp.get_now_playing_summary_async = counting_async
card._refreshing = False
card.refresh()
card.refresh()
card.refresh()
check("a second refresh() while one is in flight is a no-op",
      call_count[0] == 1, f"(calls={call_count[0]})")
card._refreshing = False

print("\n  -- logout rejects a queued old-session result --")
late_callbacks = []
sp.get_now_playing_summary_async = late_callbacks.append
card.refresh()
check("sanity: an old-session card result is pending", len(late_callbacks) == 1)
sp.forget_login()
late_callbacks[0](dict(summary))
app.processEvents()
check("logout clears the card immediately",
      shows(card._title_label, "Nothing playing"),
      f"(got {card._title_label.text()!r})")
check("the queued result cannot repaint old account data",
      card._caption_label.text() == "Spotify"
      and card._title_label.text() != summary["title"])

print("\n  -- empty state --")
sp.get_now_playing_summary_async = lambda on_done: on_done(None)
card.refresh()
app.processEvents()
check("no data shows a neutral empty state",
      shows(card._title_label, "Nothing playing"),
      f"(got {card._title_label.text()!r})")
check("caption doesn't claim Spotify when there's nothing to attribute to it",
      card._caption_label.text() == "Spotify")
check("indicator stops animating in the empty state", card._indicator._playing is False)

print("\n  -- not logged in --")
# The logged-in check used to happen in refresh(), on the press that opens the
# menu — and validating a cached token can refresh it over the network. It now
# runs inside the worker job instead, so what's worth pinning down is that the
# wrapper answers None from there without asking Spotify anything.
sp.submit = lambda job: job()  # inline, so the answer is here on the next line
sp.get_now_playing_summary_async = _real_summary_async
sp.is_logged_in = lambda: False
asked = []
sp.get_current_playback = lambda: asked.append(True)
delivered = []
_real_summary_async(delivered.append)
check("a logged-out lookup answers None", delivered == [None], f"(got {delivered})")
check("without asking Spotify anything first", asked == [], f"(got {asked})")

card._refreshing = False
card.refresh()
app.processEvents()
check("so the card still shows the empty state",
      shows(card._title_label, "Nothing playing"),
      f"(got {card._title_label.text()!r})")

print("\n  -- close_menu() stops the equalizer timer --")
sp.is_logged_in = lambda: True
sp.get_now_playing_summary_async = lambda on_done: on_done(dict(summary))
card.refresh()
app.processEvents()
check("sanity: timer running before close", card._indicator._timer.isActive())
win.close_menu()
check("close_menu() stops the timer", not card._indicator._timer.isActive())

print("\n[the Now Playing panel]")
# The panel asks Spotify and, failing that, the Windows media session. Both
# used to happen inline in build_nav(), i.e. on the press that opens it.


class HeldSubmit:
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


spotify_held = HeldSubmit()
winrt_held = HeldSubmit()
sp.submit = spotify_held
sp.is_logged_in = lambda: True

from panels.nowplaying import NowPlayingPanel
from actions import now_playing as _now_playing

panel = NowPlayingPanel()
panel._spotify_loader = Loader(spotify_held, "test/spotify")
panel._winrt_loader = Loader(winrt_held, "test/winrt")

PANEL_TRACK = {"id": "t1", "name": "Panel Song",
               "artists": [{"name": "Panel Artist"}], "album": {"images": []}}
sp.get_current_playback = lambda: {"is_playing": True, "item": PANEL_TRACK}
_now_playing.get = lambda: {"title": "Fast fallback", "artist": "Windows"}

spotify_held.defer = True
winrt_held.defer = True
nav = panel.build_nav()
check("the panel opens before either lookup answers",
      panel._song_label.text() == "Loading…", f"(got {panel._song_label.text()!r})")
check("with nothing selectable yet", nav.rows == [], f"(got {nav.rows})")
winrt_held.run_all()
check("WinRT can answer while Spotify is still pending",
      "Fast fallback" in panel._song_label.text(),
      f"(got {panel._song_label.text()!r})")
spotify_held.run_all()
check("the track appears once the lookup lands",
      "Panel Song" in panel._song_label.text(), f"(got {panel._song_label.text()!r})")
check("Spotify replaces an earlier WinRT fallback",
      panel.heading.text() == "Now playing on Spotify")
check("and the open-in-Spotify row comes with it", nav.rows == [panel._open_row],
      f"(got {nav.rows})")

spotify_held.defer = True
winrt_held.defer = True
nav = panel.build_nav()
check("reopening shows the last answer rather than blanking",
      "Panel Song" in panel._song_label.text(), f"(got {panel._song_label.text()!r})")
winrt_held.run_all()
spotify_held.run_all()
spotify_held.defer = False
winrt_held.defer = False

# The Web API has nothing, so the panel falls back to the Windows media
# session. Since 2026-09-06 that reading is Spotify-only (actions/now_playing
# filters by the session's owning application), so what arrives here is
# Spotify's content read a different way - and it has to be attributed, not
# hidden. These three checks asserted the opposite before that change, when
# the fallback could be a browser.
sp.get_current_playback = lambda: None
_now_playing.get = lambda: {"title": "Clair de Lune", "artist": "Debussy"}
panel.build_nav()
check("it falls back to the Windows media session",
      "Clair de Lune" in panel._song_label.text(), f"(got {panel._song_label.text()!r})")
check("and attributes it, because that reading is Spotify-only now",
      panel.heading.text() == "Now playing on Spotify",
      f"(got {panel.heading.text()!r})")
check("and offers a link back to the service",
      len(panel._nav.rows) == 1, f"(got {panel._nav.rows})")

# A browser or another player owning the session: now_playing.get() returns
# None for those, so this is also what "something else is playing" looks like.
# The other service's track never reaches the screen at all.
_now_playing.get = lambda: None
panel.build_nav()
check("nothing playing anywhere says so",
      panel._song_label.text() == "Nothing playing", f"(got {panel._song_label.text()!r})")
check("with no attribution, since there is no Spotify content",
      panel.heading.text() == "Now playing", f"(got {panel.heading.text()!r})")
check("and no link row", panel._nav.rows == [], f"(got {panel._nav.rows})")

print("\n[logout clears the Now Playing panel cache]")
sp.get_current_playback = lambda: {"is_playing": True, "item": PANEL_TRACK}
panel.build_nav()
check("sanity: Spotify metadata is cached before logout",
      panel._last_result is not None and panel._current_track is PANEL_TRACK)
sp.forget_login()
check("the cached Spotify result is cleared",
      panel._last_result is None and panel._current_track is None)
check("the Spotify action is removed", panel._nav.rows == [])
check("the panel no longer displays the old track",
      panel._song_label.text() == "Nothing playing")

finish()
