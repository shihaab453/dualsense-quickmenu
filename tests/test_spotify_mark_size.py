"""Both content headings must render the mark above its minimum size."""
import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('kind', ['music', 'nowplaying'])
def test_heading_mark_minimum(kind):
    from panels.music import MusicPanel
    from panels.nowplaying import NowPlayingPanel

    app = QApplication.instance() or QApplication([])
    panel = MusicPanel() if kind == 'music' else NowPlayingPanel()
    logo = panel._spotify_logo
    assert min(logo.width(), logo.height()) >= 21
    pixmap = logo.pixmap()
    assert not pixmap.isNull()
    assert min(pixmap.width(), pixmap.height()) / pixmap.devicePixelRatio() >= 21
    panel.close()
