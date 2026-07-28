# A live Alt-Tab-style window switcher — genuinely different from the removed
# Task Switcher (see HANDOFF.md): that one needed a user-curated pinned-games
# list; this one needs no configuration at all and works for any open app,
# built fresh each time from Windows' own window list via
# actions/window_switcher.py. Reached from a home card (overlay.py's
# _AppSwitcherCard), not a tray icon — same pattern the Now Playing panel
# already uses.

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from actions import window_switcher
from nav import RowList
from panels.base import (
    Panel,
    clear_layout,
    fit_scroll_to_content,
    make_scrollable_rows,
    selected_row_style,
)

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
        icon = window.get("icon")
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

    def build_nav(self):
        clear_layout(self._rows_container)
        self._rows = []

        for w in window_switcher.list_switchable_windows():
            row = _WindowRow(w)
            self._rows.append(row)
            self._rows_container.addWidget(row)

        if not self._rows:
            hint = QLabel("Nothing else appears to be open right now.")
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.5);")
            self._rows_container.addWidget(hint)

        fit_scroll_to_content(self._scroll)

        def on_activate(index, row):
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
            window_switcher.switch_to(row.window["hwnd"])

        return RowList(self._rows, on_activate=on_activate, orientation="vertical")
