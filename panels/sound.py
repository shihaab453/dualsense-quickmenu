# Phase B: the real Sound panel, matching the design mockup — Output Device
# (read-only), Master Volume slider, Mute Microphone toggle, Input Device
# (read-only), Mic Volume slider. All five rows are reachable with D-pad
# up/down (the mockup itself only ever shows one row "selected" since it has
# no real navigation — ours does, via the same RowList every panel uses).
#
# Real device *switching* needs an undocumented Windows COM interface
# (IPolicyConfig) with no first-party support — deferred as a stretch goal,
# not attempted here. These two device rows just display the current name.

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import logs
from actions import volume
from icons import render_icon, split_color_opacity
from nav import RowList
from panels.base import Panel, selected_row_style

log = logs.get(__name__)


def _qcolor(color: str) -> QColor:
    """QColor can't parse the CSS rgba(...) strings used everywhere else in
    this app, so route through the same hex+opacity split icons.py already
    uses for the same reason with SVG fills."""
    hex_color, opacity = split_color_opacity(color)
    qcolor = QColor(hex_color)
    qcolor.setAlphaF(opacity)
    return qcolor


def _apply_selected_style(frame: QFrame, selected: bool) -> None:
    frame.setStyleSheet(selected_row_style(selected))


def _icon_label(icon_name: str) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(render_icon(icon_name, "rgba(255,255,255,0.7)", 20))
    return lbl


class _InfoRow(QFrame):
    """A read-only row: an icon + label plus a current value (a device name)."""

    def __init__(self, icon_name: str, label: str, get_value):
        super().__init__()
        self.setObjectName("row")
        self._get_value = get_value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(_icon_label(icon_name))
        self._label = QLabel(label)
        self._label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._value = QLabel()
        self._value.setStyleSheet("font-size: 16px; color: rgba(255,255,255,0.5);")
        lay.addWidget(self._label)
        lay.addStretch(1)
        lay.addWidget(self._value)
        _apply_selected_style(self, False)
        self.refresh()

    def refresh(self) -> None:
        try:
            self._value.setText(self._get_value())
        except Exception:
            # Usually pycaw failing to enumerate a device — e.g. no microphone
            # is plugged in at all.
            log.exception("Couldn't read the value for a Sound device row")
            self._value.setText("Unavailable")

    def set_selected(self, selected: bool) -> None:
        _apply_selected_style(self, selected)


class _SliderTrack(QWidget):
    """Custom-painted volume track: filled bar + a circular white thumb at
    the fill's edge, matching the mockup exactly — QProgressBar has no way
    to draw a handle, only a flat filled chunk."""

    def __init__(self, fill_color: QColor, track_height: int = 4, thumb_size: int = 18):
        super().__init__()
        self._percent = 0
        self._fill_color = fill_color
        self._track_height = track_height
        self._thumb_size = thumb_size
        self.setFixedHeight(thumb_size)

    def set_percent(self, percent: int) -> None:
        self._percent = max(0, min(100, percent))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        w = self.width()
        th = self._track_height
        mid = self.height() / 2

        painter.setBrush(QColor(255, 255, 255, 38))
        painter.drawRoundedRect(QRectF(0, mid - th / 2, w, th), th / 2, th / 2)

        fill_w = w * (self._percent / 100)
        painter.setBrush(self._fill_color)
        painter.drawRoundedRect(QRectF(0, mid - th / 2, fill_w, th), th / 2, th / 2)

        ts = self._thumb_size
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(fill_w - ts / 2, mid - ts / 2, ts, ts))
        painter.end()


class _VolumeRow(QFrame):
    """Selectable row: Left/Right (via RowList.adjust) changes a volume level."""

    def __init__(self, icon_name: str, label: str, get_percent, change_percent, fill_color: str = "#ffffff"):
        super().__init__()
        self.setObjectName("row")
        self._get_percent = get_percent
        self._change_percent = change_percent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        label_row = QHBoxLayout()
        label_row.setSpacing(12)
        label_row.addWidget(_icon_label(icon_name))
        self._label = QLabel(label)
        self._label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._percent = QLabel()
        self._percent.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.5);")
        label_row.addWidget(self._label)
        label_row.addStretch(1)
        label_row.addWidget(self._percent)
        lay.addLayout(label_row)

        self._bar = _SliderTrack(_qcolor(fill_color))
        lay.addWidget(self._bar)

        _apply_selected_style(self, False)
        self.refresh()

    def refresh(self) -> None:
        try:
            percent = self._get_percent()
        except Exception:
            log.exception("Couldn't read a volume level; showing 0%%")
            percent = 0
        self._bar.set_percent(percent)
        self._percent.setText(f"{percent}%")

    def adjust(self, delta: int) -> None:
        try:
            self._change_percent(delta)
        except Exception:
            log.exception("Couldn't change a volume level by %s%%", delta)
        self.refresh()

    def set_selected(self, selected: bool) -> None:
        _apply_selected_style(self, selected)


class _ToggleRow(QFrame):
    """Selectable row: Cross flips a switch-style boolean setting."""

    def __init__(self, icon_name: str, label: str, get_state, set_state):
        super().__init__()
        self.setObjectName("row")
        self._get_state = get_state
        self._set_state = set_state
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(_icon_label(icon_name))
        self._label = QLabel(label)
        self._label.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._switch = QLabel()
        self._switch.setFixedSize(44, 24)
        lay.addWidget(self._label)
        lay.addStretch(1)
        lay.addWidget(self._switch)
        _apply_selected_style(self, False)
        self.refresh()

    def refresh(self) -> None:
        try:
            on = self._get_state()
        except Exception:
            log.exception("Couldn't read a Sound toggle's state; assuming off")
            on = False
        color = "#3ddc97" if on else "rgba(255,255,255,0.2)"
        self._switch.setStyleSheet(f"background: {color}; border-radius: 12px;")

    def toggle(self) -> None:
        try:
            self._set_state()
        except Exception:
            log.exception("Couldn't toggle a Sound control (e.g. mic mute)")
        self.refresh()

    def set_selected(self, selected: bool) -> None:
        _apply_selected_style(self, selected)


class SoundPanel(Panel):
    def __init__(self):
        super().__init__("Sound", width=1040)

        self._output_row = _InfoRow("sound", "Output Device", volume.get_output_device_name)
        self._volume_row = _VolumeRow(
            "sound", "Master Volume", volume.get_percent, volume.change_percent,
            fill_color="#3ddc97",
        )
        self._mic_mute_row = _ToggleRow(
            "mic", "Mute Microphone", volume.is_mic_muted, volume.toggle_mic_mute
        )
        self._input_row = _InfoRow("mic", "Input Device", volume.get_input_device_name)
        self._mic_volume_row = _VolumeRow(
            "mic", "Mic Volume", volume.get_mic_percent, volume.change_mic_percent,
            fill_color="rgba(255,255,255,0.55)",
        )
        self._rows = [
            self._output_row,
            self._volume_row,
            self._mic_mute_row,
            self._input_row,
            self._mic_volume_row,
        ]
        for row in self._rows:
            self.body.addWidget(row)

        note = QLabel("Switching output/input devices is coming in a later update.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.35);")
        self.body.addWidget(note)

    def build_nav(self):
        for row in self._rows:
            row.refresh()

        def on_activate(index, row):
            if row is self._mic_mute_row:
                row.toggle()

        return RowList(self._rows, on_activate=on_activate, orientation="vertical")
