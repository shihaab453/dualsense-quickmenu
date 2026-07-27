# Phase C: the real Power panel — Sleep (selected by default, with helper
# text, matching the mockup), Shut Down, Restart. Cross on a row performs
# the action immediately; see actions/power.py for why there's no separate
# confirmation step.

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from actions import power
from icons import render_icon
from nav import RowList
from panels.base import Panel, selected_row_style


def _apply_selected_style(frame: QFrame, selected: bool) -> None:
    frame.setStyleSheet(selected_row_style(selected))


class _ActionRow(QFrame):
    def __init__(self, icon_name: str, label: str, action, description: str = ""):
        super().__init__()
        self.setObjectName("row")
        self.action = action
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        icon = QLabel()
        icon.setPixmap(render_icon(icon_name, "#3ddc97", 22))
        title_row.addWidget(icon)
        title = QLabel(label)
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        lay.addLayout(title_row)
        if description:
            desc = QLabel(description)
            desc.setWordWrap(True)
            desc.setStyleSheet(
                "font-size: 15px; color: rgba(255,255,255,0.5); margin-left: 36px;"
            )
            lay.addWidget(desc)
        _apply_selected_style(self, False)

    def set_selected(self, selected: bool) -> None:
        _apply_selected_style(self, selected)


def _divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(255,255,255,0.08);")
    return line


class PowerPanel(Panel):
    def __init__(self):
        super().__init__("Power", width=780)

        self._rows = [
            _ActionRow(
                "power",
                "Sleep",
                power.sleep,
                "Turns off the display and suspends active apps. The PC stays powered on.",
            ),
            _ActionRow("power", "Shut Down", power.shut_down),
            _ActionRow("restart", "Restart", power.restart),
        ]
        for index, row in enumerate(self._rows):
            if index > 0:
                self.body.addWidget(_divider())
            self.body.addWidget(row)

    def build_nav(self):
        def on_activate(index, row):
            row.action()

        return RowList(self._rows, on_activate=on_activate, orientation="vertical")
