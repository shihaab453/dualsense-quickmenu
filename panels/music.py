# Phase D milestone 2: real Spotify library browsing — Library (Liked Songs
# + your playlists, every row clickable) -> Songs (that row's tracklist) ->
# Detail (a song's playback controls). Built on milestone 1's login flow.
#
# Each view is its own widget added to self.body; only one is visible at a
# time. Moving between views pushes a new RowList onto the shared nav stack
# (self.nav, handed in by OverlayWindow) tagged with on_enter=<show that
# view> — so Circle popping back to a view re-shows it automatically,
# without this panel needing its own separate "which screen am I on"
# tracking (see nav.py's on_enter for why).
#
# Note: pressing "Log in with Spotify" opens your default browser, which
# will take window focus away from the overlay (expected — that's how
# logging in works). Press the PS button again afterward to bring the menu
# back.
#
# There are three logged-out-ish states, not one. Before anything works the
# user needs a Spotify client ID of their own (see actions/spotify_client.py
# for why it isn't baked in), and a 32-character ID can't be typed with a
# D-pad — so that state offers a row that closes the overlay and opens the
# desktop Settings window instead. Once an ID is saved, the normal
# browser-based login row takes over.
#
# --- State layout (2026-09-05 refactor) ---------------------------------
#
# Library and Songs used to be ~9 flat attributes each (loader, nav, rows,
# items, total, offset, loaded, load_failed, paging), hand-copied and kept
# in sync by convention rather than by anything that enforced it — which is
# exactly the shape bug #11 (a single "paging" flag shared between both
# lists, so finishing one request could latch paging off for the other)
# shipped from. _PagedSection below is that shape given one home instead of
# two: every mechanic a paged, D-pad-navigable list needs — offset
# accounting, stale-reply detection (a page reply whose requested offset no
# longer matches means a fresher reload superseded it), in-place Load More
# mutation, identity-preserving rebuilds — is written once and used
# identically by self._library and self._songs, so a future fix to any of
# that applies to both by construction instead of needing to be re-derived
# a second time.
#
# What's deliberately NOT folded in: fetching page 0. Library's first page
# is entangled with a login check and the Liked Songs count in a way
# Songs' isn't (see _start_library_load / _start_songs_load below) — that's
# real business logic, not duplicated mechanism, so each keeps its own
# short method rather than being forced through a shared hook for the sake
# of it. Likewise _login (bridge/in_progress/attempt) and _detail
# (loader/pending_track/current_track_id/status_pending) group state that
# always changes together, but their own behavior — starting a login,
# handling a tile press — stays on MusicPanel: unlike Library/Songs there is
# no second copy of that behavior to de-duplicate, and every one of those
# methods already touches panel-owned widgets directly.

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import logs
import settings_window
from actions import album_art
from actions import spotify_client as sp
from icons import render_icon
from nav import RowList
from workers import Commands, Loader
from panels.base import (
    ActionRow,
    Panel,
    Tile,
    clear_layout,
    fit_scroll_to_content,
    make_scrollable_rows,
    message_label,
    open_in_spotify,
    selected_row_style,
    spotify_logo_label,
)

log = logs.get(__name__)

_REPEAT_CYCLE = ["off", "context", "track"]
_PAGE_SIZE = 20

# Liked Songs is a real library entry with no playlist id of its own, so None
# is already meaningful there and can't double as "no playlist cached yet".
# Reused below as _PagedSection's default cache_key: only Songs' instance
# ever compares it against anything (see _open_songs_view), but it lives on
# both instances since both are the same class.
_NO_PLAYLIST = object()

_LOAD_FAILED_MESSAGE = "Couldn't reach Spotify. Press Circle and open Music again to retry."

_UNAVAILABLE_MESSAGES = {
    "no_device": "Open Spotify on this PC or phone to enable playback control.",
    "premium_required": "Playback control requires Spotify Premium.",
    "other": "Couldn't reach Spotify right now.",
}


def _row_style(selected: bool, radius: int = 12) -> str:
    return selected_row_style(selected, radius=radius)


def _explicit_badge() -> QLabel:
    """The small bordered "E" box the mockup uses for explicit tracks —
    just styled text, not an icon."""
    badge = QLabel("E")
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(20, 16)
    badge.setStyleSheet(
        "border: 1.5px solid rgba(255,255,255,0.5); border-radius: 4px;"
        " font-size: 11px; font-weight: 700; color: rgba(255,255,255,0.6);"
    )
    return badge



class _LoginSignal(QObject):
    # login_async's callback fires on a background thread; Qt widgets can
    # only be touched from the main thread, so this signal hops back over —
    # the same bridge pattern main.py uses for controller events.
    finished = Signal(bool, str)


class _LibraryRow(QFrame):
    """A row in the Library view: Liked Songs or one of the user's real
    playlists — all of them clickable, opening the same Songs view.
    `playlist_id=None` is the sentinel for Liked Songs (its tracks come from
    a different API call than a normal playlist's)."""

    def __init__(self, title: str, subtitle: str, playlist_id, playlist=None):
        super().__init__()
        self.setObjectName("row")
        self.playlist_id = playlist_id
        self.playlist_name = title
        # The whole playlist dict, kept for its `uri`/`external_urls` the way
        # _TrackRow keeps its track. This row used to take only the id and
        # the name, which threw the link away at the point of construction
        # and left a playlist as the one piece of displayed Spotify metadata
        # with no way back to Spotify — see _open_songs_view, which offers
        # it. None for Liked Songs: it is a library section rather than a
        # playlist and has no page of its own to open.
        self.playlist = playlist
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 17px; font-weight: 700; color: white;")
        lay.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.4);")
        lay.addWidget(subtitle_label)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(_row_style(selected))


class _OpenPlaylistRow(ActionRow):
    """The Songs view's way back to the playlist on Spotify.

    Its own class rather than a bare ActionRow so _row_identity can tell it
    apart: it sits at the top of a list that gets rebuilt under the user by
    every refresh, and a row the selection can't identify is a row the
    selection loses track of."""


def _fetch_songs_page(playlist_id, offset: int):
    """One page of a library entry's tracks, whichever kind it is. Runs on the
    Spotify worker thread, so it must not touch a widget."""
    if playlist_id is None:
        return sp.get_liked_songs_page(limit=_PAGE_SIZE, offset=offset)
    return sp.get_playlist_tracks_page(playlist_id, limit=_PAGE_SIZE, offset=offset)


def _load_more_label(noun: str, failed: bool) -> str:
    """The Load More row's text — pressing it again after a failed page is
    the retry, so the row says what happened instead of silently no-op'ing."""
    if failed:
        return f"Couldn't load more {noun} · press to retry"
    return f"Load more {noun}"


class _LoadMoreRow(QFrame):
    """A controller-selectable continuation row at the end of a paged list."""

    def __init__(self, label: str, failed: bool = False):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self._text = QLabel(label)
        color = "rgba(255,255,255,0.5)" if failed else "#3ddc97"
        self._text.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {color};")
        lay.addWidget(self._text)
        lay.addStretch(1)
        self.set_selected(False)

    def set_label(self, label: str) -> None:
        """Used to say "Loading more…" in place while the page is fetched,
        rather than replacing the row and losing the selection sitting on it."""
        self._text.setText(label)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(_row_style(selected))


class _TrackRow(QFrame):
    """A row in the Songs (tracklist) view — one liked song."""

    _ART_SIZE = 36

    def __init__(self, track: dict):
        super().__init__()
        self.track = track
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)

        self._art = QLabel()
        self._art.setFixedSize(self._ART_SIZE, self._ART_SIZE)
        # Color placeholder while the real art downloads (or if there's no
        # art / the request fails) — derived from the track so different
        # songs are at least visually distinct in the meantime.
        hue = abs(hash(track.get("id") or track.get("name", ""))) % 360
        self._art.setStyleSheet(
            f"background: hsl({hue}, 45%, 40%);"
            f" border-radius: {album_art.CORNER_RADIUS}px;"
        )
        lay.addWidget(self._art)
        album_art.get(
            album_art.smallest_image_url(track),
            self._ART_SIZE,
            album_art.CORNER_RADIUS,
            self._on_art_loaded,
        )

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_label = QLabel(track.get("name", ""))
        title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_row.addWidget(title_label)
        if track.get("explicit"):
            title_row.addWidget(_explicit_badge())
        title_row.addStretch(1)
        text_col.addLayout(title_row)
        artist_label = QLabel(", ".join(a["name"] for a in track.get("artists", [])))
        artist_label.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.45);")
        text_col.addWidget(artist_label)
        lay.addLayout(text_col)
        lay.addStretch(1)

        self.set_selected(False)

    def _on_art_loaded(self, pixmap) -> None:
        if pixmap is None:
            return
        try:
            self._art.setStyleSheet("")
            self._art.setPixmap(pixmap)
        except RuntimeError:
            pass  # row was rebuilt/deleted (e.g. switched playlists) before this arrived

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(_row_style(selected))


class _ToggleTile(Tile):
    """A Tile with an on/off "active" look (accent-green tint) that's
    independent of the white focus-selected ring, so both can show at once.
    Which icon *shape* to show (e.g. outline vs filled heart, or repeat vs
    repeat-one) is up to the caller via set_icon_name() — this class only
    owns the color."""

    def __init__(self, icon_name: str):
        self._active = False
        super().__init__(icon_name)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._restyle()

    def _restyle(self) -> None:
        bg = "white" if self._selected else "rgba(255,255,255,0.10)"
        if self._selected:
            fg = "#15151c"
        else:
            fg = "#3ddc97" if self._active else "white"
        self.setStyleSheet(f"background: {bg}; border-radius: {self.SIZE // 2}px;")
        self.setPixmap(render_icon(self._icon_name, fg, self.ICON_SIZE))


# ---- shared row-identity helpers (module-level: no per-panel state needed) --

def _row_identity(row):
    """What a row *is*, as opposed to where it currently sits. A refresh
    can add, remove or reorder rows, so restoring by position lands the
    user somewhere arbitrary; restoring by identity lands them back on the
    thing they were looking at."""
    if isinstance(row, _LibraryRow):
        return ("playlist", row.playlist_id)
    if isinstance(row, _TrackRow):
        return ("track", row.track.get("id"))
    if isinstance(row, _LoadMoreRow):
        return ("load-more", None)
    if isinstance(row, _OpenPlaylistRow):
        return ("open-playlist", None)
    return None


def _index_of(rows, identity, default: int = 0) -> int:
    """Where the selection should land. `default` is where to go when there
    is nothing to restore — normally the top, but a list whose first row is
    a secondary action (Songs' open-in-Spotify link) passes the index of its
    first real item instead, so opening a playlist puts the cursor on a song
    rather than on a link."""
    fallback = min(default, max(0, len(rows) - 1))
    if identity is None:
        return fallback
    for i, row in enumerate(rows):
        if _row_identity(row) == identity:
            return i
    # It's gone - the playlist was deleted, or the song dropped out of the
    # list. Falling back is the honest answer; silently landing on whatever
    # moved into that position is not.
    return fallback


def _selected_identity(nav):
    if nav is None:
        return None
    row = nav.selected_row()
    return None if row is None else _row_identity(row)


class _PagedSection:
    """One paged, D-pad-navigable list's paging state and mechanics: a
    Loader, the RowList currently pushed for it (or None before it's been
    opened), the row widgets on screen, the raw entries fetched so far, how
    many Spotify says exist in total, how many entries have been consumed
    (not the same as rows displayed — see add_rows), whether the first page
    has arrived, whether the last fetch failed, and an in-flight "paging"
    guard. Used identically by MusicPanel's self._library and self._songs.

    cache_key exists for Songs' benefit (which playlist's tracks are these?)
    and Library's instance simply never reads it — see _open_songs_view.
    Fetching the *first* page is not here; see the module comment for why.
    """

    def __init__(self, loader, rows_container, scroll, row_factory, noun: str):
        self.loader = loader
        self.rows_container = rows_container
        self.scroll = scroll
        self.row_factory = row_factory  # item -> QWidget
        self.noun = noun  # "playlists" / "songs", for Load More text
        self.nav = None
        self.rows = []
        self.items = []
        self.total = 0
        self.offset = 0
        self.loaded = False
        self.load_failed = False
        self.paging = False
        self.cache_key = _NO_PLAYLIST

    def reset(self) -> None:
        """Everything except loader and nav, which the owner cancels/empties
        itself — see MusicPanel._reset_spotify_session for why that ordering
        (nav emptied before the row container is cleared) has to stay
        hand-written there rather than folded in here."""
        self.rows = []
        self.items = []
        self.total = 0
        self.offset = 0
        self.loaded = False
        self.load_failed = False
        self.paging = False
        self.cache_key = _NO_PLAYLIST

    # ---- rendering ----

    def render(self, resettle, prefix_rows=(), empty_message="There's nothing in here yet.",
               default_index: int = 0) -> None:
        """Rebuild this section's rows from current state and hand them to
        nav. Emptying the nav level *first* is deliberate: a press delivered
        mid-rebuild finds an empty list and no-ops, rather than a list full
        of about-to-be-deleted widgets, which is a crash. That used to be
        reachable because measuring pumped the Qt event loop; it isn't any
        more (see fit_scroll_to_content), so this ordering is now defence in
        depth rather than the thing standing between you and the bug. Keep
        it — it costs one line and it is what makes this rebuild safe to call
        from anywhere, including from inside a future callback that does
        re-enter.

        `prefix_rows` are added unconditionally once loaded, before any
        paged items — Library's synthetic Liked Songs row; pass none for a
        section (Songs) with no such row."""
        keep = _selected_identity(self.nav)
        if self.nav is not None:
            self.nav.replace_rows([])
        clear_layout(self.rows_container)
        self.rows = []
        if not self.loaded:
            message = _LOAD_FAILED_MESSAGE if self.load_failed else "Loading…"
            self.rows_container.addWidget(message_label(message))
            fit_scroll_to_content(self.scroll)
            resettle()
            return

        self.rows = list(prefix_rows)
        for widget in prefix_rows:
            self.rows_container.addWidget(widget)

        if not self.items and not self.rows:
            # Reachable only by Songs (Library always has a prefix row once
            # loaded). Checked in the same priority order as the loading
            # case above so a failed *first* page and a genuinely empty
            # playlist can't be confused for each other.
            message = _LOAD_FAILED_MESSAGE if self.load_failed else empty_message
            self.rows_container.addWidget(message_label(message))
            fit_scroll_to_content(self.scroll)
            resettle()
            return

        self.add_rows(0)
        if self.nav is not None:
            self.nav.replace_rows(self.rows, _index_of(self.rows, keep, default_index))
        resettle()

    def add_rows(self, rendered: int) -> None:
        """Add self.items from `rendered` onward via row_factory, then a
        Load More row if Spotify says more remain. `rendered` indexes into
        self.items (entries already turned into rows before this call), not
        into self.rows — prefix rows (Library's Liked Songs) are not part of
        that count."""
        for item in self.items[rendered:]:
            row = self.row_factory(item)
            self.rows.append(row)
            self.rows_container.addWidget(row)

        if self.offset < self.total:
            more_row = _LoadMoreRow(_load_more_label(self.noun, self.load_failed), self.load_failed)
            self.rows.append(more_row)
            self.rows_container.addWidget(more_row)

        fit_scroll_to_content(self.scroll)

    # ---- paging (page 0 excepted - see the module comment) ----

    def page_in_more(self, fetch_more, resettle, on_error=None) -> None:
        """fetch_more(offset) is the Spotify call for the next page, run on
        the worker thread. `paging` is an instance attribute, so self._library
        and self._songs each get their own by construction - the single
        shared flag that let finishing one list's request re-enable paging
        for the *other* one can't recur without two objects sharing state
        they don't actually share."""
        if self.paging:
            return  # already fetching; a second press shouldn't queue another
        self.paging = True
        if self.rows and isinstance(self.rows[-1], _LoadMoreRow):
            self.rows[-1].set_label(f"Loading more {self.noun}…")
        offset = self.offset

        def work():
            return fetch_more(offset)

        self.loader.start(
            work, lambda value, error: self._on_page(offset, value, error, resettle, on_error)
        )

    def _on_page(self, offset, value, error, resettle, on_error) -> None:
        self.paging = False
        if offset != self.offset:
            # The section was reloaded from scratch underneath this page (a
            # reopen, or a login landing). Appending now would duplicate or
            # interleave rows, so let the reload's own render stand.
            return
        # How many items are already on screen. Since a page can contain
        # entries that produce no row, this is no longer the same number as
        # the offset that was requested.
        rendered = len(self.items)
        if error is not None:
            if on_error is not None:
                on_error(error)
            self.load_failed = True
        else:
            items, total, consumed = value
            self.load_failed = False
            self.items.extend(items)
            self.offset += consumed
            # A page that returned nothing at all means the collection
            # really is exhausted. A page that returned entries but no
            # displayable items is not the end - keep the count Spotify gave
            # us so the Load More row survives and the next press moves
            # past them.
            self.total = total if consumed else len(self.items)
        selected = self._drop_selected_load_more()
        self.add_rows(rendered)
        if self.nav is not None:
            self.nav.reselect(selected)
        resettle()

    def _drop_selected_load_more(self) -> int:
        """Take the pressed Load More row off the end of this list and
        report the position it held, so the first row of the newly loaded
        page can take over the selection.

        A page is appended to the RowList already on screen rather than
        built into a replacement one: rebuilding re-created and re-styled
        every row each time, so each press cost more than the last (about a
        second of frozen UI by the thousandth track) — exactly the size of
        library paging exists to serve."""
        index = len(self.rows) - 1
        if self.rows and isinstance(self.rows[-1], _LoadMoreRow):
            row = self.rows.pop()
            self.rows_container.removeWidget(row)
            # deleteLater() alone isn't enough: Qt only destroys the widget
            # once control is back in the event loop, so until then the row
            # carries on painting where it was and the first row of the new
            # page renders on top of it. Unparenting hides it immediately.
            row.setParent(None)
            row.deleteLater()
        return index


class _LoginState:
    """The three fields that only ever change together around one login
    attempt. Behavior (starting a login, handling the result) stays on
    MusicPanel - unlike Library/Songs there is exactly one of these, so
    there is no second copy to de-duplicate, and every method already
    touches panel-owned widgets (the login row, the status label)."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.in_progress = False
        self.attempt = None


class _DetailState:
    """The reused now-playing view's data: which track it's currently
    showing, the track handed off from activation to display, the pending
    status line, and the Loader that refreshes the transport controls.

    is_current() is the one bit of real behavior worth centralizing: every
    async reply into this view (album art landing, a tile-state refresh, a
    playback/tile action finishing) must check it before touching a widget,
    since the view is reused across tracks rather than rebuilt and a slow
    reply for a track the user already left must not overwrite what they're
    looking at now."""

    def __init__(self, loader):
        self.loader = loader
        self.pending_track = None
        self.current_track_id = None
        self.status_pending = ""

    def is_current(self, track_id) -> bool:
        return track_id == self.current_track_id


class MusicPanel(Panel):
    def __init__(self):
        # 1500 matches the design mockup's Music panel — this got left behind
        # at 460 (a leftover from Phase A's small flat now-playing view) when
        # this panel grew into full Library/Songs/Detail browsing.
        super().__init__("Music", width=1500)
        # The one panel the mockup positions left-anchored instead of
        # centered under its tray icon — see OverlayWindow._relayout().
        self.anchor = "left"
        # Music's header is smaller than Sound/Power's in the
        # mockup (24px vs 32px) since it sits next to a small app icon.
        self.heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        # That "small app icon" the mockup leaves room for is the Spotify
        # mark, and putting it here rather than inside each view is what
        # attributes the library, the tracklist and the detail view in one
        # widget - the heading is above the view stack, so it is on screen
        # whichever of them is showing. _show_view hides it for the
        # logged-out/setup views, which display no Spotify content.
        self._spotify_logo = spotify_logo_label(20)
        self.heading_row.insertWidget(1, self._spotify_logo)

        # A QStackedWidget (not setVisible() on 4 sibling widgets in one
        # layout) — it sizes itself to only the *current* page, sidestepping
        # a real Qt quirk where hidden siblings in a plain layout still
        # threw off the panel's computed height across view switches.
        self._view_stack = QStackedWidget()
        self.body.addWidget(self._view_stack)

        self._build_logged_out_view()
        self._build_library_view()
        self._build_songs_view()
        self._build_detail_view()

        self._login = _LoginState(_LoginSignal())
        self._login.bridge.finished.connect(self._on_login_finished)
        self._detail = _DetailState(Loader(sp.submit, "music/detail"))
        # Which playlist the Detail back-label says it came from. Set in
        # _open_songs_view, read in _show_detail; unrelated to paging
        # mechanics so it stays flat rather than living on _PagedSection.
        self._current_playlist_name = "Liked Songs"
        self._current_playlist_id = None
        self._current_playlist = None
        self._liked_songs_total = 0

        # Nothing in this panel talks to Spotify on the Qt thread. Each
        # loader owns one slot of work and drops results the user has already
        # navigated past — open one playlist, back out, open another, and the
        # first one's songs must not land under the second one's heading.
        self._library = _PagedSection(
            Loader(sp.submit, "music/library"),
            self._library_rows_container,
            self._library_scroll,
            row_factory=lambda pl: _LibraryRow(
                pl.get("name", "Playlist"),
                # Spotify's own docs call this field "tracks", but the actual
                # API response uses "items" — checking both in case that
                # differs by account or API version.
                f"{(pl.get('tracks') or pl.get('items') or {}).get('total', 0)} songs",
                playlist_id=pl.get("id"),
                playlist=pl,
            ),
            noun="playlists",
        )
        self._songs = _PagedSection(
            Loader(sp.submit, "music/songs"),
            self._songs_rows_container,
            self._songs_scroll,
            row_factory=_TrackRow,
            noun="songs",
        )
        # Presses, not queries. A Loader would drop the first of two quick
        # presses; see the class comment in workers.py.
        self._commands = Commands(sp.submit, "music/commands")
        # Which of the three root views build_nav() settled on. on_enter fires
        # again every time Circle pops back to this level, and by then the
        # answer may have changed (a background login check can turn a
        # library into a login prompt), so it is looked up rather than baked
        # into the RowList.
        self._root_mode = "library"
        sp.register_session_reset(self._reset_spotify_session)

    # ---- view construction ----

    def _build_logged_out_view(self) -> None:
        self._logged_out_view = QWidget()
        lay = QVBoxLayout(self._logged_out_view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.5);")
        lay.addWidget(self._status_label)
        # Both rows live in this one view; whichever state the panel is in
        # shows one and hides the other (see _show_setup / _show_logged_out).
        self._setup_row = ActionRow("Set up Spotify…")
        lay.addWidget(self._setup_row)
        self._login_row = ActionRow("Log in with Spotify")
        lay.addWidget(self._login_row)
        self._view_stack.addWidget(self._logged_out_view)

    def _build_library_view(self) -> None:
        self._library_view = QWidget()
        lay = QVBoxLayout(self._library_view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        label = QLabel("YOUR LIBRARY")
        label.setStyleSheet(
            "font-size: 13px; font-weight: 700; letter-spacing: 1px;"
            " color: rgba(255,255,255,0.45);"
        )
        lay.addWidget(label)
        self._library_scroll, self._library_rows_container = make_scrollable_rows()
        lay.addWidget(self._library_scroll)
        self._view_stack.addWidget(self._library_view)

    def _build_songs_view(self) -> None:
        self._songs_view = QWidget()
        lay = QVBoxLayout(self._songs_view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        back = QLabel("‹ Music")
        back.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.4);")
        lay.addWidget(back)
        self._songs_header = QLabel("Liked Songs")
        self._songs_header.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(self._songs_header)
        self._songs_scroll, self._songs_rows_container = make_scrollable_rows()
        lay.addWidget(self._songs_scroll)
        self._view_stack.addWidget(self._songs_view)

    def _build_detail_view(self) -> None:
        self._detail_view = QWidget()
        lay = QVBoxLayout(self._detail_view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._detail_back_label = QLabel("‹ Liked Songs")
        self._detail_back_label.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.4);")
        lay.addWidget(self._detail_back_label)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)
        self._detail_art = QLabel()
        self._detail_art.setFixedSize(88, 88)
        content_row.addWidget(self._detail_art)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._detail_title = QLabel()
        self._detail_title.setStyleSheet("font-size: 22px; font-weight: 700;")
        title_row.addWidget(self._detail_title)
        self._detail_explicit_badge = _explicit_badge()
        self._detail_explicit_badge.hide()
        title_row.addWidget(self._detail_explicit_badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)
        self._detail_artist = QLabel()
        self._detail_artist.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.5);")
        text_col.addWidget(self._detail_artist)
        self._detail_status = QLabel()
        self._detail_status.setWordWrap(True)
        self._detail_status.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.5);")
        text_col.addWidget(self._detail_status)
        content_row.addLayout(text_col)
        content_row.addStretch(1)
        lay.addLayout(content_row)

        # Built once, reused every time Detail view shows a (single) song's
        # controls — only one song's Detail view is ever visible at a time.
        self._like_tile = _ToggleTile("like_outline")
        self._shuffle_tile = _ToggleTile("shuffle")
        self._prev_tile = Tile("previous")
        self._playpause_tile = Tile("play")
        self._next_tile = Tile("next")
        self._repeat_tile = _ToggleTile("repeat")
        # Opens this song in Spotify itself. Required, not a nicety: their
        # design guidelines say displayed metadata must always link back to the
        # Spotify service. Last in the row so it never sits between two playback
        # controls that get used mid-game.
        self._open_tile = Tile("external")
        self._tiles = [
            self._like_tile,
            self._shuffle_tile,
            self._prev_tile,
            self._playpause_tile,
            self._next_tile,
            self._repeat_tile,
            self._open_tile,
        ]
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        tiles_row.addStretch(1)
        for tile in self._tiles:
            tiles_row.addWidget(tile)
        tiles_row.addStretch(1)
        lay.addLayout(tiles_row)
        self._view_stack.addWidget(self._detail_view)

    # ---- view switching ----

    def _show_view(self, view: QWidget) -> None:
        # Attribution follows the content, not the panel: the setup and
        # logged-out views show no Spotify data, only prompts, so the mark
        # comes off there. Doing it here rather than in each _show_* method
        # means one place decides, and a view added later can't forget.
        self._spotify_logo.setVisible(view is not self._logged_out_view)
        self._view_stack.setCurrentWidget(view)
        # Deferred to the next event-loop tick rather than done here and
        # now: right after setCurrentWidget(), Qt hasn't yet finished
        # propagating the page switch through every widget level (stack ->
        # body -> panel), so sizing immediately gives a stale answer one
        # step behind — a single/multiple processEvents() call here still
        # wasn't enough to settle every level. Letting the event loop
        # finish this cycle first (imperceptible, <1 frame) is the robust
        # fix instead of guessing how many processEvents() calls suffice.
        QTimer.singleShot(0, lambda: self._finish_view_transition(view))

    def _finish_view_transition(self, view: QWidget) -> None:
        # QStackedWidget's own sizeHint isn't simply "the current page's
        # size" either — Qt factors other pages in too, so a previously-
        # visited taller view keeps inflating the reported height even
        # after switching back to a smaller one. Force it explicitly.
        self._view_stack.setFixedHeight(view.sizeHint().height())
        # setFixedHeight() sets the stack's geometry directly, which
        # bypasses the normal path Qt uses to know a parent layout's cached
        # sizeHint is now stale. Invalidating the panel's own top-level
        # layout doesn't reliably cascade into self.body (a *nested*
        # layout added via addLayout() in panels/base.py) — it must be
        # invalidated directly, or adjustSize() keeps using a one-
        # transition-stale size.
        self.body.invalidate()
        self.layout().invalidate()
        self.request_relayout()

    def _resettle(self, view) -> None:
        """Re-measure after a background load changed how tall a view wants to
        be. A view *switch* already does this (_show_view), but rows arriving
        later change the same thing without switching anything: the overlay
        sizes this panel from outside, so without asking again the list stays
        clipped to whatever the "Loading…" line needed."""
        if self._view_stack.currentWidget() is not view:
            return
        QTimer.singleShot(0, lambda: self._finish_view_transition(view))

    def _show_setup(self) -> None:
        self._status_label.setText(
            "Spotify needs a one-time setup before you can browse your songs. "
            "Press Cross to close the overlay and open Settings."
        )
        self._setup_row.show()
        self._login_row.hide()
        self._show_view(self._logged_out_view)

    def _show_logged_out(self) -> None:
        if not self._login.in_progress:
            self._status_label.setText("Log in to control Spotify from here.")
        self._setup_row.hide()
        self._login_row.show()
        self._show_view(self._logged_out_view)

    def _show_library(self) -> None:
        self._show_view(self._library_view)

    def _show_songs(self) -> None:
        self._show_view(self._songs_view)

    def _show_detail(self) -> None:
        track = self._detail.pending_track or {}
        self._detail.current_track_id = track.get("id")
        self._detail_back_label.setText(f"‹ {self._current_playlist_name}")
        self._detail_title.setText(track.get("name", ""))
        self._detail_explicit_badge.setVisible(bool(track.get("explicit")))
        self._detail_artist.setText(", ".join(a["name"] for a in track.get("artists", [])))
        self._detail_status.setText(self._detail.status_pending)
        self._refresh_tile_states()
        hue = abs(hash(track.get("id") or track.get("name", ""))) % 360
        self._detail_art.setStyleSheet(
            f"background: hsl({hue}, 45%, 40%);"
            f" border-radius: {album_art.CORNER_RADIUS}px;"
        )
        track_id = track.get("id")
        album_art.get(
            album_art.largest_image_url(track), 88, album_art.CORNER_RADIUS,
            lambda pixmap, tid=track_id: self._on_detail_art_loaded(pixmap, tid),
        )
        self._show_view(self._detail_view)

    def _on_detail_art_loaded(self, pixmap, track_id) -> None:
        # The Detail view is reused across different tracks (not rebuilt),
        # so a slow download for a track the user has since navigated away
        # from must not overwrite whatever's showing now.
        if pixmap is None or not self._detail.is_current(track_id):
            return
        self._detail_art.setStyleSheet("")
        self._detail_art.setPixmap(pixmap)

    # ---- setup / login ----

    def _reset_spotify_session(self) -> None:
        """Cancel old-account work and erase every account-derived UI value."""
        self._library.loader.cancel()
        self._songs.loader.cancel()
        self._detail.loader.cancel()
        self._commands.cancel_all()
        if self._login.attempt is not None:
            self._login.attempt.cancel()
        self._login.in_progress = False
        self._login.attempt = None
        self._login_row.set_text("Log in with Spotify")

        self._detail.pending_track = None
        self._detail.current_track_id = None
        self._current_playlist_id = None
        self._current_playlist = None
        self._current_playlist_name = "Liked Songs"
        self._liked_songs_total = 0
        self._library.reset()
        self._songs.reset()

        if self._library.nav is not None:
            self._library.nav.replace_rows([])
        if self._songs.nav is not None:
            self._songs.nav.replace_rows([])
        clear_layout(self._library_rows_container)
        clear_layout(self._songs_rows_container)

        self._songs_header.setText("Liked Songs")
        self._detail_back_label.setText("‹ Liked Songs")
        self._detail_title.clear()
        self._detail_artist.clear()
        self._detail_status.clear()
        self._detail_explicit_badge.hide()
        self._detail_art.clear()
        self._like_tile.set_icon_name("like_outline")
        self._like_tile.set_active(False)
        self._shuffle_tile.set_active(False)
        self._playpause_tile.set_icon_name("play")
        self._repeat_tile.set_icon_name("repeat")
        self._repeat_tile.set_active(False)

        self._root_mode = "logged_out" if sp.is_configured() else "setup"
        if self._root_mode == "logged_out":
            self._show_logged_out()
        else:
            self._show_setup()

    def _open_settings(self) -> None:
        # The overlay is frameless, always-on-top and deliberately holds the
        # foreground (see overlay._force_foreground), so a normal window shown
        # underneath it would be invisible. Closing the menu first hands focus
        # back to the desktop, and then the settings window can come forward.
        overlay = self.window()
        close_menu = getattr(overlay, "close_menu", None)
        if callable(close_menu):
            close_menu()
        settings_window.open_settings()

    def _start_login(self) -> None:
        if self._login.in_progress:
            if self._login.attempt is not None and self._login.attempt.cancel():
                self._status_label.setText("Cancelling Spotify login...")
            return
        self._login.in_progress = True
        self._login_row.set_text("Cancel Spotify login")
        self._status_label.setText(
            "Opening your browser to log in to Spotify. "
            "Press Cross again to cancel."
        )
        self._login.attempt = sp.login_async(
            lambda ok, err: self._login.bridge.finished.emit(ok, err or "")
        )

    def _on_login_finished(self, ok: bool, err: str) -> None:
        self._login.in_progress = False
        self._login.attempt = None
        self._login_row.set_text("Log in with Spotify")
        if ok:
            self._status_label.setText("Logged in! Press Circle, then reopen Music.")
        else:
            self._status_label.setText(f"Login failed: {err}")

    # ---- library ----

    def _start_library_load(self) -> None:
        """Fetch the first page of the library in the background. Whatever is
        already on screen (last time's playlists, or nothing at all) stays put
        until the answer arrives.

        Kept separate from _PagedSection's shared paging (rather than forced
        through a hook): this one call also has to check the cached login
        token (a network round trip, which is exactly why it can't happen on
        the Qt thread that opens the panel) and fetch the Liked Songs count -
        neither of which Songs' first load needs to do."""
        # This supersedes any page request already queued on the same loader,
        # so that request's callback will never run. Clearing the flag here
        # rather than only in that callback is what stops paging latching on
        # permanently: see the note on _PagedSection.paging.
        self._library.paging = False

        def work():
            # Doubles as the login check: validating the cached token can
            # refresh it over the network, which is exactly the kind of thing
            # that has no business on the Qt thread.
            if not sp.is_logged_in():
                return None
            try:
                liked_total = sp.get_liked_songs_total()
            except Exception:
                # The count is decoration on one row; losing it shouldn't cost
                # the user the whole library.
                log.exception("Couldn't read the Liked Songs count")
                liked_total = 0
            playlists, total, consumed = sp.get_playlists_page(
                limit=_PAGE_SIZE, offset=0
            )
            return {
                "liked_total": liked_total,
                "playlists": playlists,
                "total": total,
                "consumed": consumed,
            }

        self._library.loader.start(work, self._on_library_loaded)

    def _on_library_loaded(self, value, error) -> None:
        if error is not None:
            log.exception(
                "Couldn't fetch the user's playlists", exc_info=error
            )
            self._library.load_failed = True
            self._render_library_rows()
            return
        if value is None:
            self._show_login_prompt()
            return
        self._liked_songs_total = value["liked_total"]
        self._library.items = list(value["playlists"])
        self._library.offset = value["consumed"]
        # An empty first page while `total` still claims there's more would
        # leave a Load More row that can never load anything — trust what
        # actually consumed over the count. Fixed here to use `consumed`
        # (2026-09-05), matching every other paging call site
        # (_PagedSection._on_page, and this method's own sibling
        # _on_songs_loaded below) — this one call site was still checking
        # `value["playlists"]` truthiness, a leftover from before commit
        # e5b2532 introduced the consumed/displayed distinction and updated
        # the other three sites but missed this one. A first page that
        # consumed entries but produced zero displayable playlists (the same
        # shape of gap the unavailable-track fixture already covers for
        # tracks) would otherwise have looked like the end of the library
        # when more was still there to load.
        self._library.total = (
            value["total"] if value["consumed"] else len(self._library.items)
        )
        self._library.loaded = True
        self._library.load_failed = False
        self._render_library_rows()

    def _show_login_prompt(self) -> None:
        """The cached token turned out to be no good. The level that was
        going to be the library becomes the login prompt instead."""
        self._root_mode = "logged_out"
        self._library.items = []
        self._library.loaded = False
        if self._library.nav is not None:
            self._library.nav.replace_rows([self._login_row])
        self._show_root_view()

    def _render_library_rows(self) -> None:
        liked_row = _LibraryRow(
            "Liked Songs", f"Playlist · {self._liked_songs_total} songs", playlist_id=None
        )
        self._library.render(
            lambda: self._resettle(self._library_view),
            prefix_rows=[liked_row],
        )

    def _page_in_more_playlists(self) -> None:
        self._library.page_in_more(
            lambda offset: sp.get_playlists_page(limit=_PAGE_SIZE, offset=offset),
            lambda: self._resettle(self._library_view),
            on_error=lambda error: log.exception(
                "Couldn't fetch the user's playlists", exc_info=error
            ),
        )

    def _open_songs_view(self, playlist_id, playlist_name: str, playlist=None) -> None:
        self._current_playlist_name = playlist_name
        self._current_playlist_id = playlist_id
        # Kept so the Songs view can offer a way back to this playlist on
        # Spotify. None for Liked Songs, which is a library section rather
        # than a playlist and has no page to open.
        self._current_playlist = playlist
        self._songs_header.setText(playlist_name)
        # Reopening the playlist that's still cached shows its songs at once;
        # any other one starts empty rather than briefly showing the wrong
        # playlist's tracks under this one's heading.
        if self._songs.cache_key != playlist_id:
            self._songs.items = []
            self._songs.total = 0
            self._songs.offset = 0
            self._songs.loaded = False
        self._songs.load_failed = False
        self._songs.nav = RowList(
            [],
            on_activate=self._on_songs_activate,
            on_select=lambda i, row: self._songs_scroll.ensureWidgetVisible(row),
            orientation="vertical",
            on_enter=self._show_songs,
        )
        self._render_song_rows()
        self.nav.push(self._songs.nav)
        self._start_songs_load(playlist_id)

    def _on_songs_activate(self, index, row) -> None:
        if isinstance(row, _OpenPlaylistRow):
            self._open_playlist_in_spotify()
            return
        if isinstance(row, _LoadMoreRow):
            self._page_in_more_songs()
            return
        self._on_song_activated(index, row)

    def _open_playlist_in_spotify(self) -> None:
        """Spotify's guidelines require displayed metadata to link back to
        the service. Every *track* already does, through the Detail view's
        open tile; a playlist's name and track count are displayed metadata
        too, and until this existed they were the one thing on screen with no
        way back."""
        if not open_in_spotify(self, self._current_playlist or {}):
            log.warning(
                "No Spotify link on playlist %r", self._current_playlist_id
            )

    def _start_songs_load(self, playlist_id) -> None:
        # Opening a playlist supersedes any page request still queued for the
        # previous one, which means _PagedSection._on_page will never run for
        # it. The flag has to be cleared here as well as there, or leaving a
        # playlist mid-page leaves this panel unable to page anything for the
        # rest of the session - which is exactly what happened.
        self._songs.paging = False

        def work():
            return _fetch_songs_page(playlist_id, 0)

        self._songs.loader.start(
            work, lambda value, error: self._on_songs_loaded(playlist_id, value, error)
        )

    def _on_songs_loaded(self, playlist_id, value, error) -> None:
        if error is not None:
            # The most likely cause of a mysteriously empty song list, and
            # historically a response-shape mismatch rather than a network
            # problem — see HANDOFF.md gotcha #7.
            log.exception(
                "Couldn't fetch tracks for playlist %r", playlist_id, exc_info=error
            )
            self._songs.load_failed = True
            self._render_song_rows()
            return
        tracks, total, consumed = value
        self._songs.load_failed = False
        self._songs.items = list(tracks)
        self._songs.offset = consumed
        # See _on_library_loaded: "nothing displayable" and "nothing at all"
        # are different answers.
        self._songs.total = total if consumed else len(self._songs.items)
        self._songs.cache_key = playlist_id
        self._songs.loaded = True
        self._render_song_rows()

    def _render_song_rows(self) -> None:
        # The link row is offered only when the playlist actually carries a
        # link (links_for needs a uri or an external_urls entry), so a
        # library entry without one - Liked Songs - simply doesn't get a row
        # that would fail when pressed.
        prefix = []
        if any(sp.links_for(self._current_playlist or {})):
            prefix.append(_OpenPlaylistRow(f"Open {self._current_playlist_name} in Spotify"))
        self._songs.render(
            lambda: self._resettle(self._songs_view),
            prefix_rows=prefix,
            # Land on the first track, not on the link: opening a playlist is
            # something you do to play a song.
            default_index=len(prefix),
        )

    def _page_in_more_songs(self) -> None:
        playlist_id = self._current_playlist_id
        self._songs.page_in_more(
            lambda offset: _fetch_songs_page(playlist_id, offset),
            lambda: self._resettle(self._songs_view),
            on_error=lambda error: log.exception(
                "Couldn't fetch tracks for playlist %r", playlist_id, exc_info=error
            ),
        )

    def _on_song_activated(self, index, row) -> None:
        track = row.track
        self._detail.pending_track = track
        # The Detail view opens on the press, not when Spotify answers. Whether
        # playback actually started is reported into the status line underneath
        # once the request comes back — the old order made every song press
        # sit on a blank overlay for a network round trip first.
        self._detail.status_pending = ""
        uri = track.get("uri")

        detail_list = RowList(
            self._tiles,
            on_activate=self._on_tile_activated,
            orientation="horizontal",
            on_enter=self._show_detail,
        )
        self.nav.push(detail_list)

        if not uri:
            return
        track_id = track.get("id")
        self._commands.run(
            lambda: sp.play_track(uri),
            lambda value, error: self._on_playback_started(uri, track_id, error),
        )

    def _on_playback_started(self, uri: str, track_id, error) -> None:
        # The command itself always runs, even if the user closed the overlay
        # on the way out - picking a song and going back to the game is the
        # normal way to use this. Only the *feedback* is conditional, because
        # by now they may be looking at a different song.
        if not self._detail.is_current(track_id):
            return
        if error is None:
            self._detail_status.setText("")
            self._refresh_tile_states()
            return
        if isinstance(error, sp.PlaybackUnavailable):
            message = _UNAVAILABLE_MESSAGES.get(
                error.reason, _UNAVAILABLE_MESSAGES["other"]
            )
        else:
            log.exception("Couldn't start playback for %r", uri, exc_info=error)
            message = _UNAVAILABLE_MESSAGES["other"]
        self._detail_status.setText(message)

    # ---- detail / playback ----

    def _refresh_tile_states(self) -> None:
        """Ask Spotify what the transport controls should look like. Two
        network calls, so the tiles keep showing their last state until the
        answer lands rather than the panel waiting on it."""
        track_id = self._detail.current_track_id

        def work():
            try:
                playback = sp.get_current_playback()
            except Exception:
                log.exception("Couldn't read current playback state")
                playback = None
            liked = None
            if track_id:
                try:
                    liked = sp.is_liked(track_id)
                except Exception:
                    log.exception("Couldn't check liked state for %r", track_id)
                    liked = False
            return playback, liked

        self._detail.loader.start(
            work, lambda value, error: self._on_tile_states(track_id, value, error)
        )

    def _on_tile_states(self, track_id, value, error) -> None:
        # The Detail view is reused across tracks rather than rebuilt, so a
        # slow answer for a song the user has already left must not repaint
        # the controls for the one they're looking at now — the same guard
        # _on_detail_art_loaded needs, for the same reason.
        if error is not None or not self._detail.is_current(track_id):
            return
        playback, liked = value
        if playback:
            self._playpause_tile.set_icon_name("pause" if playback.get("is_playing") else "play")
            self._shuffle_tile.set_active(bool(playback.get("shuffle_state")))
            repeat_state = playback.get("repeat_state", "off")
            self._repeat_tile.set_active(repeat_state != "off")
            self._repeat_tile.set_icon_name("repeat_one" if repeat_state == "track" else "repeat")

        if liked is not None:
            self._like_tile.set_icon_name("like_filled" if liked else "like_outline")
            self._like_tile.set_active(liked)

    def _on_tile_activated(self, index, tile) -> None:
        """A tile's action() runs here on the Qt thread and returns the network
        call to make, or None if there wasn't one. Splitting it that way keeps
        the part that reads the tiles' own state (is it liked right now?
        shuffled?) on the thread that owns those widgets, while the request
        itself goes to the worker."""
        name = getattr(tile, "_icon_name", "?")
        work = tile.action()
        if work is None:
            return
        self._detail_status.setText("")
        track_id = self._detail.current_track_id
        self._commands.run(
            work, lambda value, error: self._on_tile_action_done(name, track_id, error)
        )

    def _on_tile_action_done(self, name: str, track_id, error) -> None:
        if not self._detail.is_current(track_id):
            return  # see _on_playback_started: the press ran, the report is stale
        if error is None:
            self._detail_status.setText("")
            self._refresh_tile_states()
            return
        if isinstance(error, sp.PlaybackUnavailable):
            self._detail_status.setText(
                _UNAVAILABLE_MESSAGES.get(error.reason, _UNAVAILABLE_MESSAGES["other"])
            )
            return
        log.exception("Playback control %r failed", name, exc_info=error)
        self._detail_status.setText(_UNAVAILABLE_MESSAGES["other"])

    def _toggle_like(self):
        track_id = self._detail.current_track_id
        if not track_id:
            return None
        liked_now = self._like_tile._active
        return lambda: sp.set_liked(track_id, not liked_now)

    def _open_current_in_spotify(self):
        # Handing a URL to Windows, not a Spotify request — it belongs on this
        # thread and there is nothing to run in the background afterwards.
        if not open_in_spotify(self, self._detail.pending_track or {}):
            self._detail_status.setText("Couldn't open this song in Spotify.")
        return None

    def _toggle_shuffle(self):
        shuffled_now = self._shuffle_tile._active
        return lambda: sp.set_shuffle(not shuffled_now)

    def _cycle_repeat(self):
        def work():
            try:
                playback = sp.get_current_playback()
                current = playback.get("repeat_state", "off") if playback else "off"
            except Exception:
                log.exception("Couldn't read repeat state; assuming off")
                current = "off"
            next_mode = _REPEAT_CYCLE[
                (_REPEAT_CYCLE.index(current) + 1) % len(_REPEAT_CYCLE)
            ]
            sp.set_repeat(next_mode)

        return work

    # ---- nav entry point ----

    def build_nav(self):
        # Each action is called on the Qt thread and returns the Spotify call
        # to run in the background (or None) — see _on_tile_activated.
        for tile, action in (
            (self._like_tile, self._toggle_like),
            (self._shuffle_tile, self._toggle_shuffle),
            (self._prev_tile, lambda: sp.previous_track),
            (self._playpause_tile, lambda: sp.play_pause),
            (self._next_tile, lambda: sp.next_track),
            (self._repeat_tile, self._cycle_repeat),
            (self._open_tile, self._open_current_in_spotify),
        ):
            tile.action = action

        # No client ID yet: the only useful thing a D-pad can do here is send
        # the user to the desktop Settings window. A local settings read, so
        # unlike every other question this panel asks it can be answered on
        # the spot.
        if not sp.is_configured():
            self._root_mode = "setup"
            return RowList(
                [self._setup_row],
                on_activate=self._on_root_activate,
                orientation="horizontal",
                on_enter=self._show_root_view,
            )

        # Whether the cached token is still good is *not* answered here.
        # Checking it can refresh it, which is a network round trip, and this
        # runs on the press that opens the panel. It goes to the worker with
        # the library fetch instead, and the answer either fills the library
        # in or turns this level into the login prompt.
        self._root_mode = "library"
        self._library.nav = RowList(
            [],
            on_activate=self._on_root_activate,
            on_select=lambda i, row: self._library_scroll.ensureWidgetVisible(row),
            orientation="vertical",
            on_enter=self._show_root_view,
        )
        self._render_library_rows()
        self._start_library_load()
        return self._library.nav

    def _show_root_view(self) -> None:
        """Which view this panel's first nav level is showing. Looked up on
        every enter because a background login check can change the answer
        after the level was already pushed."""
        if self._root_mode == "setup":
            self._show_setup()
        elif self._root_mode == "logged_out":
            self._show_logged_out()
        else:
            self._show_library()

    def _on_root_activate(self, index, row) -> None:
        """Cross on the first nav level. That level is one of three different
        things depending on what the background check found, so the row
        itself says what to do rather than the panel tracking it twice."""
        if row is self._setup_row:
            self._open_settings()
        elif row is self._login_row:
            self._start_login()
        elif isinstance(row, _LoadMoreRow):
            self._page_in_more_playlists()
        else:
            self._open_songs_view(
                row.playlist_id, row.playlist_name, getattr(row, "playlist", None)
            )
