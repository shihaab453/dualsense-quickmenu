"""A cancelled download cannot complete a new request for the same URL."""
import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


def test_old_download_cannot_consume_new_request(monkeypatch):
    from actions import album_art

    app = QApplication.instance() or QApplication([])
    jobs = []

    class Thread:
        def __init__(self, *, target, args, daemon):
            jobs.append(lambda: target(*args))

        def start(self):
            pass

    def fail_download(*args, **kwargs):
        raise OSError('synthetic CDN failure')

    monkeypatch.setattr(album_art.threading, 'Thread', Thread)
    monkeypatch.setattr(album_art.urllib.request, 'urlopen', fail_download)
    loader = album_art._Loader()
    old, new = [], []
    loader.get('review-generation-url', 20, 8, old.append)
    loader.cancel_all()
    assert old == [None]
    loader.get('review-generation-url', 20, 8, new.append)
    assert len(jobs) == 2
    jobs[0]()
    app.processEvents()
    assert new == [], 'The old failure was delivered to the new request'
    assert 'review-generation-url' in loader._pending
    jobs[1]()
    app.processEvents()
    assert new == [None] and loader._pending == {}
