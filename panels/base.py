# Shared look for every floating panel: frosted-glass surface, title header,
# fade+slide-up open animation. Qt Widgets can't blur what's behind the
# window the way CSS backdrop-filter can, so we approximate with a
# near-opaque translucent surface instead — visually close since the real
# panels are ~90% opaque anyway.

from PySide6.QtCore import QPropertyAnimation, QRect, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import logs
from actions import spotify_client
from icons import render_icon

_log = logs.get(__name__)

# Default cap on how tall a scrollable row list can grow before scrolling,
# so a panel's size stays consistent regardless of how many rows it holds.
DEFAULT_LIST_MAX_HEIGHT = 500
_MIN_FITTED_SCROLL_HEIGHT = 72
_MAX_WIDGET_SIZE = 16777215


class Tile(QLabel):
    """A circular icon button, shared by the tray and any panel that wants
    a row of icon-style controls. Shows a real vector icon (icons.py) —
    not emoji — since Windows renders color emoji as fixed-color bitmaps
    that ignore recoloring, which broke the "active" tint on toggle tiles."""

    SIZE = 60
    ICON_SIZE = 26

    def __init__(self, icon_name: str):
        super().__init__()
        self._icon_name = icon_name
        self._selected = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignCenter)
        self._restyle()

    def set_icon_name(self, icon_name: str) -> None:
        self._icon_name = icon_name
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._restyle()

    def _foreground(self) -> str:
        return "#15151c" if self._selected else "white"

    def _restyle(self) -> None:
        bg = "white" if self._selected else "rgba(255,255,255,0.10)"
        self.setStyleSheet(f"background: {bg}; border-radius: {self.SIZE // 2}px;")
        self.setPixmap(render_icon(self._icon_name, self._foreground(), self.ICON_SIZE))


def selected_row_style(selected: bool, radius: int = 14) -> str:
    """The focus treatment every selectable row across every panel (Sound,
    Power, Music) uses for the current D-pad selection — one
    shared implementation instead of four near-identical copies. Matches
    the mockup's selected-row rule (border + a faint background tint); the
    mockup also adds a soft green box-shadow glow around the border, which
    Qt stylesheets have no equivalent for, so that part is left out."""
    if selected:
        return (
            f"#row {{ border: 2px solid #3ddc97; border-radius: {radius}px;"
            f" background: rgba(255,255,255,0.03); }}"
        )
    return f"#row {{ border: 1px solid transparent; border-radius: {radius}px; }}"


class Panel(QFrame):
    """Base for every tray panel. Subclasses build their content into
    `self.body` (a QVBoxLayout) and register their rows with a RowList,
    returned from `build_nav()`.
    """

    def __init__(self, title: str, width: int = 460):
        super().__init__()
        self.setObjectName("panel")
        self.preferred_width = width
        self.setFixedWidth(width)
        # Exact mockup values: panel surface rgba(24,25,29,0.86), border
        # rgba(255,255,255,0.08) — QSS accepts alpha as either 0-255 (plain
        # int) or 0-1 (a value with a decimal point); using the mockup's own
        # 0-1 style directly here for an exact match rather than converting.
        self.setStyleSheet(
            """
            #panel { background: rgba(24, 25, 29, 0.86); border-radius: 20px;
                     border: 1px solid rgba(255,255,255,0.08); }
            QLabel { color: white; font-family: 'Manrope', 'Segoe UI', sans-serif; }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 28)
        outer.setSpacing(18)

        # Default matches Sound/Power's title size (32px/700 in the
        # mockup); Music and Now Playing use smaller headers with a leading
        # icon and override self.heading's style after calling super().
        self.heading = None
        if title:
            self.heading = QLabel(title)
            self.heading.setStyleSheet("font-size: 32px; font-weight: 700;")
            outer.addWidget(self.heading)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

        self._anim = None
        # Filled in by OverlayWindow._open_panel after construction. Lets a
        # panel with multiple internal views (only Music needs this so far)
        # push further nav levels itself and ask for a re-layout when its
        # content size changes between views.
        self.nav = None
        self.request_relayout = lambda: None
        # "center" (default, matches every panel except Music in the
        # mockup) or "left" — checked by OverlayWindow._relayout() instead
        # of it needing to know about specific Panel subclasses.
        self.anchor = "center"

    def fit_to_viewport(self, max_width: int, max_height: int) -> None:
        """Fit this panel inside the overlay while preserving its ideal size.

        Qt screen geometry is expressed in logical pixels, so the same method
        handles ordinary resolutions and Windows display scaling. Scrollable
        row lists give up height first. If a very small display still cannot
        fit an inflexible view, the panel itself is capped as a last resort so
        its geometry never extends beyond the screen.
        """
        max_width = max(1, int(max_width))
        max_height = max(1, int(max_height))

        # Release a height cap from a previous, smaller monitor and restore
        # each row list to the content height recorded by
        # fit_scroll_to_content().
        self.setMinimumHeight(0)
        self.setMaximumHeight(_MAX_WIDGET_SIZE)
        for scroll in self.findChildren(QScrollArea):
            preferred = scroll.property("dsqmPreferredHeight")
            if preferred is not None:
                scroll.setFixedHeight(int(preferred))
        # Music keeps its current page in a fixed-height QStackedWidget so
        # hidden pages cannot inflate the panel. Recompute that preferred
        # height after restoring its scroll area from a smaller monitor.
        for stack in self.findChildren(QStackedWidget):
            current = stack.currentWidget()
            if current is not None:
                if current.layout() is not None:
                    current.layout().invalidate()
                stack.setFixedHeight(current.sizeHint().height())

        self.setFixedWidth(min(self.preferred_width, max_width))
        self.adjustSize()

        overflow = max(0, self.height() - max_height)
        if overflow:
            visible_scrolls = [
                scroll for scroll in self.findChildren(QScrollArea)
                if scroll.isVisibleTo(self)
            ]
            for scroll in visible_scrolls:
                reducible = max(0, scroll.height() - _MIN_FITTED_SCROLL_HEIGHT)
                reduction = min(overflow, reducible)
                if reduction:
                    scroll.setFixedHeight(scroll.height() - reduction)
                    parent = scroll.parentWidget()
                    while parent is not None and parent is not self:
                        if isinstance(parent, QStackedWidget):
                            parent.setFixedHeight(max(1, parent.height() - reduction))
                            break
                        parent = parent.parentWidget()
                    overflow -= reduction
                if not overflow:
                    break
            self.layout().invalidate()
            self.adjustSize()

        if self.height() > max_height:
            self.setFixedHeight(max_height)

    def build_nav(self):
        """Return the RowList this panel should push onto the NavStack when
        opened. Override in subclasses. None means no navigable rows."""
        return None

    def play_open_animation(self, start_rect: QRect, end_rect: QRect) -> None:
        self.setGeometry(start_rect)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(160)
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.start()


class ActionRow(QFrame):
    """A single full-width pressable row with a label. Used wherever a panel
    needs one plain thing to press — Music's "Set up Spotify" / "Log in with
    Spotify" entry points, and Now Playing's link back to Spotify."""

    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        self._label = QLabel(text)
        self._label.setStyleSheet("font-size: 20px; font-weight: 600;")
        lay.addWidget(self._label)
        self.set_selected(False)

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(selected_row_style(selected, radius=14))


def open_in_spotify(panel, item) -> bool:
    """Opens a track/playlist in Spotify, closing the overlay first.

    Shared by every view that displays Spotify metadata, because their design
    guidelines require that such metadata always links back to the service.

    Tries the `spotify:` URI before the https one so the desktop app opens
    directly where it's installed (the guidelines ask for that specifically),
    and falls back to open.spotify.com otherwise. The overlay is closed first
    for the same reason the Spotify login flow closes it: it's frameless,
    always-on-top and holds the foreground, so anything launched underneath it
    would be invisible."""
    app_uri, web_url = spotify_client.links_for(item)
    if not app_uri and not web_url:
        _log.warning("No Spotify link available for %r", item)
        return False

    window = panel.window()
    close_menu = getattr(window, "close_menu", None)
    if callable(close_menu):
        close_menu()

    for target in (app_uri, web_url):
        if not target:
            continue
        if QDesktopServices.openUrl(QUrl(target)):
            _log.info("Opened %s in Spotify", target)
            return True
        _log.info("Couldn't open %s — trying the next form", target)
    _log.warning("Every Spotify link form failed for %r", item)
    return False


def message_label(text: str) -> QLabel:
    """The muted line a panel shows in place of rows — "Loading…", "Nothing
    else appears to be open right now", a failure. One helper so every
    panel's in-place-of-content states look the same, and so a list that is
    still loading has nothing selectable in it (the D-pad should have
    nowhere to go until there's something real to go to)."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.5);")
    return label


def clear_layout(layout) -> None:
    """Remove and delete every widget currently in a layout, so it can be
    rebuilt from scratch (e.g. a list that reloads from live data)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            # Unparented as well as deleted. deleteLater() doesn't destroy the
            # widget until control is back in the event loop, and until then it
            # carries on painting exactly where it was — so the rows a rebuild
            # is replacing show through underneath the new ones. This was
            # first hit when measuring pumped the event loop mid-rebuild;
            # that pump is gone, but unparenting stays, because deleteLater()
            # still defers destruction to the next event-loop turn and a
            # repaint before then would show the old rows.
            widget.setParent(None)
            widget.deleteLater()


def make_scrollable_rows():
    """A QScrollArea + the QVBoxLayout to add row widgets to inside it."""
    rows_layout = QVBoxLayout()
    rows_layout.setSpacing(6)
    rows_widget = QWidget()
    rows_widget.setLayout(rows_layout)

    scroll = QScrollArea()
    scroll.setWidget(rows_widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("background: transparent;")
    scroll.viewport().setStyleSheet("background: transparent;")
    return scroll, rows_layout


def fit_scroll_to_content(scroll: QScrollArea, max_height: int = DEFAULT_LIST_MAX_HEIGHT) -> None:
    """QScrollArea's own sizeHint doesn't reflect its content widget's real
    size (it has its own small generic default) — call this after adding
    rows so the panel actually grows to fit them, up to max_height, instead
    of rendering squished.

    Newly added row widgets are still flagged hidden, and Qt layouts leave
    hidden widgets out of sizeHint, so measuring straight after adding rows
    measures an empty list — 18px for a list that wants 242. (This, and the
    whole reason this function exists, came out of a real, hard-to-spot bug:
    see the Phase D Music panel bug-fix history for the full story.)

    This used to call QApplication.processEvents() to clear that flag, which
    worked and was the wrong tool: it re-enters the event loop in the middle
    of a list rebuild, so a queued controller press or a load completing
    could run against rows that were half-replaced. Showing each row
    explicitly clears the same flag with no event loop involved, and measures
    identically (verified both ways, including with the panel still hidden,
    which is the normal case — rows get built before the overlay is shown).

    show() here does not put anything on screen. It clears "explicitly
    hidden"; a row whose parent chain is still hidden stays invisible and
    simply starts counting toward the layout's sizeHint, which is all this
    needs."""
    rows_widget = scroll.widget()
    rows_layout = rows_widget.layout()
    if rows_layout is not None:
        for i in range(rows_layout.count()):
            item = rows_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.show()
    content_height = rows_widget.sizeHint().height()
    height = min(content_height, max_height)
    # Panel.fit_to_viewport() may temporarily reduce this on a shorter screen.
    # Keep the content-sized value so moving back to a taller monitor restores
    # the list instead of leaving it permanently compressed.
    scroll.setProperty("dsqmPreferredHeight", height)
    scroll.setFixedHeight(height)
