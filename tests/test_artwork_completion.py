"""One deleted artwork consumer must not strand the remaining consumers."""
import io
import pytest
from PySide6.QtCore import QBuffer, QIODevice, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


def test_deleted_panel_does_not_prevent_other_artwork_callbacks(monkeypatch):
    from actions import album_art
    from panels.nowplaying import NowPlayingPanel

    app = QApplication.instance() or QApplication([])
    jobs = []

    class Thread:
        def __init__(self, *, target, args, daemon):
            jobs.append(lambda: target(*args))

        def start(self):
            pass

    raw = QPixmap(20, 20)
    raw.fill('red')
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert raw.save(buffer, 'PNG')
    monkeypatch.setattr(album_art.threading, 'Thread', Thread)
    monkeypatch.setattr(album_art.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(bytes(buffer.data())))
    panel = NowPlayingPanel()
    panel._current_art_id = 'track'
    received = []
    loader = album_art._Loader()
    loader.get('review-url', 20, 8, lambda pixmap: panel._on_art_loaded(pixmap, 'track'))
    loader.get('review-url', 20, 8, received.append)
    panel.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    try:
        assert len(jobs) == 1
        jobs[0]()
        assert len(received) == 1 and not received[0].isNull()
        assert loader._pending == {}
    finally:
        album_art._cache.pop('review-url', None)
