# The real Power panel. Sleep runs immediately; Shut Down and Restart require
# a held Cross press so an accidental D-pad confirmation cannot end a game.

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from actions import power
from icons import render_icon
from nav import RowList
from panels.base import Panel, selected_row_style


_HOLD_SECONDS = 1.0
_HOLD_TICK_MS = 50


def _apply_selected_style(frame: QFrame, selected: bool) -> None:
    frame.setStyleSheet(selected_row_style(selected))


class _ActionRow(QFrame):
    def __init__(self, icon_name: str, label: str, action, description: str = ""):
        super().__init__()
        self.setObjectName("row")
        self.action = action
        self.action_label = label
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
    def __init__(self, is_cross_held=lambda: False):
        super().__init__("Power", width=780)
        self._is_cross_held = is_cross_held
        self._confirming_row = None
        self._hold_started = None
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(_HOLD_TICK_MS)
        self._hold_timer.timeout.connect(self._advance_confirmation)

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

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.hide()
        self.body.addWidget(self._status)

    def build_nav(self):
        def on_activate(index, row):
            if index == 0:
                self._run_action(row)
            else:
                self._begin_confirmation(row)

        return RowList(
            self._rows,
            on_activate=on_activate,
            on_select=lambda _index, _row: self.cancel_confirmation(),
            orientation="vertical",
        )

    def _begin_confirmation(self, row) -> None:
        if self._confirming_row is not row:
            self.cancel_confirmation()
            self._confirming_row = row
            self._hold_started = time.monotonic()
            self._hold_timer.start()
        self._update_hold_status()

    def _advance_confirmation(self) -> None:
        if not self._is_cross_held():
            self.cancel_confirmation()
            return
        if time.monotonic() - self._hold_started >= _HOLD_SECONDS:
            row = self._confirming_row
            self.cancel_confirmation()
            self._run_action(row)
            return
        self._update_hold_status()

    def _update_hold_status(self) -> None:
        if self._confirming_row is None:
            return
        elapsed = time.monotonic() - self._hold_started
        progress = min(100, round(elapsed / _HOLD_SECONDS * 100))
        self._status.setText(
            f"Keep holding Cross to {self._confirming_row.action_label.lower()} ({progress}%)"
        )
        self._status.setStyleSheet("font-size: 15px; color: #3ddc97;")
        self._status.show()

    def cancel_confirmation(self) -> None:
        if self._confirming_row is None:
            return
        self._hold_timer.stop()
        self._confirming_row = None
        self._hold_started = None
        self._status.hide()

    def _run_action(self, row) -> None:
        try:
            row.action()
        except Exception as error:
            self._status.setText(f"Couldn't {row.action_label.lower()}: {error}")
            self._status.setStyleSheet("font-size: 15px; color: #ff8a8a;")
            self._status.show()

    def hideEvent(self, event) -> None:
        self.cancel_confirmation()
        super().hideEvent(event)
