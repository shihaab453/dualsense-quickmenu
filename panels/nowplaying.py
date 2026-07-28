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
from panels.base import Panel

log = logs.get(__name__)

_ART_SIZE = 64
# Panel width (460) minus its own left+right margins (36 each, from
# panels/base.py's Panel), minus the art label's width and the row's
# spacing between it and the text.
_SONG_LABEL_WIDTH = 460 - 36 - 36 - _ART_SIZE - 14


class NowPlayingPanel(Panel):
    def __init__(self):
        super().__init__("Now playing on Music", width=460)
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
        self._reset_art_placeholder()

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

    def _spotify_track(self):
        try:
            if not sp.is_logged_in():
                return None
            playback = sp.get_current_playback()
        except Exception:
            # Falls back to the Windows-wide media session, so this is a
            # degraded-but-working path rather than a failure the user sees.
            log.exception("Spotify playback lookup failed; falling back to Windows")
            return None
        if not playback or not playback.get("item"):
            return None
        return playback["item"]

    def build_nav(self):
        track = self._spotify_track()
        if track is not None:
            text = track.get("name") or "(unknown title)"
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            if artists:
                text += f"\n{artists}"
            art_url = album_art.smallest_image_url(track)
            track_id = track.get("id")
        else:
            info = now_playing.get()
            if info and (info["title"] or info["artist"]):
                text = info["title"] or "(unknown title)"
                if info["artist"]:
                    text += f"\n{info['artist']}"
            else:
                text = "Nothing playing"
            art_url = None
            track_id = None

        self._song_label.setText(text)
        self._current_art_id = track_id
        self._reset_art_placeholder()
        if art_url:
            album_art.get(
                art_url, _ART_SIZE, album_art.CORNER_RADIUS,
                lambda pixmap, tid=track_id: self._on_art_loaded(pixmap, tid),
            )
        return RowList([])
