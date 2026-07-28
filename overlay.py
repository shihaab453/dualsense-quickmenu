# The overlay shell: top status row, 6-icon tray, home cards, and panel
# routing. Every navigable list (tray, home cards, each panel's own rows)
# is a `nav.RowList`; `handle_button` is a thin dispatcher onto whichever
# list is currently on top of the `nav.NavStack` — see nav.py for why.
#
# Controls while open:
#   D-pad left/right   -> move within a horizontal list (tray, media tiles)
#   D-pad up/down       -> move within a vertical list (panel rows), or
#                          move focus between tray and home cards
#   D-pad left/right    -> (inside a vertical list) adjust the selected
#                          row's value, e.g. the volume slider (2% per step,
#                          1% while Cross is held for finer control)
#   Cross               -> activate the current selection
#   Circle              -> back up one level (submenu -> panel -> tray -> closed)
#   PS                  -> always closes everything, from any depth
# Keyboard fallback for testing without a controller: arrows / Enter / Esc.

import ctypes
import random
import time
from datetime import datetime

from PySide6.QtCore import QObject, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import logs
from actions import album_art
from actions import spotify_client as sp
from icons import render_battery_pill, render_icon
from nav import NavStack, RowList
from panels.base import Tile
from panels.chats import ChatsPanel
from panels.music import MusicPanel
from panels.nowplaying import NowPlayingPanel
from panels.power import PowerPanel
from panels.sound import SoundPanel

log = logs.get(__name__)

# (key, hover label) — left to right. The mockup has a sixth icon here, a Task
# Switcher (pinned/recently-played games); it was built and then removed once it
# turned out not to serve what this app is for. Panel indices are derived from
# this list, so removing an entry is all that's needed.
_TRAY_ICONS = [
    ("home", "Close Control Centre"),
    ("chats", "Chats & Calls"),
    ("music", "Music"),
    ("sound", "Sound"),
    ("power", "Power"),
]

_PANEL_CLASSES = {
    "chats": ChatsPanel,
    "music": MusicPanel,
    "sound": SoundPanel,
    "power": PowerPanel,
    "nowplaying": NowPlayingPanel,
}

_MARGIN_RIGHT = 64
_TRAY_BOTTOM_MARGIN = 110
_LABEL_GAP = 40
_CONTENT_GAP = 20
_ADJUST_STEP = 2
_ADJUST_STEP_FINE = 1  # used while Cross is held, for finer control
_LEFT_ANCHOR_MARGIN = 210  # matches the mockup's Music panel left offset


def _force_foreground(hwnd: int) -> None:
    # Windows normally refuses to let a background app grab focus (anti focus-
    # stealing). Tapping the ALT key first is the long-standing workaround that
    # lifts the restriction. We *want* focus here: while the menu has it, the
    # game is unfocused and ignores the D-pad presses we use for navigation.
    #
    # This isn't 100% reliable — Windows applies a timing-based heuristic on
    # top of the ALT-tap trick, so it can silently lose the race under the
    # wrong conditions. GetForegroundWindow lets us check whether it actually
    # worked and retry a few times rather than hoping the first attempt lands.
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32
    # HWNDs are 64-bit pointers; without this, ctypes' default 32-bit return
    # type can silently mishandle the comparison below on 64-bit Windows.
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

    for attempt in range(4):
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.03 * (attempt + 1))


_KEYMAP = {
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Return: "cross",
    Qt.Key_Enter: "cross",
    Qt.Key_Escape: "circle",
}


class _PlayingIndicator(QWidget):
    """The small "currently playing" glyph in the card's top-right corner: 4
    bars that jump to random heights on a timer while something is playing,
    swapping to a static pause glyph otherwise. Purely decorative — not
    reactive to real audio, matching the real PS5 UI this was modeled on,
    which does the same thing.

    Owns its own QTimer, started/stopped via set_playing() rather than run
    continuously, so nothing animates (or costs CPU) while nothing's playing
    or the card isn't visible — see OverlayWindow.close_menu()."""

    _BAR_COUNT = 4
    _TICK_MS = 140

    def __init__(self, size: int = 20):
        super().__init__()
        self.setFixedSize(size, size)
        self._playing = False
        self._heights = [0.45, 0.75, 0.55, 0.35]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_playing(self, playing: bool) -> None:
        if playing == self._playing:
            return
        self._playing = playing
        if playing:
            self._timer.start(self._TICK_MS)
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._heights = [random.uniform(0.25, 1.0) for _ in range(self._BAR_COUNT)]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._playing:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("white"))
            w, h = self.width(), self.height()
            gap = w / (self._BAR_COUNT * 2 - 1)
            for i, frac in enumerate(self._heights):
                bar_h = max(2.0, h * frac)
                painter.drawRoundedRect(
                    QRectF(i * gap * 2, h - bar_h, gap, bar_h), 1, 1
                )
        else:
            painter.drawPixmap(0, 0, render_icon("pause", "white", self.width()))
        painter.end()


class _RefreshSignal(QObject):
    # get_now_playing_summary_async's callback fires on a background thread;
    # Qt widgets can only be touched from the main thread, so this hops back
    # over — same bridge shape as MusicPanel's login flow.
    ready = Signal(object)  # dict | None


class _NowPlayingCard(QFrame):
    """The home screen's Now Playing card — the only functional one of the
    four; the other three are decorative placeholders in the mockup too.

    Deliberately shows Spotify data only, nothing else. Unlike the Now
    Playing *panel* (panels/nowplaying.py), which falls back to Windows'
    system-wide media session so it works for any player, this card is
    explicitly Spotify-branded (the reserved logo slot below) — silently
    substituting some other app's track under Spotify's branding would be
    exactly the "attributing someone else's content to Spotify" problem
    already fixed once for the panel's own heading. If nothing's playing on
    Spotify, the card says so rather than reaching for the Windows fallback.

    Height is never hardcoded — every child is a fixed size, so the card's
    total height falls out of Qt's own layout math, and toggling
    _detail_widget's visibility changes it for free. See set_selected() for
    why that still needs an explicit nudge (gotcha #4 territory: a widget
    that changes size after construction can under-measure itself even after
    invalidating its layout)."""

    _WIDTH = 260
    _ART_SIZE = _WIDTH - 18 * 2  # fills the card's content width, square

    def __init__(self):
        super().__init__()
        self.setFixedWidth(self._WIDTH)
        self.setObjectName("card")
        self._pending_art_url = None
        self._refreshing = False

        self._signal = _RefreshSignal()
        self._signal.ready.connect(self._on_summary_ready)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(8)

        # Real QWidget boundary for this row (gotcha #3): a bare addLayout()
        # here has, historically in this app, cached a stale sizeHint once
        # anything around it changes size.
        top_row_widget = QWidget()
        top_row = QHBoxLayout(top_row_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        # Reserved and left empty on purpose — the real Spotify logo asset
        # goes here once supplied. Never approximate or redraw their mark;
        # their design guidelines are explicit that it must not be modified.
        self._logo_slot = QLabel()
        self._logo_slot.setFixedSize(24, 24)
        top_row.addWidget(self._logo_slot)
        top_row.addStretch(1)
        self._indicator = _PlayingIndicator(20)
        top_row.addWidget(self._indicator, 0, Qt.AlignTop)
        outer.addWidget(top_row_widget)

        self._art_label = QLabel()
        self._art_label.setFixedSize(self._ART_SIZE, self._ART_SIZE)
        outer.addWidget(self._art_label)

        self._caption_label = QLabel()
        self._caption_label.setStyleSheet(
            "font-size: 13px; color: rgba(255,255,255,0.5);"
        )
        outer.addWidget(self._caption_label)

        self._title_label = QLabel()
        self._title_label.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: white;"
        )
        outer.addWidget(self._title_label)

        # Shown only while the card is D-pad-selected — see set_selected().
        # Real QWidget boundary, same reason as top_row_widget above.
        self._detail_widget = QWidget()
        detail_lay = QVBoxLayout(self._detail_widget)
        detail_lay.setContentsMargins(0, 2, 0, 0)
        detail_lay.setSpacing(2)
        self._artist_label = QLabel()
        self._artist_label.setStyleSheet(
            "font-size: 13px; color: rgba(255,255,255,0.65);"
        )
        detail_lay.addWidget(self._artist_label)
        self._source_label = QLabel()
        self._source_label.setStyleSheet(
            "font-size: 12px; color: rgba(255,255,255,0.4);"
        )
        detail_lay.addWidget(self._source_label)
        outer.addWidget(self._detail_widget)
        self._detail_widget.hide()

        self._show_empty_state()
        self.set_selected(False)

    # ---- text / art helpers ----

    def _elide(self, label: QLabel, text: str) -> None:
        """Single-line truncation with a trailing "…", matching the real PS5
        card's card this was modeled on — not word-wrap, which would make the
        card's height depend on how long a song/artist/playlist name happens
        to be."""
        available = self._WIDTH - 18 * 2
        label.setText(QFontMetrics(label.font()).elidedText(text, Qt.ElideRight, available))

    def _reset_art_placeholder(self) -> None:
        self._art_label.setPixmap(QPixmap())
        self._art_label.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f" stop:0 #3a4b8f, stop:1 #20264d);"
            f" border-radius: {album_art.CORNER_RADIUS}px;"
        )

    def _on_art_loaded(self, pixmap, url) -> None:
        # url must match what's still wanted — refresh() can complete after a
        # newer one has already started (e.g. the track changed mid-download).
        if pixmap is None or url != self._pending_art_url:
            return
        self._art_label.setStyleSheet("")
        self._art_label.setPixmap(pixmap)

    def pause_for_close(self) -> None:
        """Stops the playing-indicator's animation timer. Called when the
        whole overlay closes — there's no point animating a widget nobody can
        see, and left running the timer would otherwise tick indefinitely in
        the background for as long as the app is open. The next refresh()
        (open_menu() always triggers one) resyncs it if music is still
        genuinely playing."""
        self._indicator.set_playing(False)

    def _show_empty_state(self) -> None:
        self._pending_art_url = None
        self._indicator.set_playing(False)
        self._reset_art_placeholder()
        self._caption_label.setText("Spotify")
        self._elide(self._title_label, "Nothing playing")
        self._elide(self._artist_label, "")
        self._elide(self._source_label, "")

    # ---- data refresh ----

    def refresh(self) -> None:
        """Kicks off a background fetch of what's currently playing; the card
        updates itself once it arrives. Never blocks — see
        spotify_client.get_now_playing_summary_async for why that matters
        here specifically (this runs on every menu-open, not just when a
        panel is deliberately opened)."""
        if self._refreshing:
            return
        if not sp.is_logged_in():
            self._show_empty_state()
            return
        self._refreshing = True
        sp.get_now_playing_summary_async(self._signal.ready.emit)

    def _on_summary_ready(self, summary) -> None:
        self._refreshing = False
        if summary is None:
            self._show_empty_state()
            return

        self._indicator.set_playing(summary["is_playing"])
        self._caption_label.setText("Now playing on Spotify")
        self._elide(self._title_label, summary["title"])
        self._elide(self._artist_label, summary["artists"])
        source = summary.get("source_name")
        self._elide(self._source_label, f"From {source}" if source else "")

        art_url = summary.get("art_url")
        self._pending_art_url = art_url
        if art_url:
            album_art.get(
                art_url, self._ART_SIZE, album_art.CORNER_RADIUS,
                lambda pixmap, u=art_url: self._on_art_loaded(pixmap, u),
            )
        else:
            self._reset_art_placeholder()

    # ---- selection ----

    def set_selected(self, selected: bool) -> None:
        border = "2px solid #3ddc97" if selected else "1px solid rgba(255,255,255,0.08)"
        self.setStyleSheet(
            f"#card {{ background: rgba(28,29,33,220); border-radius: 16px;"
            f" border: {border}; }}"
        )
        self._detail_widget.setVisible(selected)
        # The card's height depends on _detail_widget's visibility (nothing
        # here is hardcoded — see the class docstring), and this app's own
        # history (gotchas #2-#4) is that a single synchronous relayout right
        # after a size-affecting change doesn't reliably measure correctly.
        # updateGeometry() nudges Qt to recompute now; the caller (OverlayWindow)
        # still does its own explicit _relayout() afterward, matching how every
        # other size-changing action in this app is handled.
        self.updateGeometry()
        if selected:
            self.refresh()


def _decorative_card(caption: str) -> QFrame:
    card = QFrame()
    card.setFixedSize(260, 300)
    card.setStyleSheet(
        "background: rgba(35,36,39,200); border-radius: 16px;"
        " border: 1px solid rgba(255,255,255,0.06);"
    )
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.addStretch(1)
    label = QLabel(caption)
    label.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.4);")
    lay.addWidget(label)
    return card


class OverlayWindow(QWidget):
    def __init__(self, get_battery=lambda: None, is_held=lambda name: False):
        super().__init__()
        self._get_battery = get_battery
        self._is_held = is_held

        # Frameless + always-on-top; Tool means no taskbar entry.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.nav = NavStack()
        self._mode = "home"       # "home" | "panel"
        self._home_focus = "tray"  # "tray" | "cards"  (only meaningful in "home")
        self._panels = {}          # key -> Panel instance, created lazily
        self._active_panel = None

        self._build_ui()

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(140)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_status)
        self._clock_timer.start(1000)

    # ---- UI construction ----

    def _build_ui(self) -> None:
        # .geometry() (the full physical screen), not .availableGeometry()
        # (which excludes the Windows taskbar strip) — a game overlay should
        # cover the whole screen, otherwise the scrim can't dim that strip
        # and it shows through undimmed while the menu is open.
        screen = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self._clock_label = QLabel(self)
        self._clock_label.setStyleSheet(
            "color: white; font-size: 26px; font-weight: 700; font-family: 'Manrope', 'Segoe UI', sans-serif;"
        )

        # Controller glyph + percent + battery pill, matching the mockup's
        # status row exactly — real vector icons (icons.py), not emoji.
        self._battery_widget = QWidget(self)
        battery_lay = QHBoxLayout(self._battery_widget)
        battery_lay.setContentsMargins(0, 0, 0, 0)
        battery_lay.setSpacing(10)
        self._controller_icon = QLabel()
        self._battery_percent_label = QLabel()
        self._battery_percent_label.setStyleSheet(
            "color: rgba(255,255,255,0.6); font-size: 18px; font-weight: 600;"
            " font-family: 'Manrope', 'Segoe UI', sans-serif;"
        )
        self._battery_pill_icon = QLabel()
        battery_lay.addWidget(self._controller_icon)
        battery_lay.addWidget(self._battery_percent_label)
        battery_lay.addWidget(self._battery_pill_icon)

        self._scrim = QWidget(self)
        self._scrim.setStyleSheet("background: rgba(0,0,0,0.35);")
        self._scrim.hide()

        self._tray_widget = QWidget(self)
        tray_lay = QHBoxLayout(self._tray_widget)
        tray_lay.setSpacing(60)
        tray_lay.setContentsMargins(0, 0, 0, 0)
        self._tray_tiles = []
        for key, _label in _TRAY_ICONS:
            tile = Tile(key)  # icon name matches the tray key exactly
            tray_lay.addWidget(tile)
            self._tray_tiles.append(tile)

        self._tray_label = QLabel(self)
        self._tray_label.setStyleSheet(
            "color: rgba(255,255,255,0.75); font-size: 19px; font-family: 'Manrope', 'Segoe UI', sans-serif;"
        )

        # Same role as _tray_label but for the cards row — currently only ever
        # shows text for the Now Playing card, since it's the only card that's
        # actually part of the cards RowList (the 3 decorative ones aren't
        # navigable at all, so there's nothing to hint at for them).
        self._cards_hint_label = QLabel(self)
        self._cards_hint_label.setStyleSheet(
            "color: rgba(255,255,255,0.5); font-size: 14px; font-family: 'Manrope', 'Segoe UI', sans-serif;"
        )

        self._now_playing_card = _NowPlayingCard()
        self._cards_widget = QWidget(self)
        cards_lay = QHBoxLayout(self._cards_widget)
        cards_lay.setSpacing(22)
        cards_lay.setContentsMargins(0, 0, 0, 0)
        # Explicit bottom alignment on every card: they're all the same fixed
        # height today, so this changes nothing yet, but the Now Playing card's
        # height now varies with selection (see _NowPlayingCard's docstring) —
        # without this, QHBoxLayout's default alignment for children shorter
        # than the tallest one is to hang them from the top, which would visibly
        # detach the 3 decorative cards' bottoms from the row's shared baseline
        # the moment the real card grows taller than them.
        cards_lay.addWidget(self._now_playing_card, 0, Qt.AlignBottom)
        for caption in (
            "Recently created\nNew Clip Saved",
            "Discover\nQuick Setup Tips",
            "Recently created\nNew Screenshot",
        ):
            cards_lay.addWidget(_decorative_card(caption), 0, Qt.AlignBottom)

    # ---- input handling ----

    def handle_button(self, name: str) -> None:
        if not self.isVisible():
            if name == "ps":
                self.open_menu()
            return
        if name == "ps":
            self.close_menu()
            return

        ctx = self.nav.current()
        if name == "circle":
            self._go_back()
        elif name in ("up", "down"):
            if self._mode == "home":
                if name == "up" and self._home_focus == "tray":
                    self._focus_cards()
                    return
                if name == "down" and self._home_focus == "cards":
                    self._go_back()
                    return
            if ctx and ctx.orientation == "vertical":
                ctx.move(-1 if name == "up" else 1)
        elif name in ("left", "right"):
            if ctx and ctx.orientation == "horizontal":
                ctx.move(-1 if name == "left" else 1)
            elif ctx:
                # Holding Cross while adjusting (e.g. a volume slider) drops
                # to a finer 1% step instead of the normal 2%.
                step = _ADJUST_STEP_FINE if self._is_held("cross") else _ADJUST_STEP
                ctx.adjust(-step if name == "left" else step)
        elif name == "cross":
            if ctx:
                ctx.activate()

    def keyPressEvent(self, event) -> None:
        name = _KEYMAP.get(event.key())
        if name:
            self.handle_button(name)
        else:
            super().keyPressEvent(event)

    # ---- navigation ----

    def _activate_tray(self, index, tile) -> None:
        key = _TRAY_ICONS[index][0]
        if key == "home":
            self.close_menu()
        else:
            self._open_panel(key)

    def _update_tray_label(self, index, tile) -> None:
        self._tray_label.setText(_TRAY_ICONS[index][1])
        self._relayout()

    def _focus_cards(self) -> None:
        self._home_focus = "cards"
        self._now_playing_card.set_selected(True)
        self.nav.push(
            RowList(
                [self._now_playing_card],
                on_activate=lambda i, r: self._open_panel("nowplaying"),
                on_select=self._update_cards_hint,
                orientation="horizontal",
                name="cards",
            )
        )
        self._relayout()

    def _update_cards_hint(self, index, card) -> None:
        # Text only, matching the reference — Square isn't mapped to anything
        # in controller.py yet, so this doesn't do anything if pressed. Cross
        # still opens the full Now Playing panel, unchanged.
        self._cards_hint_label.setText("Press □ for Pause")
        self._relayout()

    def _go_back(self) -> None:
        still_open = self.nav.pop()
        if not still_open:
            self.close_menu()
            return
        # Derive where we landed from what's now on top of the stack, rather
        # than assuming — a panel opened from "cards" must go back to
        # "cards", not unconditionally reset to "tray".
        landed = self.nav.current()
        name = getattr(landed, "name", None)
        if name in ("tray", "cards"):
            self._return_to_home(name)
        elif landed is not None and landed.on_enter:
            # Landed back on one of the panel's own internal views (e.g.
            # Music's Songs list after leaving a song's Detail view) — let
            # the panel restore its own visible widgets for that view.
            landed.on_enter()

    def _open_panel(self, key: str) -> None:
        cls = _PANEL_CLASSES.get(key)
        if cls is None:
            return
        panel = self._panels.get(key)
        if panel is None:
            panel = cls()
            panel.setParent(self)
            # Lets a panel with multiple internal views (Library/Songs/
            # Detail) push further levels itself and ask for a re-layout
            # when its content size changes between views.
            panel.nav = self.nav
            panel.request_relayout = self._relayout
            self._panels[key] = panel

        self._active_panel = panel
        self._mode = "panel"
        self._cards_widget.hide()
        self._scrim.show()
        panel.show()
        panel.raise_()
        self._relayout()

        row_list = panel.build_nav() or RowList([])
        self.nav.push(row_list)

        # build_nav() may populate scrollable content whose real size only
        # becomes visible to Qt's layout system on the next event-loop tick
        # (see panels/base.py's fit_scroll_to_content for the full story) —
        # without this deferred second pass, a panel with a scrollable list
        # renders squished the first time it's opened.
        def _settle():
            # Invalidating just the outer layout isn't reliable — Qt's
            # sizeHint caching doesn't always cascade into a *nested*
            # layout like panel.body (added via addLayout() in
            # panels/base.py), so it must be invalidated directly too (this
            # exact gap caused a real squished-panel bug during Phase D).
            panel.body.invalidate()
            panel.layout().invalidate()
            self._relayout()
            # A panel that grew a lot between opens (e.g. NowPlayingPanel's
            # song label going from one placeholder line to several wrapped
            # ones) can leave stale pixels from its previous, smaller paint
            # visible in the newly-exposed area — an incremental/partial
            # repaint doesn't always cover it. Force a full repaint of the
            # panel's whole new rect once it's actually settled at its
            # final size.
            panel.update()

        QTimer.singleShot(0, _settle)

    def _return_to_home(self, focus: str) -> None:
        if self._active_panel:
            self._active_panel.hide()
        self._active_panel = None
        self._mode = "home"
        self._home_focus = focus
        self._scrim.hide()
        self._cards_widget.show()
        self._now_playing_card.set_selected(focus == "cards")
        self._relayout()

    # ---- open / close ----

    def open_menu(self) -> None:
        self.nav.clear()
        self._mode = "home"
        self._home_focus = "tray"
        self._active_panel = None
        for panel in self._panels.values():
            panel.hide()
        self._scrim.hide()
        self._cards_widget.show()
        self._now_playing_card.set_selected(False)
        # Non-blocking (see get_now_playing_summary_async) — safe to fire on
        # every open, including ones where the user never navigates up to the
        # card at all.
        self._now_playing_card.refresh()

        self._update_status()
        tray_list = RowList(
            self._tray_tiles,
            on_activate=self._activate_tray,
            on_select=self._update_tray_label,
            orientation="horizontal",
            name="tray",
        )
        self.nav.push(tray_list)

        self.setWindowOpacity(0.0)
        self.show()
        self._relayout()
        self.raise_()
        self.activateWindow()
        _force_foreground(int(self.winId()))
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def close_menu(self) -> None:
        # Hide instantly (no fade-out) so focus snaps back to the game.
        self._fade.stop()
        self.hide()
        self.nav.clear()
        for panel in self._panels.values():
            panel.hide()
        self._active_panel = None
        self._now_playing_card.pause_for_close()

    def set_controller_connected(self, connected: bool) -> None:
        if self.isVisible():
            self._update_status()

    # ---- layout ----

    def _update_status(self) -> None:
        clock_text = datetime.now().strftime("%I:%M %p").lstrip("0")
        self._clock_label.setText(clock_text)
        percent = self._get_battery()
        color = "rgba(255,255,255,0.6)"
        self._controller_icon.setPixmap(render_icon("controller", color, 22))
        self._battery_percent_label.setText(f"{percent}%" if percent is not None else "—")
        self._battery_pill_icon.setPixmap(render_battery_pill(percent, color))
        self._battery_widget.adjustSize()
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        self._scrim.setGeometry(0, 0, w, h)

        self._clock_label.adjustSize()
        self._clock_label.move(w - _MARGIN_RIGHT - self._clock_label.width(), 44)
        self._battery_widget.adjustSize()
        self._battery_widget.move(w - _MARGIN_RIGHT - self._battery_widget.width(), 88)

        self._tray_widget.adjustSize()
        tray_y = h - _TRAY_BOTTOM_MARGIN
        self._tray_widget.move((w - self._tray_widget.width()) // 2, tray_y)

        # _tray_label and _cards_hint_label share this one slot rather than
        # each getting their own reserved space — they're never meaningful at
        # the same time (home_focus is either "tray" or "cards", never both),
        # and giving the cards hint its own permanent gap would mean growing
        # content_bottom, which panels anchor against too and would shift for
        # a hint line that only ever applies to the home screen.
        hint_y = tray_y - _LABEL_GAP
        self._tray_label.setVisible(self._mode == "home" and self._home_focus == "tray")
        self._tray_label.adjustSize()
        self._tray_label.move((w - self._tray_label.width()) // 2, hint_y)

        self._cards_hint_label.setVisible(self._mode == "home" and self._home_focus == "cards")
        self._cards_hint_label.adjustSize()
        self._cards_hint_label.move((w - self._cards_hint_label.width()) // 2, hint_y)

        content_bottom = tray_y - _LABEL_GAP - _CONTENT_GAP
        if self._mode == "panel" and self._active_panel:
            panel = self._active_panel
            panel.adjustSize()
            if getattr(panel, "anchor", "center") == "left":
                # On a screen wide enough, hold the mockup's 210px left offset.
                # On a narrower one, center instead — min(_LEFT_ANCHOR_MARGIN,
                # w - panel.width()) used to be the fallback here, but that
                # expression picks x = w - panel.width() once the margin no
                # longer fits, which flushes the panel's right edge exactly
                # against the screen's right edge (zero right margin) rather
                # than the "center-ish" result the comment described. Visible
                # on any screen narrower than panel.width() + 210 — e.g. a
                # 1707px-wide screen with Music's 1500px panel, where it
                # clamped to x=207 and sat flush against the right edge.
                max_x = w - panel.width()
                x = _LEFT_ANCHOR_MARGIN if max_x >= _LEFT_ANCHOR_MARGIN else max(0, max_x // 2)
            else:
                x = (w - panel.width()) // 2
            panel.move(x, content_bottom - panel.height())
        else:
            self._cards_widget.adjustSize()
            self._cards_widget.move(
                (w - self._cards_widget.width()) // 2,
                content_bottom - self._cards_widget.height(),
            )
