# Phase E: the real Task Switcher — "RECENT GAMES" (configured games that
# are currently running, detected via psutil) and "PINNED GAMES" (every
# game you've configured), matching the mockup's two-section layout. Cross
# on a row launches that game.
#
# There's no Windows API for "recently played games" the way a PS5 has, so
# this is backed by a list you maintain yourself, through the Settings window
# (tray icon -> Settings). See actions/games.py.

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from actions import games
from nav import RowList
from panels.base import (
    Panel,
    clear_layout,
    fit_scroll_to_content,
    make_scrollable_rows,
    selected_row_style,
)


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        "font-size: 13px; font-weight: 700; letter-spacing: 1px;"
        " color: rgba(255,255,255,0.45);"
    )
    return label


class _GameRow(QFrame):
    def __init__(self, game: dict):
        super().__init__()
        self.game = game
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)

        thumb = QLabel()
        thumb.setFixedSize(56, 56)
        # A color derived from the name so different games are at least
        # visually distinct — real box art is a Phase F nicety, not this.
        hue = abs(hash(game.get("name", ""))) % 360
        thumb.setStyleSheet(f"background: hsl({hue}, 45%, 40%); border-radius: 10px;")
        lay.addWidget(thumb)

        name = QLabel(game.get("name", "Unknown"))
        name.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(name)
        lay.addStretch(1)

        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(selected_row_style(selected, radius=12))


class SwitcherPanel(Panel):
    def __init__(self):
        super().__init__("Switcher", width=860)
        self._scroll, self._rows_container = make_scrollable_rows()
        self.body.addWidget(self._scroll)
        self._game_rows = []

    def build_nav(self):
        clear_layout(self._rows_container)
        self._game_rows = []

        recent = games.get_recent_games()
        pinned = games.get_pinned_games()

        if recent:
            self._rows_container.addWidget(_section_label("RECENT GAMES"))
            for g in recent:
                row = _GameRow(g)
                self._game_rows.append(row)
                self._rows_container.addWidget(row)

        if pinned:
            self._rows_container.addWidget(_section_label("PINNED GAMES"))
            for g in pinned:
                row = _GameRow(g)
                self._game_rows.append(row)
                self._rows_container.addWidget(row)

        if not self._game_rows:
            hint = QLabel(
                "No games added yet. Right-click the tray icon and choose"
                " Settings to add some."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.5);")
            self._rows_container.addWidget(hint)

        fit_scroll_to_content(self._scroll)

        def on_activate(index, row):
            try:
                games.launch(row.game.get("path", ""))
            except Exception:
                pass  # bad/missing path — nothing sensible to do but not crash

        return RowList(self._game_rows, on_activate=on_activate, orientation="vertical")
