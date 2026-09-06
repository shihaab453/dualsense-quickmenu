"""A new track must not inherit the previous track's decoded cover."""
import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('view', ['card', 'detail'])
def test_old_cover_clears_while_next_track_downloads(monkeypatch, view):
    from actions import album_art, spotify_client as sp
    from overlay import _NowPlayingCard
    from panels.music import MusicPanel

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sp, 'submit', lambda job: None)
    callbacks = []
    monkeypatch.setattr(album_art, 'get', lambda u, s, r, cb: callbacks.append(cb))
    old = QPixmap(20, 20)
    old.fill('red')
    if view == 'card':
        widget = _NowPlayingCard()
        label = widget._art_label
        label.setPixmap(old)
        widget._pending_art_url = 'old'
        widget._on_summary_ready(sp.session_generation(), {
            'title': 'New track', 'artists': 'New artist', 'art_url': 'new', 'is_playing': True,
        })
    else:
        widget = MusicPanel()
        label = widget._detail_art
        label.setPixmap(old)
        widget._detail.pending_track = {
            'id': 'new', 'name': 'New track', 'artists': [],
            'album': {'images': [{'url': 'new'}]},
        }
        widget._show_detail()
    assert callbacks, 'The replacement artwork must actually be pending'
    assert label.pixmap().isNull()
    callbacks[-1](None)
    assert label.pixmap().isNull(), 'A failed replacement must keep the placeholder'
    widget.close()
