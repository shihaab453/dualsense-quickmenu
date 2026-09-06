"""Metadata the home card truncates must be readable somewhere.

Spotify's design guidelines allow truncating displayed metadata only where
full viewing capability exists. The home card elides all three of its lines to
a fixed width (_NowPlayingCard._elide), which is legitimate for a 260px card -
but only if the untruncated text can be reached.

Title and artist could be: pressing Cross opens this panel, whose song label is
word-wrapped rather than elided. The *source* line could not. The card showed
"From <album or playlist>", truncated, and the panel did not show it at all, so
a long album or playlist name had nowhere to be read in full. That is the gap
these tests hold closed - see SPOTIFY-GUIDELINES-REVIEW.md section 3.4.
"""

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.unit

# A name long enough that the card is guaranteed to elide it, so these tests
# are about a real truncation rather than a hypothetical one.
LONG_SOURCE = "The Absolutely Enormous Playlist Name That Will Not Fit"
TRACK = {
    "id": "t1",
    "name": "A Song",
    "uri": "spotify:track:t1",
    "artists": [{"name": "An Artist"}],
    "album": {"images": []},
}


def _panel(monkeypatch, tmp_path, playback, source=LONG_SOURCE):
    import settings
    from actions import album_art, spotify_client as sp
    from actions import now_playing
    from nav import NavStack
    from panels.nowplaying import NowPlayingPanel

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(album_art, "get", lambda u, s, r, cb: cb(None))
    monkeypatch.setattr(sp, "submit", lambda job: job())
    monkeypatch.setattr(sp, "is_logged_in", lambda: True)
    monkeypatch.setattr(sp, "is_configured", lambda: True)
    monkeypatch.setattr(sp, "get_current_playback", lambda: playback)
    monkeypatch.setattr(sp, "resolve_context_name", lambda pb: source)
    monkeypatch.setattr(now_playing, "get", lambda: None)

    panel = NowPlayingPanel()
    # The winrt lookup runs on the MEDIA worker, which is a real background
    # thread - replaced with an inline one so the assertions below are not
    # racing it. The Spotify side is already inline via sp.submit.
    from workers import Loader
    panel._winrt_loader = Loader(lambda job: job(), "test/winrt")
    panel.nav = NavStack()
    panel.build_nav()
    return panel


def test_the_source_is_shown_in_full(monkeypatch, tmp_path):
    panel = _panel(monkeypatch, tmp_path, {"item": TRACK, "is_playing": True})
    assert panel._source_label.isVisibleTo(panel)
    # In full, and not elided: the whole name is present, with no ellipsis.
    assert panel._source_label.text() == f"From {LONG_SOURCE}"
    assert "…" not in panel._source_label.text()
    # Word wrap is what lets it be long without being cut off.
    assert panel._source_label.wordWrap()
    panel.close()


def test_the_card_elides_the_same_source(monkeypatch, tmp_path):
    """The other half of the pair: this is the truncation being compensated
    for. If the card ever stops eliding, this test failing is a prompt to
    recheck whether the panel still needs to carry the full text - not a
    reason to delete it."""
    from overlay import _NowPlayingCard

    QApplication.instance() or QApplication([])
    card = _NowPlayingCard()
    card._elide(card._source_label, f"From {LONG_SOURCE}")
    shown = card._source_label.text()
    assert shown != f"From {LONG_SOURCE}", "the card is expected to truncate"
    assert shown.endswith("…")
    card.close()


def test_no_source_hides_the_row_rather_than_leaving_it_blank(monkeypatch, tmp_path):
    """Liked Songs, a single queued track and Spotify radio are
    indistinguishable from the API and legitimately have no context."""
    panel = _panel(
        monkeypatch, tmp_path, {"item": TRACK, "is_playing": True}, source=None
    )
    assert not panel._source_label.isVisibleTo(panel)
    assert panel._source_label.text() == ""
    panel.close()


def test_the_media_session_path_claims_no_source(monkeypatch, tmp_path):
    """The Windows media session reports title and artist only. It must not
    inherit the source left over from a previous Spotify lookup."""
    import settings
    from actions import album_art, spotify_client as sp
    from actions import now_playing
    from nav import NavStack
    from panels.nowplaying import NowPlayingPanel

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(album_art, "get", lambda u, s, r, cb: cb(None))
    monkeypatch.setattr(sp, "submit", lambda job: job())
    monkeypatch.setattr(sp, "is_logged_in", lambda: True)
    monkeypatch.setattr(sp, "is_configured", lambda: True)
    monkeypatch.setattr(sp, "resolve_context_name", lambda pb: LONG_SOURCE)
    monkeypatch.setattr(sp, "get_current_playback", lambda: {"item": TRACK})
    monkeypatch.setattr(now_playing, "get", lambda: None)

    from workers import Loader

    panel = NowPlayingPanel()
    panel._winrt_loader = Loader(lambda job: job(), "test/winrt")  # see _panel
    panel.nav = NavStack()
    panel.build_nav()
    assert panel._source_label.isVisibleTo(panel), "sanity: Spotify set a source"

    # Now Spotify has nothing and the media session answers instead.
    monkeypatch.setattr(sp, "get_current_playback", lambda: None)
    monkeypatch.setattr(
        now_playing, "get", lambda: {"title": "Fallback", "artist": "Someone"}
    )
    panel.build_nav()
    assert "Fallback" in panel._song_label.text()
    assert not panel._source_label.isVisibleTo(panel)
    panel.close()
