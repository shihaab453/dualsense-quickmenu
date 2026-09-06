# Phase A shows the real current track as a read-only view — the full
# mockup layout (progress bar, like/shuffle/repeat, volume sub-view, 3-dot
# submenu) is a bigger undertaking than this card needs, since the same
# controls already exist one tap away in the Music panel's own Detail view
# (reached via the Music tray icon). Prefers Spotify's own playback state
# when logged in (accurate to what "Music" is actually playing), falling
# back to the Windows media session when not logged in or nothing's there.
#
# Both sources are Spotify-only. The media session reading used to report
# whatever was playing anywhere - a browser, VLC - and this panel displayed
# it; actions/now_playing.py now filters by the session's owning application,
# so it returns Spotify or nothing. Read that module's comment before changing
# anything here: the filter is a policy requirement, and this panel's
# attribution rules below depend on it holding.

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

import logs
from actions import album_art
from actions import now_playing
from actions import spotify_client as sp
from nav import RowList
from panels.base import ActionRow, Panel, open_in_spotify, spotify_logo_label
from workers import MEDIA, Loader

log = logs.get(__name__)

_ART_SIZE = 64
# Panel width (460) minus its own left+right margins (36 each, from
# panels/base.py's Panel), minus the art label's width and the row's
# spacing between it and the text.
_SONG_LABEL_WIDTH = 460 - 36 - 36 - _ART_SIZE - 14

# Stands in for a track when the media session gave us metadata but no
# addressable item. Shaped like a Spotify object on purpose, so it goes
# through the same links_for/open_in_spotify path as everything else rather
# than needing a second way to open a link.
_SPOTIFY_SERVICE = {
    "uri": "spotify:",
    "external_urls": {"spotify": "https://open.spotify.com"},
}


class NowPlayingPanel(Panel):
    def __init__(self):
        # Replaced per-open by build_nav with the real source; this is only
        # what shows before the first refresh.
        super().__init__("Now playing", width=460)
        # Smaller/lighter than Sound/Power's 32px bold titles —
        # matches the mockup's own header style for this panel (19px/600).
        self.heading.setStyleSheet("font-size: 19px; font-weight: 600;")

        # A real QWidget wrapper (not a bare addLayout()) — nested layouts
        # added directly via addLayout() have repeatedly caused stale/
        # uninvalidated sizeHint bugs elsewhere in this app (see panels/
        # music.py's Detail-view history); every other panel's rows use a
        # QFrame/QWidget for exactly this reason, so this one does too.
        # Attribution sits next to the heading, and like the heading text and
        # the open-in-Spotify row below it, it appears whenever Spotify
        # content is on screen - from either source, since both are now
        # Spotify-only. It is still a toggle rather than always-on, because
        # "Nothing playing" and "Loading…" are this panel's own words and
        # attributing those to Spotify would be crediting them for nothing.
        # _render() owns it.
        self._spotify_logo = spotify_logo_label(22)
        self._spotify_logo.hide()
        self.heading_row.insertWidget(1, self._spotify_logo)

        content_widget = QWidget()
        # Fixed vertical policy: without this, the panel's leftover height
        # (whatever the note label below needs but doesn't get, e.g. once
        # its text wraps to 2 lines) gets handed to this row instead of the
        # note — Qt hands slack space to whichever sibling's policy allows
        # growing, and a plain QWidget's default policy does.
        content_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        content_row = QHBoxLayout(content_widget)
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(14)
        self._art_label = QLabel()
        self._art_label.setFixedSize(_ART_SIZE, _ART_SIZE)
        content_row.addWidget(self._art_label, 0, Qt.AlignTop)

        # A bare addLayout()'d column here (even holding just this one
        # label) reproduced the exact same stale-sizeHint bug one level
        # deeper — every raw nested QLayout in this app's history has
        # eventually needed a QWidget boundary, so skip adding one at all
        # and put the label straight on content_row instead.
        self._song_label = QLabel()
        self._song_label.setStyleSheet("font-size: 24px; font-weight: 700;")
        self._song_label.setWordWrap(True)
        # QHBoxLayout doesn't reliably support heightForWidth children (a
        # word-wrapped QLabel needs its height computed *from* its assigned
        # width) when mixed with a fixed-size sibling like the art label —
        # a real, reproducible Qt limitation, not something specific to
        # this app. It caused the wrapped title to visibly double-paint/
        # corrupt itself once it needed more than 2 lines. Giving the label
        # an explicit fixed width sidesteps the negotiation entirely: Qt
        # can compute its wrap height directly from a width that's already
        # known, instead of through the HBox.
        self._song_label.setFixedWidth(_SONG_LABEL_WIDTH)
        content_row.addWidget(self._song_label, 0, Qt.AlignVCenter)
        self.body.addWidget(content_widget)

        # What the track is playing *from* - an album title or a playlist
        # name. The home card shows this too but truncates it to one line,
        # and unlike the title and artist it had nowhere to be read in full:
        # opening this panel showed the same metadata minus the source
        # entirely. Spotify's guidelines allow truncating displayed metadata
        # only where full viewing capability exists somewhere, so this is
        # where the card's third line becomes readable.
        #
        # A direct child of `body` (a plain QVBoxLayout), deliberately not
        # inside content_row: that HBox already has one word-wrapped label
        # negotiating height against a fixed-size sibling, which is the
        # arrangement the comment above says corrupted itself once.
        self._source_label = QLabel()
        self._source_label.setWordWrap(True)
        self._source_label.setStyleSheet(
            "font-size: 15px; color: rgba(255,255,255,0.55);"
        )
        self._source_label.hide()
        self.body.addWidget(self._source_label)

        self._current_art_id = None
        self._current_track = None
        # What the last look found, so reopening the panel shows it at once
        # instead of blanking while the same two lookups run again.
        self._last_result = None
        # Both lookups are slow enough to matter and neither belongs on the
        # Qt thread: Spotify is a network round trip, and the Windows media
        # session is a WinRT call that has to spin up its own event loop.
        self._spotify_loader = Loader(sp.submit, "nowplaying/spotify")
        self._winrt_loader = Loader(MEDIA.submit, "nowplaying/winrt")
        self._spotify_pending = False
        self._winrt_pending = False
        self._spotify_track = None
        self._winrt_info = None
        self._nav = None
        self._reset_art_placeholder()

        # Built once and shown/hidden per build_nav, rather than created and
        # destroyed — the RowList holds a reference to it either way.
        self._open_row = ActionRow("Open in Spotify")
        self._open_row.hide()
        self.body.addWidget(self._open_row)

        note = QLabel(
            "Progress and playback controls are coming in a later update."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.35);")
        # This panel's width is fixed, so this text always wraps to exactly
        # 2 lines here — pinning the height directly sidesteps a layout
        # quirk where QVBoxLayout wasn't reliably giving a word-wrapped
        # QLabel its full heightForWidth()-based height (it kept getting
        # a single line's worth, ~19px, no matter how much slack space the
        # panel actually had), clipping the second line.
        note.setMinimumHeight(40)
        self.body.addWidget(note)
        sp.register_session_reset(self._reset_spotify_session)

    def _reset_spotify_session(self) -> None:
        """Discard old-account results and callbacks when Spotify disconnects."""
        self._spotify_loader.cancel()
        self._winrt_loader.cancel()
        self._spotify_pending = False
        self._winrt_pending = False
        self._spotify_track = None
        self._winrt_info = None
        self._last_result = None
        self._current_art_id = None
        self._current_track = None
        self._render("Nothing playing", None)

    def _reset_art_placeholder(self) -> None:
        self._art_label.setPixmap(QPixmap())
        self._art_label.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f" stop:0 #3a4b8f, stop:1 #20264d);"
            f" border-radius: {album_art.CORNER_RADIUS}px;"
        )

    def _on_art_loaded(self, pixmap, track_id) -> None:
        if pixmap is None or track_id != self._current_art_id:
            return
        self._art_label.setStyleSheet("")
        self._art_label.setPixmap(pixmap)

    @staticmethod
    def _look_up_spotify():
        """Return (track, source_name) for Spotify's current playback, or
        None, without touching widgets.

        resolve_context_name costs a real API call for a playlist and never
        raises - a lookup failure just means no name - so the source is
        best-effort and its absence is not an error. It is resolved here, on
        the worker thread, because it is a network round trip like the
        playback read it accompanies."""
        if not sp.is_logged_in():
            return None
        playback = sp.get_current_playback()
        if not playback or not playback.get("item"):
            return None
        return playback["item"], sp.resolve_context_name(playback)

    @staticmethod
    def _look_up_winrt():
        """Return the Windows media-session fallback without touching widgets."""
        return now_playing.get()

    def build_nav(self):
        # One RowList, filled in when the lookup lands. The open-in-Spotify
        # row is the only thing that can ever be in it, and whether it belongs
        # there depends on the answer, so it starts empty either way.
        self._nav = RowList(
            [],
            on_activate=lambda i, r: self._open_in_spotify(),
            orientation="horizontal",
        )
        # Last time's answer stays on screen while the new one is fetched;
        # only a genuinely first open shows the placeholder.
        if self._last_result is None:
            self._render("Loading…", None)
        else:
            self._render(*self._last_result)
        self._spotify_pending = True
        self._winrt_pending = True
        self._spotify_track = None
        self._winrt_info = None
        self._spotify_loader.start(
            self._look_up_spotify, self._on_spotify_looked_up
        )
        self._winrt_loader.start(self._look_up_winrt, self._on_winrt_looked_up)
        return self._nav

    def _on_spotify_looked_up(self, value, error) -> None:
        self._spotify_pending = False
        if error is not None:
            log.exception(
                "Spotify playback lookup failed; using the Windows fallback",
                exc_info=error,
            )
            value = None
        track, source = value if value is not None else (None, None)
        self._spotify_track = track
        if track is not None:
            self._apply_lookup_result(track, None, source)
        elif not self._winrt_pending:
            self._apply_lookup_result(None, self._winrt_info)

    def _on_winrt_looked_up(self, value, error) -> None:
        self._winrt_pending = False
        if error is not None:
            log.exception("Windows media session lookup failed", exc_info=error)
            value = None
        self._winrt_info = value
        if self._spotify_track is None and (
            value is not None or not self._spotify_pending
        ):
            self._apply_lookup_result(None, value)

    def _apply_lookup_result(self, track, info, source=None) -> None:
        if track is not None:
            text = track.get("name") or "(unknown title)"
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            if artists:
                text += f"\n{artists}"
        elif info and (info["title"] or info["artist"]):
            text = info["title"] or "(unknown title)"
            if info["artist"]:
                text += f"\n{info['artist']}"
        else:
            text = "Nothing playing"
        # Whether there is Spotify content on screen at all, from either
        # source. Not the same question as `track is not None`: the media
        # session path has no track object but does carry Spotify metadata,
        # and every attribution rule below turns on the content, not on which
        # lookup happened to answer first.
        showing_content = track is not None or bool(
            info and (info["title"] or info["artist"])
        )
        self._last_result = (text, track, showing_content, source)
        self._render(text, track, showing_content, source)

    def _render(self, text: str, track, showing_content: bool = False,
                source: str | None = None) -> None:
        # Heading, mark and link row all key off showing_content rather than
        # `track is not None`. Before actions/now_playing.py filtered by
        # owning application, the fallback could be another player's track and
        # crediting Spotify for it was the thing to avoid. Now the fallback is
        # Spotify's own session, so withholding attribution would be the
        # error: their guidelines require Spotify metadata to carry the mark
        # and to link back, and metadata read from the media session is still
        # their metadata.
        self._current_track = track
        self.heading.setText(
            "Now playing on Spotify" if showing_content else "Now playing"
        )
        self._spotify_logo.setVisible(showing_content)

        self._song_label.setText(text)
        # Hidden rather than blank when there is nothing to say: Liked Songs,
        # a single queued track and Spotify radio are all indistinguishable
        # from the API and legitimately have no source, and an empty row
        # would just be dead space. The media-session path has none either.
        self._source_label.setText(f"From {source}" if source else "")
        self._source_label.setVisible(bool(source))
        track_id = track.get("id") if track is not None else None
        self._current_art_id = track_id
        self._reset_art_placeholder()
        art_url = album_art.smallest_image_url(track) if track is not None else None
        if art_url:
            album_art.get(
                art_url, _ART_SIZE, album_art.CORNER_RADIUS,
                lambda pixmap, tid=track_id: self._on_art_loaded(pixmap, tid),
            )

        # Spotify's guidelines require displayed metadata to link back to the
        # service — so the row appears exactly when Spotify metadata is on
        # screen, which now includes the media-session path. That path has no
        # track object to link to, so _open_in_spotify falls back to opening
        # Spotify itself; a link to the service satisfies the requirement even
        # when the exact track cannot be addressed.
        if not showing_content:
            self._open_row.hide()
            if self._nav is not None:
                self._nav.replace_rows([])
        else:
            self._open_row.show()
            if self._nav is not None:
                self._nav.replace_rows([self._open_row])
        # The panel's height changes with the song label's wrapping, and the
        # overlay sizes it from outside on the press that opened it.
        self.request_relayout()

    def _open_in_spotify(self) -> None:
        # A track when we have one (lands on that exact song), otherwise
        # Spotify itself. open_in_spotify already tries the `spotify:` URI
        # before the https one, so the desktop app opens where it is
        # installed and the website answers where it is not.
        open_in_spotify(self, self._current_track or _SPOTIFY_SERVICE)
