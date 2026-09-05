# Phase A shows the real current track as a read-only view — the full
# mockup layout (progress bar, like/shuffle/repeat, volume sub-view, 3-dot
# submenu) is a bigger undertaking than this card needs, since the same
# controls already exist one tap away in the Music panel's own Detail view
# (reached via the Music tray icon). Prefers Spotify's own playback state
# when logged in (accurate to what "Music" is actually playing), falling
# back to the system-wide winrt reading (works for any app, not just
# Spotify) when not logged in or nothing's there.

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

import logs
from actions import album_art
from actions import now_playing
from actions import spotify_client as sp
from nav import RowList
from panels.base import ActionRow, Panel, open_in_spotify
from workers import Loader

log = logs.get(__name__)

_ART_SIZE = 64
# Panel width (460) minus its own left+right margins (36 each, from
# panels/base.py's Panel), minus the art label's width and the row's
# spacing between it and the text.
_SONG_LABEL_WIDTH = 460 - 36 - 36 - _ART_SIZE - 14


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

        self._current_art_id = None
        self._current_track = None
        # What the last look found, so reopening the panel shows it at once
        # instead of blanking while the same two lookups run again.
        self._last_result = None
        # Both lookups are slow enough to matter and neither belongs on the
        # Qt thread: Spotify is a network round trip, and the Windows media
        # session is a WinRT call that has to spin up its own event loop.
        self._loader = Loader(sp.submit, "nowplaying")
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
    def _look_up_whats_playing():
        """Spotify's own state if there is any, else the Windows-wide media
        session. Runs on a worker thread, so it must not touch a widget.

        Returns (spotify_track_or_None, fallback_info_or_None).

        Both halves still run on the Spotify worker, one after the other, so
        the Windows fallback waits behind the Spotify attempt it exists to
        cover for. That is bounded now rather than open-ended (the auth and
        API calls both have timeouts), but it is still the wrong shape: the
        two should run on separate workers and the Spotify answer should win
        when it arrives. Left as an open item rather than half-done here."""
        track = None
        try:
            if sp.is_logged_in():
                playback = sp.get_current_playback()
                if playback and playback.get("item"):
                    track = playback["item"]
        except Exception:
            # Falls back to the Windows-wide media session, so this is a
            # degraded-but-working path rather than a failure the user sees.
            log.exception("Spotify playback lookup failed; falling back to Windows")
        if track is not None:
            return track, None
        try:
            return None, now_playing.get()
        except Exception:
            log.exception("Windows media session lookup failed")
            return None, None

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
        self._loader.start(self._look_up_whats_playing, self._on_looked_up)
        return self._nav

    def _on_looked_up(self, value, error) -> None:
        if error is not None:
            log.exception("Couldn't work out what's playing", exc_info=error)
            self._render("Nothing playing", None)
            return
        track, info = value
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
        self._last_result = (text, track)
        self._render(text, track)

    def _render(self, text: str, track) -> None:
        # The heading names Spotify only when the track actually came from
        # Spotify. This panel falls back to the Windows media session, which
        # reports whatever is playing anywhere — a browser, VLC, a competing
        # music app — and labelling that "on Spotify" would attribute someone
        # else's content to them, which their guidelines specifically prohibit.
        self._current_track = track
        self.heading.setText(
            "Now playing on Spotify" if track is not None else "Now playing"
        )

        self._song_label.setText(text)
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
        # screen, and not when the fallback is showing another player's track.
        if track is None:
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
        open_in_spotify(self, self._current_track or {})
