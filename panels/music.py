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
from panels.base import (
    ActionRow,
    Panel,
    Tile,
    clear_layout,
    fit_scroll_to_content,
    make_scrollable_rows,
    open_in_spotify,
    selected_row_style,
)

log = logs.get(__name__)

_REPEAT_CYCLE = ["off", "context", "track"]
_PAGE_SIZE = 20

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

    def __init__(self, title: str, subtitle: str, playlist_id):
        super().__init__()
        self.setObjectName("row")
        self.playlist_id = playlist_id
        self.playlist_name = title
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
        text = QLabel(label)
        color = "rgba(255,255,255,0.5)" if failed else "#3ddc97"
        text.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {color};")
        lay.addWidget(text)
        lay.addStretch(1)
        self.set_selected(False)

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

        self._login_bridge = _LoginSignal()
        self._login_bridge.finished.connect(self._on_login_finished)
        self._logging_in = False
        self._pending_track = None
        self._detail_status_pending = ""
        self._current_track_id = None
        self._current_playlist_name = "Liked Songs"
        self._liked_songs_total = 0
        self._library_playlists = []
        self._library_total = 0
        self._song_tracks = []
        self._song_total = 0
        self._library_load_failed = False
        self._song_load_failed = False
        self._current_playlist_id = None

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
        self._library_rows = []

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
        self._song_rows = []

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

    def _show_setup(self) -> None:
        self._status_label.setText(
            "Spotify needs a one-time setup before you can browse your songs. "
            "Press Cross to close the overlay and open Settings."
        )
        self._setup_row.show()
        self._login_row.hide()
        self._show_view(self._logged_out_view)

    def _show_logged_out(self) -> None:
        if not self._logging_in:
            self._status_label.setText("Log in to control Spotify from here.")
        self._setup_row.hide()
        self._login_row.show()
        self._show_view(self._logged_out_view)

    def _show_library(self) -> None:
        self._show_view(self._library_view)

    def _show_songs(self) -> None:
        self._show_view(self._songs_view)

    def _show_detail(self) -> None:
        track = self._pending_track or {}
        self._current_track_id = track.get("id")
        self._detail_back_label.setText(f"‹ {self._current_playlist_name}")
        self._detail_title.setText(track.get("name", ""))
        self._detail_explicit_badge.setVisible(bool(track.get("explicit")))
        self._detail_artist.setText(", ".join(a["name"] for a in track.get("artists", [])))
        self._detail_status.setText(self._detail_status_pending)
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
        if pixmap is None or track_id != self._current_track_id:
            return
        self._detail_art.setStyleSheet("")
        self._detail_art.setPixmap(pixmap)

    # ---- setup / login ----

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
        if self._logging_in:
            return
        self._logging_in = True
        self._status_label.setText("Opening your browser to log in to Spotify…")
        sp.login_async(lambda ok, err: self._login_bridge.finished.emit(ok, err or ""))

    def _on_login_finished(self, ok: bool, err: str) -> None:
        self._logging_in = False
        if ok:
            self._status_label.setText("Logged in! Press Circle, then reopen Music.")
        else:
            self._status_label.setText(f"Login failed: {err}")

    # ---- library ----

    def _build_library_nav(self) -> RowList:
        try:
            total = sp.get_liked_songs_total()
        except Exception:
            log.exception("Couldn't read the Liked Songs count")
            total = 0
        self._liked_songs_total = total
        self._library_playlists = []
        self._library_total = 0
        self._library_load_failed = False
        self._load_library_page()
        return self._render_library_nav()

    def _load_library_page(self) -> None:
        try:
            playlists, total = sp.get_playlists_page(
                limit=_PAGE_SIZE, offset=len(self._library_playlists)
            )
        except Exception:
            # Keep the pages already loaded — a failed continuation must
            # never wipe out playlists the user can still browse. The Load
            # More row says so and stays pressable to retry.
            log.exception("Couldn't fetch the user's playlists")
            self._library_load_failed = True
            return
        self._library_load_failed = False
        self._library_playlists.extend(playlists)
        # An empty page while `total` still claims there's more would leave
        # a Load More row that can never load anything — trust what actually
        # came back over the count.
        self._library_total = total if playlists else len(self._library_playlists)

    def _render_library_nav(self) -> RowList:
        clear_layout(self._library_rows_container)
        liked_row = _LibraryRow(
            "Liked Songs", f"Playlist · {self._liked_songs_total} songs", playlist_id=None
        )
        self._library_rows = [liked_row]
        self._library_rows_container.addWidget(liked_row)
        self._add_library_rows(0)

        def on_activate(index, row):
            if isinstance(row, _LoadMoreRow):
                self._page_in_more_playlists()
                return
            self._open_songs_view(row.playlist_id, row.playlist_name)

        return RowList(
            self._library_rows,
            on_activate=on_activate,
            on_select=lambda i, row: self._library_scroll.ensureWidgetVisible(row),
            orientation="vertical",
            on_enter=self._show_library,
        )

    def _add_library_rows(self, rendered: int) -> None:
        """Add the playlists from `rendered` onward, then a Load More row if
        the library still has pages left."""
        for pl in self._library_playlists[rendered:]:
            name = pl.get("name", "Playlist")
            # Spotify's own docs call this field "tracks", but the actual
            # API response uses "items" — checking both in case that
            # differs by account or API version.
            tracks_info = pl.get("tracks") or pl.get("items") or {}
            count = tracks_info.get("total", 0)
            row = _LibraryRow(name, f"{count} songs", playlist_id=pl.get("id"))
            self._library_rows.append(row)
            self._library_rows_container.addWidget(row)

        if len(self._library_playlists) < self._library_total:
            more_row = _LoadMoreRow(
                _load_more_label("playlists", self._library_load_failed),
                self._library_load_failed,
            )
            self._library_rows.append(more_row)
            self._library_rows_container.addWidget(more_row)

        fit_scroll_to_content(self._library_scroll)

    def _page_in_more_playlists(self) -> None:
        rendered = len(self._library_playlists)
        self._load_library_page()
        selected = self._drop_selected_load_more(
            self._library_rows, self._library_rows_container
        )
        self._add_library_rows(rendered)
        self.nav.current().reselect(selected)

    def _open_songs_view(self, playlist_id, playlist_name: str) -> None:
        self._current_playlist_name = playlist_name
        self._current_playlist_id = playlist_id
        self._songs_header.setText(playlist_name)
        self._song_tracks = []
        self._song_rows = []
        self._song_total = 0
        self._song_load_failed = False
        self._load_songs_page()
        self._push_songs_nav()

    def _load_songs_page(self) -> None:
        offset = len(self._song_tracks)
        try:
            if self._current_playlist_id is None:
                tracks, total = sp.get_liked_songs_page(limit=_PAGE_SIZE, offset=offset)
            else:
                tracks, total = sp.get_playlist_tracks_page(
                    self._current_playlist_id, limit=_PAGE_SIZE, offset=offset
                )
        except Exception:
            # The most likely cause of a mysteriously empty song list, and
            # historically a response-shape mismatch rather than a network
            # problem — see HANDOFF.md gotcha #7. Keep the rows already
            # loaded so a failed later page never erases music the user can
            # still browse.
            log.exception("Couldn't fetch tracks for playlist %r", self._current_playlist_id)
            self._song_load_failed = True
            return
        self._song_load_failed = False
        self._song_tracks.extend(tracks)
        # See _load_library_page for why an empty page overrides `total`.
        self._song_total = total if tracks else len(self._song_tracks)

    def _push_songs_nav(self) -> None:
        clear_layout(self._songs_rows_container)
        self._song_rows = []
        self._add_song_rows(0)

        def on_activate(index, row):
            if isinstance(row, _LoadMoreRow):
                self._page_in_more_songs()
                return
            self._on_song_activated(index, row)

        self.nav.push(
            RowList(
                self._song_rows,
                on_activate=on_activate,
                on_select=lambda i, row: self._songs_scroll.ensureWidgetVisible(row),
                orientation="vertical",
                on_enter=self._show_songs,
            )
        )

    def _add_song_rows(self, rendered: int) -> None:
        """Add the tracks from `rendered` onward, then a Load More row if the
        tracklist still has pages left."""
        for track in self._song_tracks[rendered:]:
            row = _TrackRow(track)
            self._song_rows.append(row)
            self._songs_rows_container.addWidget(row)

        if len(self._song_tracks) < self._song_total:
            more_row = _LoadMoreRow(
                _load_more_label("songs", self._song_load_failed),
                self._song_load_failed,
            )
            self._song_rows.append(more_row)
            self._songs_rows_container.addWidget(more_row)

        fit_scroll_to_content(self._songs_scroll)

    def _page_in_more_songs(self) -> None:
        rendered = len(self._song_tracks)
        self._load_songs_page()
        selected = self._drop_selected_load_more(
            self._song_rows, self._songs_rows_container
        )
        self._add_song_rows(rendered)
        self.nav.current().reselect(selected)

    def _drop_selected_load_more(self, rows, container) -> int:
        """Take the pressed Load More row off the end of a paged list and
        report the position it held, so the first row of the newly loaded page
        can take over the selection.

        A page is appended to the RowList already on screen rather than built
        into a replacement one: rebuilding re-created and re-styled every row
        each time, so each press cost more than the last (about a second of
        frozen UI by the thousandth track) — exactly the size of library
        paging exists to serve.
        """
        index = len(rows) - 1
        if rows and isinstance(rows[-1], _LoadMoreRow):
            row = rows.pop()
            container.removeWidget(row)
            # deleteLater() alone isn't enough: Qt only destroys the widget
            # once control is back in the event loop, so until then the row
            # carries on painting where it was and the first row of the new
            # page renders on top of it. Unparenting hides it immediately.
            row.setParent(None)
            row.deleteLater()
        return index

    def _on_song_activated(self, index, row) -> None:
        track = row.track
        try:
            sp.play_track(track["uri"])
        except sp.PlaybackUnavailable as e:
            self._detail_status_pending = _UNAVAILABLE_MESSAGES.get(
                e.reason, _UNAVAILABLE_MESSAGES["other"]
            )
        except Exception:
            log.exception("Couldn't start playback for %r", track.get("uri"))
            self._detail_status_pending = _UNAVAILABLE_MESSAGES["other"]
        else:
            self._detail_status_pending = ""
        self._pending_track = track

        detail_list = RowList(
            self._tiles,
            on_activate=self._on_tile_activated,
            orientation="horizontal",
            on_enter=self._show_detail,
        )
        self.nav.push(detail_list)

    # ---- detail / playback ----

    def _refresh_tile_states(self) -> None:
        try:
            playback = sp.get_current_playback()
        except Exception:
            log.exception("Couldn't read current playback state")
            playback = None

        if playback:
            self._playpause_tile.set_icon_name("pause" if playback.get("is_playing") else "play")
            self._shuffle_tile.set_active(bool(playback.get("shuffle_state")))
            repeat_state = playback.get("repeat_state", "off")
            self._repeat_tile.set_active(repeat_state != "off")
            self._repeat_tile.set_icon_name("repeat_one" if repeat_state == "track" else "repeat")

        if self._current_track_id:
            try:
                liked = sp.is_liked(self._current_track_id)
            except Exception:
                log.exception("Couldn't check liked state for %r", self._current_track_id)
                liked = False
            self._like_tile.set_icon_name("like_filled" if liked else "like_outline")
            self._like_tile.set_active(liked)

    def _on_tile_activated(self, index, tile) -> None:
        try:
            tile.action()
        except sp.PlaybackUnavailable as e:
            self._detail_status.setText(
                _UNAVAILABLE_MESSAGES.get(e.reason, _UNAVAILABLE_MESSAGES["other"])
            )
            return
        except Exception:
            log.exception("Playback control %r failed", getattr(tile, "_icon_name", "?"))
            self._detail_status.setText(_UNAVAILABLE_MESSAGES["other"])
            return
        self._detail_status.setText("")
        self._refresh_tile_states()

    def _toggle_like(self) -> None:
        if self._current_track_id:
            sp.set_liked(self._current_track_id, not self._like_tile._active)

    def _open_current_in_spotify(self) -> None:
        if not open_in_spotify(self, self._pending_track or {}):
            self._detail_status.setText("Couldn't open this song in Spotify.")

    def _toggle_shuffle(self) -> None:
        sp.set_shuffle(not self._shuffle_tile._active)

    def _cycle_repeat(self) -> None:
        try:
            playback = sp.get_current_playback()
            current = playback.get("repeat_state", "off") if playback else "off"
        except Exception:
            log.exception("Couldn't read repeat state; assuming off")
            current = "off"
        next_mode = _REPEAT_CYCLE[(_REPEAT_CYCLE.index(current) + 1) % len(_REPEAT_CYCLE)]
        sp.set_repeat(next_mode)

    # ---- nav entry point ----

    def build_nav(self):
        for tile, action in (
            (self._like_tile, self._toggle_like),
            (self._shuffle_tile, self._toggle_shuffle),
            (self._prev_tile, sp.previous_track),
            (self._playpause_tile, sp.play_pause),
            (self._next_tile, sp.next_track),
            (self._repeat_tile, self._cycle_repeat),
            (self._open_tile, self._open_current_in_spotify),
        ):
            tile.action = action

        # No client ID yet: the only useful thing a D-pad can do here is send
        # the user to the desktop Settings window.
        if not sp.is_configured():
            return RowList(
                [self._setup_row],
                on_activate=lambda i, r: self._open_settings(),
                orientation="horizontal",
                on_enter=self._show_setup,
            )

        try:
            logged_in = sp.is_logged_in()
        except Exception:
            log.exception("Couldn't validate the cached Spotify token")
            logged_in = False

        if not logged_in:
            return RowList(
                [self._login_row],
                on_activate=lambda i, r: self._start_login(),
                orientation="horizontal",
                on_enter=self._show_logged_out,
            )

        return self._build_library_nav()
