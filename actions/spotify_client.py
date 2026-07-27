# Spotify Web API access via spotipy, using the PKCE login flow — the right
# choice for a desktop app, since a "client secret" embedded in code isn't
# actually secret. spotipy's SpotifyPKCE already knows how to open the
# user's browser to Spotify's own login page, run a one-shot local web
# server to catch the redirect back, exchange the code for a token, and
# cache/refresh it on disk — so none of that needs to be hand-rolled here.
#
# The one thing we add on top: get_access_token() blocks until the user
# finishes in their browser, so login_async() runs it on a background
# thread — the same "background thread + callback" shape controller.py
# already uses for the controller listener — so the Qt overlay never
# freezes waiting for someone to click "Agree".

import os
import threading

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyPKCE

CLIENT_ID = "00000000000000000000000000000000REDACTED"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = " ".join(
    [
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-library-read",
        "user-library-modify",
        "playlist-read-private",
        "playlist-read-collaborative",
    ]
)

_CACHE_PATH = os.path.join(os.environ["APPDATA"], "DualSenseQuickMenu", "spotify_token.json")

_client = None  # cached spotipy.Spotify instance, built lazily once logged in


class PlaybackUnavailable(Exception):
    """Spotify couldn't service a playback request right now.

    reason is one of: "no_device" (nothing is actively playing Spotify
    anywhere), "premium_required" (free accounts can't control playback),
    "other" (anything else — network hiccup, etc).
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _auth_manager() -> SpotifyPKCE:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    return SpotifyPKCE(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=_CACHE_PATH,
        open_browser=True,
    )


def is_logged_in() -> bool:
    """True if we already have a valid (or refreshable) cached token —
    checking this never opens a browser."""
    auth = _auth_manager()
    return auth.validate_token(auth.cache_handler.get_cached_token()) is not None


def login_async(on_done) -> None:
    """Logs in on a background thread (opens the browser if there's no
    cached session) and calls on_done(success, error_message) when it's
    done. on_done fires on the background thread — callers must hop back
    to the Qt thread themselves (see MusicPanel's _LoginSignal)."""

    def worker():
        try:
            _auth_manager().get_access_token()
            on_done(True, None)
        except Exception as e:
            on_done(False, str(e))

    threading.Thread(target=worker, daemon=True).start()


def get_client() -> spotipy.Spotify:
    global _client
    if _client is None:
        _client = spotipy.Spotify(auth_manager=_auth_manager())
    return _client


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SpotifyException as e:
        if e.http_status == 404:
            raise PlaybackUnavailable("no_device") from e
        if e.http_status == 403:
            raise PlaybackUnavailable("premium_required") from e
        raise PlaybackUnavailable("other") from e


def get_current_playback():
    """None if nothing is playing anywhere, otherwise Spotify's playback
    state dict (is_playing, item, progress_ms, shuffle_state, repeat_state)."""
    return _call(get_client().current_playback)


def play_pause() -> None:
    playback = get_current_playback()
    if playback and playback.get("is_playing"):
        _call(get_client().pause_playback)
    else:
        _call(get_client().start_playback)


def next_track() -> None:
    _call(get_client().next_track)


def previous_track() -> None:
    _call(get_client().previous_track)


def set_shuffle(on: bool) -> None:
    _call(get_client().shuffle, on)


def set_repeat(mode: str) -> None:
    """mode is one of "off", "track", "context"."""
    _call(get_client().repeat, mode)


def is_liked(track_id: str) -> bool:
    return _call(get_client().current_user_saved_tracks_contains, [track_id])[0]


def set_liked(track_id: str, liked: bool) -> None:
    client = get_client()
    if liked:
        _call(client.current_user_saved_tracks_add, [track_id])
    else:
        _call(client.current_user_saved_tracks_delete, [track_id])


def get_liked_songs(limit: int = 20):
    result = _call(get_client().current_user_saved_tracks, limit=limit)
    return [item["track"] for item in result["items"]]


def get_liked_songs_total() -> int:
    # limit=1 so this is a cheap "just the count" call, not a full fetch.
    result = _call(get_client().current_user_saved_tracks, limit=1)
    return result.get("total", 0)


def get_playlists(limit: int = 6):
    result = _call(get_client().current_user_playlists, limit=limit)
    return result["items"]


def get_playlist_tracks(playlist_id: str, limit: int = 20):
    result = _call(get_client().playlist_items, playlist_id, limit=limit)
    # Docs say each item has the track data under "track", but the real
    # response for this endpoint nests it under "item" instead, with
    # "track" left as a true/false type-discriminator (track vs. episode) —
    # same class of doc-vs-reality mismatch as the playlist track-count
    # field ("tracks" vs "items") found earlier. Checking isinstance(...,
    # dict) rather than just truthiness catches that discriminator so it
    # doesn't get mistaken for real track data.
    tracks = []
    for entry in result["items"]:
        track = entry.get("track")
        if not isinstance(track, dict):
            track = entry.get("item")
        if isinstance(track, dict):
            tracks.append(track)
    return tracks


def play_track(uri: str) -> None:
    _call(get_client().start_playback, uris=[uri])
