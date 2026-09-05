# Fetches Spotify album art on demand. Downloads happen on a background
# thread (this app already uses the same "background thread + Qt signal"
# shape for Spotify login, see actions/spotify_client.py's login_async) so
# opening a tracklist never blocks the overlay while images come in — the
# only main-thread work is decoding already-downloaded bytes into a QPixmap,
# since QPixmap should only be touched on the GUI thread.

import threading
import urllib.request
from collections import OrderedDict

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap

import logs

log = logs.get(__name__)

# Spotify's design guidelines specify the corner radius for album artwork: 4px
# on small/medium devices, 8px on large ones. This is a desktop app, so 8px
# applies everywhere — it's a stated number in terms this app has to meet, so
# every place artwork is drawn uses this rather than picking something that
# looks about right. The same value has to be used for the placeholder shown
# while art downloads, or the corners visibly jump when it arrives.
CORNER_RADIUS = 8

# url -> raw decoded pixmap, not yet scaled/rounded. Bounded, and in access
# order: this app sits in the tray for days, and paging through a large
# library used to add a pixmap per track and never drop one. A few hundred
# thumbnails is plenty to keep scrolling smooth without the cache quietly
# becoming the largest thing in the process.
_CACHE_LIMIT = 200
_cache: "OrderedDict[str, QPixmap]" = OrderedDict()


def _remember(url: str, pixmap: QPixmap) -> None:
    _cache[url] = pixmap
    _cache.move_to_end(url)
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)


def forget_all() -> None:
    """Drop every cached image. Called on logout: album art is account-bound
    data, and the next person to use this machine should not find the previous
    account's artwork still on screen."""
    _cache.clear()
    _loader.cancel_all()


def smallest_image_url(track: dict):
    """Spotify sorts album images largest-first; the smallest (usually
    64x64) is plenty for a row thumbnail and far cheaper to fetch."""
    images = (track.get("album") or {}).get("images") or []
    return images[-1]["url"] if images else None


def largest_image_url(track: dict):
    images = (track.get("album") or {}).get("images") or []
    return images[0]["url"] if images else None


def rounded(pixmap: QPixmap, size: int, radius: int) -> QPixmap:
    """Scales to a size x size square with rounded corners baked in —
    QLabel's own border-radius stylesheet only clips its background/border,
    not a pixmap drawn inside it, so the corners have to be baked into the
    pixmap itself instead. Spotify album art is always square already."""
    scaled = pixmap.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    result = QPixmap(size, size)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return result


class _Loader(QObject):
    _downloaded = Signal(str, bytes)

    def __init__(self):
        super().__init__()
        self._pending: dict[str, list] = {}
        self._downloaded.connect(self._on_downloaded)

    def get(self, url, size: int, radius: int, callback) -> None:
        """callback(QPixmap | None) runs on the Qt main thread with a
        size x size rounded pixmap — immediately if this url's raw image is
        already cached, otherwise once the download (or failure) completes.
        None means no art available; callers should keep their placeholder."""
        if not url:
            callback(None)
            return
        raw = _cache.get(url)
        if raw is not None:
            _cache.move_to_end(url)
            callback(rounded(raw, size, radius))
            return
        already_in_flight = url in self._pending
        self._pending.setdefault(url, []).append((size, radius, callback))
        if not already_in_flight:
            threading.Thread(target=self._fetch, args=(url,), daemon=True).start()

    def _fetch(self, url: str) -> None:
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = resp.read()
        except Exception:
            # Empty bytes leaves the color placeholder in place, which is a
            # fine outcome — logged at warning rather than exception because a
            # flaky image CDN is not a bug in this app.
            log.warning("Album art download failed for %s", url, exc_info=True)
            data = b""
        self._downloaded.emit(url, data)

    def _on_downloaded(self, url: str, data: bytes) -> None:
        pending = self._pending.pop(url, [])
        if not pending:
            return
        raw = None
        if data:
            candidate = QPixmap()
            if candidate.loadFromData(data):
                raw = candidate
        if raw is not None:
            _remember(url, raw)
        for size, radius, callback in pending:
            callback(rounded(raw, size, radius) if raw is not None else None)

    def cancel_all(self) -> None:
        """Drop pending account artwork and complete callbacks with no image."""
        pending, self._pending = self._pending, {}
        for requests in pending.values():
            for _size, _radius, callback in requests:
                callback(None)


_loader = _Loader()


def get(url, size: int, radius: int, callback) -> None:
    _loader.get(url, size, radius, callback)
