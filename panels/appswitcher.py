# A live Alt-Tab-style window switcher — genuinely different from the removed
# Task Switcher (see HANDOFF.md): that one needed a user-curated pinned-games
# list; this one needs no configuration at all and works for any open app,
# built fresh each time from Windows' own window list via
# actions/window_switcher.py. Reached from a home card (overlay.py's
# _AppSwitcherCard), not a tray icon — same pattern the Now Playing panel
# already uses.

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

import logs
from actions import window_switcher
from nav import RowList
from panels.base import (
    Panel,
    clear_layout,
    fit_scroll_to_content,
    make_scrollable_rows,
    message_label,
    selected_row_style,
)
from workers import SYSTEM, Loader

log = logs.get(__name__)

_ICON_SIZE = 32


class _WindowRow(QFrame):
    def __init__(self, window: dict):
        super().__init__()
        self.window = window
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        self.icon_label = icon_label = QLabel()
        icon_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        # The enumeration runs on a worker thread and so hands back a QImage;
        # QPixmap is a GUI-thread type, and this is the GUI thread.
        image = window.get("icon_image")
        icon = None if image is None else QPixmap.fromImage(image)
        if icon is not None and not icon.isNull():
            icon_label.setPixmap(icon)
        else:
            # Extraction can fail for an individual window (rare — see
            # actions/window_switcher.py's fallback chain) — a plain
            # placeholder beats a blank gap in the row.
            icon_label.setStyleSheet(
                "background: rgba(255,255,255,0.12); border-radius: 6px;"
            )
        lay.addWidget(icon_label)

        title = QLabel(window.get("title", ""))
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        lay.addWidget(title)
        lay.addStretch(1)

        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(selected_row_style(selected, radius=12))


class AppSwitcherPanel(Panel):
    def __init__(self):
        super().__init__("Switch App", width=860)
        self._scroll, self._rows_container = make_scrollable_rows()
        self.body.addWidget(self._scroll)
        self._rows = []
        self._windows = None
        self._nav = None
        self._failed_switch = ""
        self._failure_presented = False
        # Its own worker rather than the Spotify one: enumerating windows has
        # nothing to do with Spotify, and opening the switcher shouldn't queue
        # up behind whatever the Music panel last asked for. Enumeration is
        # usually only tens of milliseconds, but it walks every top-level
        # window and asks the shell for icons, so it isn't bounded — one hung
        # process is all it takes.
        self._loader = Loader(SYSTEM.submit, "appswitcher")

    def build_nav(self):
        # A failed switch closes the overlay before we can report it. Keep
        # that message throughout the next visit, including its async refresh,
        # and clear it only on the visit after that one.
        if self._failure_presented:
            self._failed_switch = ""
        self._failure_presented = bool(self._failed_switch)
        self._nav = RowList(
            [],
            on_activate=self._on_activate,
            # Without this the selection can move off the bottom of the
            # viewport: styling a row does not scroll it into view, so with
            # twenty windows open the highlight was invisible and Cross
            # switched to something the user could not see.
            on_select=lambda i, row: self._scroll.ensureWidgetVisible(row),
            orientation="vertical",
        )
        self._render()
        self._loader.start(window_switcher.list_switchable_windows, self._on_listed)
        return self._nav

    def _on_listed(self, value, error) -> None:
        if error is not None:
            log.exception("Couldn't enumerate open windows", exc_info=error)
            self._windows = self._windows or []
        else:
            self._windows = value
        self._render()

    def _report_failed_switch(self, title: str) -> None:
        """Say so in the panel. The overlay is already closing by this point,
        so this is what the user sees when they reopen it rather than a silent
        no-op."""
        self._windows = None
        self._failed_switch = title
        self._failure_presented = False
        self._render()

    def _render(self) -> None:
        # Emptied before the rebuild for the same reason the Music panel does
        # it: measuring pumps the event loop, so a press can arrive against
        # rows that are on their way out. See HANDOFF gotcha #16.
        keep = None
        if self._nav is not None:
            row = self._nav.selected_row()
            keep = None if row is None else row.window.get("hwnd")
            self._nav.replace_rows([])
        clear_layout(self._rows_container)
        self._rows = []

        if self._failed_switch:
            self._rows_container.addWidget(
                message_label(
                    f"Couldn't switch to {self._failed_switch}. It may have "
                    "closed. Choose a window to try again."
                )
            )
        # The message must not displace the fresh, selectable window list.
        if self._windows is None:
            self._rows_container.addWidget(message_label("Loading…"))
        elif not self._windows:
            self._rows_container.addWidget(
                message_label("Nothing else appears to be open right now.")
            )
        else:
            for w in self._windows:
                row = _WindowRow(w)
                self._rows.append(row)
                self._rows_container.addWidget(row)

        fit_scroll_to_content(self._scroll)
        if self._nav is not None:
            # Restore by window handle: a refresh reorders the list by
            # z-order, so the previous row *number* is a different window.
            index = next(
                (i for i, r in enumerate(self._rows) if r.window.get("hwnd") == keep),
                0,
            )
            self._nav.replace_rows(self._rows, index)
        self.request_relayout()

    def _on_activate(self, index, row) -> None:
        # The overlay is frameless, always-on-top and forces itself into
        # the foreground on open (see overlay._force_foreground) — closing
        # it first is why the Spotify login flow and the "Open in
        # Spotify" link both do the same thing before handing focus
        # elsewhere; otherwise the app we just switched to would be stuck
        # underneath our own translucent overlay.
        overlay = self.window()
        close_menu = getattr(overlay, "close_menu", None)
        if callable(close_menu):
            close_menu()
        if not window_switcher.switch_to(row.window["hwnd"]):
            # The window can have closed since the list was built, or Windows
            # can refuse the foreground change. Closing the overlay and
            # leaving the user staring at whatever was already in front, with
            # no explanation, is the one outcome worth avoiding.
            log.warning(
                "Couldn't bring %r to the front", row.window.get("title", "?")
            )
            self._report_failed_switch(row.window.get("title", "that window"))
