# Windows already tracks "what's playing" system-wide — it powers the little
# popup in the corner when you press a volume key. The winrt packages expose
# that tracker to Python. WinRT calls are asynchronous by design, hence the
# small asyncio wrapper.
#
# Deliberately optional: if winrt is missing or errors, get() returns None and
# the menu simply shows "Nothing playing" instead of crashing.
#
# **This reads Spotify's session only, and ignores every other player.** That
# is a policy requirement, not a preference. Spotify's Developer Policy III
# prohibits "products integrating streams from other services", and this was
# the one place in the app where a non-Spotify stream could reach the screen:
# the system-wide session is owned by whatever played last, which on a normal
# desktop is often a browser. The Now Playing panel showed it whenever
# Spotify's own Web API lookup came back empty.
#
# Filtering here rather than in the panel is deliberate. It means there is one
# place that decides what counts as Spotify, and no later caller can reach the
# unfiltered reading by accident.
#
# Why keep this path at all, rather than deleting it and showing only what the
# Web API reports: it is what still works when Spotify is playing but the API
# cannot answer — not logged in, an expired token, no network. In those cases
# the media session still reports the real Spotify track, and it is genuinely
# Spotify content, so the panel keeps working without misattributing anything.

import asyncio

import logs

log = logs.get(__name__)

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _MediaManager,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


# How Spotify identifies itself as the owner of a media session, by
# AppUserModelID. Both were read from a live session rather than guessed:
#
#   Microsoft Store build   SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify
#   Win32 desktop build     Spotify.exe
#
# The Store identifier is a Package Family Name plus an app id. The hash in
# the middle is derived from the publisher, so it is the same for every
# install of that package — but only the `SpotifyAB.SpotifyMusic_` prefix is
# matched, so a repackage that changed it would still be recognised.
_SPOTIFY_EXECUTABLE = "spotify.exe"
_SPOTIFY_PACKAGE_PREFIX = "spotifyab.spotifymusic_"


def _is_spotify(app_id: str | None) -> bool:
    """Whether a media session's AppUserModelID belongs to Spotify.

    Anchored on purpose — an exact match for the executable and a prefix for
    the package. A substring test ("spotify" in app_id) would accept
    `NotSpotify.exe` and anything else that merely contains the word, which is
    the opposite of the guarantee this function exists to make."""
    if not app_id:
        return False
    app_id = app_id.strip().lower()
    return app_id == _SPOTIFY_EXECUTABLE or app_id.startswith(_SPOTIFY_PACKAGE_PREFIX)


async def _fetch():
    manager = await _MediaManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None
    # A session owned by anything else is treated exactly as no session at
    # all. Note the consequence, which is deliberate: if a browser owns the
    # *current* session while Spotify plays behind it, this returns None and
    # the panel says "Nothing playing" rather than naming the browser's track.
    # Reading every session instead of the current one would fix that, but it
    # needs winrt.windows.foundation.collections, which this project does not
    # depend on. Showing nothing is the right failure here: showing another
    # service's stream is the thing that is not allowed.
    if not _is_spotify(session.source_app_user_model_id):
        return None
    props = await session.try_get_media_properties_async()
    return {"title": props.title or "", "artist": props.artist or ""}


def get() -> dict | None:
    """Current track as {'title': ..., 'artist': ...}, or None.

    None when nothing is playing, when winrt is unavailable, *and* when the
    thing playing is not Spotify — see the module comment."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_fetch())
    except Exception:
        log.warning("Windows media session lookup failed", exc_info=True)
        return None
