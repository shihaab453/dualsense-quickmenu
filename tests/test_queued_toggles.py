"""Queued toggles must use the state left by the preceding command."""
import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('control', ['like', 'shuffle'])
def test_two_queued_toggles_restore_original_state(monkeypatch, tmp_path, control):
    import settings
    from actions import spotify_client as sp
    from panels.music import MusicPanel

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, 'data_dir', lambda: str(tmp_path))
    jobs, writes = [], []
    state = {'value': False}
    monkeypatch.setattr(sp, 'submit', jobs.append)
    monkeypatch.setattr(sp, 'is_configured', lambda: False)
    monkeypatch.setattr(sp, 'is_liked', lambda tid: state['value'])
    monkeypatch.setattr(sp, 'get_current_playback', lambda: {'shuffle_state': state['value']})

    def write(value):
        writes.append(value)
        state['value'] = value

    monkeypatch.setattr(sp, 'set_liked', lambda tid, value: write(value))
    monkeypatch.setattr(sp, 'set_shuffle', write)
    panel = MusicPanel()
    panel.build_nav()
    panel._detail.current_track_id = 'track'
    tile = panel._like_tile if control == 'like' else panel._shuffle_tile
    panel._on_tile_activated(0, tile)
    panel._on_tile_activated(0, tile)
    assert len(jobs) == 2 and writes == []
    while jobs:
        jobs.pop(0)()
    assert writes == [True, False]
    assert not state['value']
    panel.close()
