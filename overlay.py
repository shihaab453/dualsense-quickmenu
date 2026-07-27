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
import time
from datetime import datetime

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from icons import render_battery_pill, render_icon
from nav import NavStack, RowList
from panels.base import Tile
from panels.music import MusicPanel
from panels.nowplaying import NowPlayingPanel
from panels.power import PowerPanel
from panels.sound import SoundPanel
from panels.switcher import SwitcherPanel

# (key, glyph, hover label) — left to right, matching the mockup.
_TRAY_ICONS = [
    ("home", "Close Control Centre"),
    ("chats", "Chats & Calls"),
    ("music", "Music"),
    ("sound", "Sound"),
    ("switcher", "Task Switcher"),
    ("power", "Power"),
]

_PANEL_CLASSES = {
    "music": MusicPanel,
    "sound": SoundPanel,
    "switcher": SwitcherPanel,
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


class _NowPlayingCard(QFrame):
    """The only functional home card in Phase A — the other three are
    decorative placeholders in the mockup too (see design README)."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(260, 300)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        art = QFrame()
        art.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 #3a4b8f, stop:1 #20264d); border-radius: 10px;"
        )
        lay.addWidget(art, stretch=1)
        caption = QLabel("Now playing on Music")
        caption.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.55);")
        lay.addWidget(caption)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        border = "2px solid #3ddc97" if selected else "1px solid rgba(255,255,255,0.08)"
        self.setStyleSheet(
            f"#card {{ background: rgba(28,29,33,220); border-radius: 16px;"
            f" border: {border}; }}"
        )


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

        self._now_playing_card = _NowPlayingCard()
        self._cards_widget = QWidget(self)
        cards_lay = QHBoxLayout(self._cards_widget)
        cards_lay.setSpacing(22)
        cards_lay.setContentsMargins(0, 0, 0, 0)
        cards_lay.addWidget(self._now_playing_card)
        for caption in (
            "Recently created\nNew Clip Saved",
            "Discover\nQuick Setup Tips",
            "Recently created\nNew Screenshot",
        ):
            cards_lay.addWidget(_decorative_card(caption))

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
        elif key == "chats":
            pass  # decorative in the mockup too — no panel wired
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
                orientation="horizontal",
                name="cards",
            )
        )

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

        self._tray_label.adjustSize()
        self._tray_label.move((w - self._tray_label.width()) // 2, tray_y - _LABEL_GAP)

        content_bottom = tray_y - _LABEL_GAP - _CONTENT_GAP
        if self._mode == "panel" and self._active_panel:
            panel = self._active_panel
            panel.adjustSize()
            if getattr(panel, "anchor", "center") == "left":
                # Clamped so a narrower window never pushes the panel
                # partly off-screen — falls back toward center-ish instead.
                x = min(_LEFT_ANCHOR_MARGIN, max(0, w - panel.width()))
            else:
                x = (w - panel.width()) // 2
            panel.move(x, content_bottom - panel.height())
        else:
            self._cards_widget.adjustSize()
            self._cards_widget.move(
                (w - self._cards_widget.width()) // 2,
                content_bottom - self._cards_widget.height(),
            )
