"""A permanent refusal stops requests, while a transient failure can retry."""
import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('reason', ['playlist_restricted', 'other'])
def test_failed_more_row_controls_retry(monkeypatch, tmp_path, reason):
    import settings
    from actions import album_art, spotify_client as sp
    from nav import NavStack
    from panels import music

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(sp, 'submit', lambda job: job())
    monkeypatch.setattr(album_art, 'get', lambda u, s, r, cb: cb(None))
    requested = []

    def page(pid, limit, offset):
        requested.append(offset)
        if offset:
            raise sp.PlaybackUnavailable(reason)
        return ([{'id': str(i), 'name': str(i), 'artists': [], 'album': {}} for i in range(20)], 21, 20)

    monkeypatch.setattr(sp, 'get_playlist_tracks_page', page)
    panel = music.MusicPanel()
    panel.nav = NavStack()
    panel._open_songs_view('p', 'Paged', {'uri': 'spotify:playlist:p'})
    nav = panel.nav.current()
    assert len(panel._songs.items) == 20 and isinstance(nav.rows[-1], music._LoadMoreRow)
    nav.reselect(len(nav.rows) - 1)
    nav.activate()
    assert requested == [0, 20] and panel._songs.load_failed
    text = nav.rows[-1]._text.text()
    assert ('retry' in text) == (reason == 'other')
    nav.activate()
    assert requested == ([0, 20] if reason == 'playlist_restricted' else [0, 20, 20])
    assert not panel._songs.paging
    panel.close()


def test_playlist_endpoint_classifies_its_own_403(monkeypatch):
    from actions import spotify_client as sp
    from spotipy.exceptions import SpotifyException

    class Client:
        def playlist_items(self, *args, **kwargs):
            assert 'forbidden' not in kwargs
            raise SpotifyException(403, -1, 'forbidden')

    monkeypatch.setattr(sp, 'get_client', lambda: Client())
    with pytest.raises(sp.PlaybackUnavailable) as caught:
        sp.get_playlist_tracks_page('p')
    assert caught.value.reason == 'playlist_restricted'
