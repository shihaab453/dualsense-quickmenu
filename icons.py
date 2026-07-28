# Real vector icons, pulled directly from the design mockup's own inline
# SVG markup (Control Centre Overlay.dc.html) and rendered via Qt's SVG
# support — not reproductions or approximations, the actual path data.
#
# This also fixes a real functional bug from earlier phases: Windows renders
# color emoji as fixed-color bitmaps that ignore CSS/Qt style colors, so a
# toggle's "on" tint (shuffle/repeat) couldn't visually show at all. Vector
# icons recolor properly, so that state becomes visible again.
#
# Each template is a full, self-contained SVG string with a `{c}` placeholder
# everywhere a color is used, so one shape can be rendered in any color a
# panel needs (white, dimmed, accent-green, etc.) without keeping separate
# assets per state.

import re

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel

# The app's own branding mark — the blue "PS" disc used for the tray icon and,
# via tools/make_icon.py, the .exe's icon. Drawn in code at whatever size is
# asked for rather than shipped as a bitmap, so it stays crisp at the 16px
# Explorer needs and the 256px the taskbar wants.
_APP_ICON_BLUE = "#2d6ff2"

_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")


def split_color_opacity(color: str):
    """Qt's SVG renderer doesn't understand the CSS rgba(...) function (nor
    8-digit hex) in fill/stroke attributes — it silently renders nothing.
    Since the rest of this app already passes rgba(...) strings around for
    translucent UI everywhere else, it's simpler to accept them here too and
    split them into a plain hex color + an SVG-level `opacity` attribute
    (which Qt *does* support) than to hunt down and rewrite every caller."""
    match = _RGBA_RE.match(color.strip())
    if not match:
        return color, 1.0
    r, g, b, a = match.groups()
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}", float(a)

def render_app_icon(size: int) -> QPixmap:
    """The blue "PS" disc, at any size. Shared by main.py's tray icon and the
    .ico generator so the two can't drift apart."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(_APP_ICON_BLUE))
    painter.setPen(Qt.NoPen)
    inset = max(1, round(size * 0.0625))
    painter.drawEllipse(inset, inset, size - inset * 2, size - inset * 2)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(6, round(size * 0.40)))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "PS")
    painter.end()
    return pixmap


_ICONS = {
    "home": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11L12 4l8 7"/><path d="M6 10v9a1 1 0 0 0 1 1h4v-6h2v6h4a1 1 0 0 0 1-1v-9"/></svg>',
    # Generic "opens somewhere outside this app" arrow. Deliberately not a
    # Spotify mark: their guidelines forbid modifying or approximating the logo,
    # so the button that links out is a neutral glyph and the branding is left
    # to the real asset.
    "external": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 4h7v7"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
    "chats": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2"><circle cx="9" cy="10" r="3.2"/><circle cx="16" cy="10" r="3.2" opacity=".55"/><path d="M3.5 20c0-3 2.5-5 5.5-5s5.5 2 5.5 5"/><path d="M12.5 20c0-2.6 2.2-4.4 4.8-4.4s4.7 1.8 4.7 4.4" opacity=".55"/></svg>',
    "music": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"><path d="M4 15V9"/><path d="M9 18V6"/><path d="M14 15V9"/><path d="M19 12V9"/></svg>',
    "sound": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"><path d="M7 8h4l6-4v16l-6-4H7z" fill="{c}" stroke="none"/><path d="M17 9a4 4 0 0 1 0 6"/></svg>',
    "power": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2"><circle cx="12" cy="13" r="8"/><line x1="12" y1="4" x2="12" y2="12"/></svg>',
    "restart": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2"><path d="M20 12a8 8 0 1 1-3-6.3"/><polyline points="20 4 20 8 16 8"/></svg>',
    "play": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}"><polygon points="7,4 20,12 7,20"/></svg>',
    "pause": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}"><rect x="5" y="4" width="5" height="16"/><rect x="14" y="4" width="5" height="16"/></svg>',
    "previous": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}"><polygon points="19,5 19,19 8,12"/><rect x="5" y="5" width="2.5" height="14"/></svg>',
    "next": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}"><polygon points="5,5 5,19 16,12"/><rect x="16.5" y="5" width="2.5" height="14"/></svg>',
    "like_outline": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2"><path d="M12 21s-7.5-4.9-10-9.3C.6 8.7 2 5 5.5 5c2 0 3.5 1.2 4.5 2.8C11 6.2 12.5 5 14.5 5 18 5 19.4 8.7 22 11.7 19.5 16.1 12 21 12 21z"/></svg>',
    "like_filled": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}" stroke="{c}" stroke-width="2"><path d="M12 21s-7.5-4.9-10-9.3C.6 8.7 2 5 5.5 5c2 0 3.5 1.2 4.5 2.8C11 6.2 12.5 5 14.5 5 18 5 19.4 8.7 22 11.7 19.5 16.1 12 21 12 21z"/></svg>',
    "shuffle": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
    "repeat": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
    "repeat_one": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><text x="10.5" y="15.5" font-size="8" font-weight="700" stroke="none" fill="{c}">1</text></svg>',
    "mic": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="21"/></svg>',
    "back": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
    "dots": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{c}"><circle cx="5" cy="12" r="2.2"/><circle cx="12" cy="12" r="2.2"/><circle cx="19" cy="12" r="2.2"/></svg>',
    "controller": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 26 18" fill="none" stroke="{c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7 2h12a5 5 0 0 1 5 5v3.5a4.5 4.5 0 0 1-4.5 4.5c-1 0-1.9-.4-2.6-1.1L15.5 12h-5L9.1 13.9c-.7.7-1.6 1.1-2.6 1.1A4.5 4.5 0 0 1 2 10.5V7a5 5 0 0 1 5-5z"/><line x1="6.5" y1="7" x2="6.5" y2="10.5"/><line x1="4.75" y1="8.75" x2="8.25" y2="8.75"/><circle cx="17" cy="6.5" r="1" fill="{c}" stroke="none"/><circle cx="19.5" cy="9" r="1" fill="{c}" stroke="none"/></svg>',
}


def render_icon(name: str, color: str, size: int) -> QPixmap:
    """Render one of the named icons at `size`x`size`, tinted `color` (any
    string Qt/SVG accepts: "#ffffff", "white", "rgba(...)", etc.)."""
    hex_color, opacity = split_color_opacity(color)
    svg = _ICONS[name].format(c=hex_color)
    if opacity < 1.0:
        svg = svg.replace("<svg ", f'<svg opacity="{opacity}" ', 1)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


def render_battery_pill(percent, color: str, width: int = 30, height: int = 16) -> QPixmap:
    """The controller-battery pill from the mockup, with a real fill level
    instead of a static 72% — percent=None (disconnected) draws it empty."""
    hex_color, opacity = split_color_opacity(color)
    fraction = 0.0 if percent is None else max(0.0, min(1.0, percent / 100))
    fill_width = 17 * fraction
    opacity_attr = f' opacity="{opacity}"' if opacity < 1.0 else ""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 16" fill="none"{opacity_attr}>'
        f'<rect x="1" y="1" width="25" height="14" rx="3" stroke="{hex_color}" stroke-width="1.5"/>'
        f'<rect x="27" y="5.5" width="2.5" height="5" rx="1" fill="{hex_color}"/>'
        f'<rect x="3" y="3" width="{fill_width}" height="10" rx="1.5" fill="{hex_color}"/>'
        f'</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


class IconLabel(QLabel):
    """A QLabel that shows one of the named vector icons, easily recolored —
    the drop-in replacement for emoji-glyph QLabels used throughout the app."""

    def __init__(self, name: str, color: str = "white", icon_size: int = 24):
        super().__init__()
        self._name = name
        self._icon_size = icon_size
        self.setAlignment(Qt.AlignCenter)
        self.set_icon(name, color)

    def set_icon(self, name: str, color: str) -> None:
        self._name = name
        self.setPixmap(render_icon(name, color, self._icon_size))

    def set_color(self, color: str) -> None:
        self.set_icon(self._name, color)
