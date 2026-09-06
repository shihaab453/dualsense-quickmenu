"""An empty rendered page is not necessarily an exhausted collection."""
import pytest
from PySide6.QtWidgets import QApplication, QLabel

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('playlist_id, linked', [(None, False), ('p', False), ('p', True)])
def test_filtered_first_page_still_offers_next_page(monkeypatch, tmp_path, playlist_id, linked):
    import settings
    from actions import album_art, spotify_client as sp
    from nav import NavStack
    from panels import music

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(sp, 'submit', lambda job: job())
    monkeypatch.setattr(album_art, 'get', lambda u, s, r, cb: cb(None))
    requested = []
    track = {'id': 'last', 'name': 'Last track', 'artists': [], 'album': {}}

    def page(offset):
        requested.append(offset)
        return ([], 21, 20) if offset == 0 else ([track], 21, 1)

    monkeypatch.setattr(sp, 'get_liked_songs_page', lambda limit, offset: page(offset))
    monkeypatch.setattr(sp, 'get_playlist_tracks_page', lambda pid, limit, offset: page(offset))
    panel = music.MusicPanel()
    panel.nav = NavStack()
    panel._open_songs_view(playlist_id, 'Collection', {'uri': 'spotify:playlist:p'} if linked else None)
    rows = panel.nav.current().rows
    assert requested == [0] and panel._songs.offset == 20 and panel._songs.total == 21
    assert rows and isinstance(rows[-1], music._LoadMoreRow)
    panel.nav.current().reselect(len(rows) - 1)
    panel.nav.current().activate()
    assert requested == [0, 20]
    assert panel.nav.current().selected_row().track['id'] == 'last'
    assert not any(isinstance(row, music._LoadMoreRow) for row in panel._songs.rows)
    panel.close()


def test_empty_linked_playlist_says_it_is_empty(monkeypatch, tmp_path):
    import settings
    from actions import spotify_client as sp
    from nav import NavStack
    from panels import music

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(sp, 'submit', lambda job: job())
    monkeypatch.setattr(sp, 'get_playlist_tracks_page', lambda *a, **k: ([], 0, 0))
    panel = music.MusicPanel()
    panel.nav = NavStack()
    panel._open_songs_view('p', 'Empty', {'uri': 'spotify:playlist:p'})
    assert any(isinstance(row, music._OpenPlaylistRow) for row in panel._songs.rows)
    labels = panel._songs_scroll.findChildren(QLabel)
    assert any(label.text() == "There's nothing in here yet." for label in labels)
    panel.close()
