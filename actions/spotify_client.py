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
#
# Every other call here is a blocking network request too, and the UI must
# not sit on the Qt main thread waiting for one. submit() is how they get off
# it. It runs jobs on a single background thread rather than one thread per
# call, and that is a correctness requirement, not tidiness: spotipy holds one
# shared requests.Session, and a Session is not safe to use from two threads
# at once. Anything that reaches this module's functions from the UI should go
# through submit() (workers.Loader wraps it with staleness handling), so the
# rule stays "one session, one thread".

import os
import queue
import threading

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyPKCE

import logs
import settings

log = logs.get(__name__)

# Deliberately built here rather than imported from workers.py: that module
# imports Qt, and this one stays Qt-free so the tests can exercise it headless.
_jobs: "queue.Queue" = queue.Queue()


def _run_jobs() -> None:
    while True:
        job = _jobs.get()
        try:
            job()
        except Exception:
            # Callers are expected to handle their own failures, so reaching
            # here is a plumbing bug. Logged and swallowed, because letting
            # this thread die would silently strand every later request.
            log.exception("A Spotify job raised")


_worker = threading.Thread(target=_run_jobs, name="spotify-worker", daemon=True)
_worker.start()


def submit(job) -> None:
    """Run job() on the one thread that owns the Spotify HTTP session. Jobs
    run in submission order; see the note at the top of this file for why
    they are not run in parallel."""
    _jobs.put(job)


# The client ID is *not* baked in, deliberately. A Spotify app registered on
# the developer dashboard starts in "development mode", which only works for
# up to 25 users that the app's owner adds by hand, by email address, in the
# dashboard. Shipping one ID would mean every stranger who installed this got
# refused by Spotify's own login page before any of our code ran.
#
# So each user supplies their own client ID (Settings window -> Spotify), from
# a free app they create on the dashboard themselves. A dev-mode app always
# works for the account that created it, so being the only user of your own
# app means the 25-user limit never applies to anyone.
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

_client = None  # cached spotipy.Spotify instance, built lazily once logged in


def _cache_path() -> str:
    return os.path.join(settings.data_dir(), "spotify_token.json")


class NotConfigured(Exception):
    """No Spotify client ID has been set up yet — the user needs to visit the
    Settings window first. Distinct from "configured but not logged in"."""


class PlaybackUnavailable(Exception):
    """Spotify couldn't service a playback request right now.

    reason is one of: "no_device" (nothing is actively playing Spotify
    anywhere), "premium_required" (free accounts can't control playback),
    "other" (anything else — network hiccup, etc).
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def is_configured() -> bool:
    """True once the user has entered a Spotify client ID. Nothing else in
    this module can work until this is True."""
    return bool(settings.get_spotify_client_id())


def _auth_manager() -> SpotifyPKCE:
    client_id = settings.get_spotify_client_id()
    if not client_id:
        raise NotConfigured("No Spotify client ID has been set up yet.")
    os.makedirs(settings.data_dir(), exist_ok=True)
    return SpotifyPKCE(
        client_id=client_id,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=_cache_path(),
        open_browser=True,
        # spotipy defaults this to None, meaning a token request can wait
        # forever. That is not hypothetical here: refreshing an expired token
        # happens inside is_logged_in(), which the overlay calls on the path
        # that opens a panel, and a stalled request would either freeze the UI
        # or occupy the single Spotify worker indefinitely. The ordinary API
        # client has its own five-second default; this is the auth half.
        requests_timeout=_TOKEN_TIMEOUT_SECONDS,
    )


# Long enough not to trip on a slow connection, short enough that a hung
# endpoint doesn't look like a hung app.
_TOKEN_TIMEOUT_SECONDS = 10


def has_cached_token() -> bool:
    """Whether there is a cached, unexpired token, judged from disk alone.

    Use this to *describe* login state (the Settings window, the diagnostics
    report). It never touches the network, which is the point: is_logged_in()
    calls spotipy's validate_token(), and that refreshes an expired token, so
    asking "are we logged in?" on the GUI thread could sit on a network round
    trip. Both of those callers used to do exactly that.

    It is not a substitute for is_logged_in() before actually calling the API,
    because a token can be revoked server-side while still looking valid here.
    """
    if not is_configured():
        return False
    try:
        auth = _auth_manager()
        token = auth.cache_handler.get_cached_token()
        return bool(token) and not auth.is_token_expired(token)
    except Exception:
        log.exception("Couldn't read the cached Spotify token")
        return False


def is_logged_in() -> bool:
    """True if we already have a valid (or refreshable) cached token —
    checking this never opens a browser, and is False rather than an error
    when no client ID is configured yet."""
    if not is_configured():
        return False
    auth = _auth_manager()
    return auth.validate_token(auth.cache_handler.get_cached_token()) is not None


def links_for(item: dict) -> tuple:
    """(app_uri, web_url) for a track, playlist or album — either may be None.

    Spotify's design guidelines require that displayed metadata always links
    back to the Spotify service, and that users are sent to the Spotify
    application when it's available. Both forms are returned so a caller can
    try the `spotify:` URI first (which opens the desktop app directly) and fall
    back to the open.spotify.com URL, which works for anyone without the app
    installed — browsing works fine on a free account with no desktop client,
    so that fallback isn't hypothetical."""
    if not isinstance(item, dict):
        return None, None
    app_uri = item.get("uri") or None
    web_url = (item.get("external_urls") or {}).get("spotify") or None
    return app_uri, web_url


def forget_login() -> bool:
    """Drops the cached OAuth token, the built client, and the account-bound
    caches. Called when the client ID changes and when the user logs out: a
    token issued by one Spotify app is meaningless to a different one, so
    keeping it would produce a confusing "logged in, but every call fails"
    state instead of a clean prompt to log in again.

    Returns whether the token file is actually gone. The caller should say so
    if it isn't, rather than reporting a logout that left credentials on disk.

    Known gap: this does not cancel work already in flight, so a token refresh
    that was already running can write the cache again afterwards. That needs
    a session generation shared by the auth and job layers - see the open
    logout item."""
    global _client
    _client = None
    _playlist_name_cache.clear()
    # Deferred import: album_art doesn't need this module, and importing it at
    # module level would make that a cycle.
    from actions import album_art

    album_art.forget_all()
    try:
        os.remove(_cache_path())
    except FileNotFoundError:
        pass  # no token cached — nothing to forget
    except OSError:
        log.exception("Couldn't delete the cached Spotify token")
        return False
    return True


def login_async(on_done) -> None:
    """Logs in on a background thread (opens the browser if there's no
    cached session) and calls on_done(success, error_message) when it's
    done. on_done fires on the background thread — callers must hop back
    to the Qt thread themselves (see MusicPanel's _LoginSignal).

    A thread of its own rather than submit(): this one blocks until the user
    finishes clicking around in their browser, which can be a minute or
    never, and parking the shared worker on that would freeze every other
    Spotify request behind it. Nothing else is talking to the API while the
    user is logged out, so the one-session-one-thread rule still holds."""

    def worker():
        try:
            _auth_manager().get_access_token()
            on_done(True, None)
        except Exception as e:
            # The message reaches the Music panel, but the traceback only ever
            # existed here — and a mismatched redirect URI is the single most
            # likely first-run failure, so it needs to be in the log.
            log.exception("Spotify login failed")
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


# playlist id -> name. Playlist names rarely change, and resolving one costs
# a real API call the currently-playing endpoint doesn't give for free — see
# resolve_context_name for why — so a lookup is cached rather than repeated.
_playlist_name_cache: dict[str, str] = {}


def resolve_context_name(playback: dict) -> str | None:
    """A human-readable name for what's currently playing *from* — an album
    title, a playlist's own name — or None when there's nothing sensible to
    show. Never raises; a lookup failure just means no name.

    Spotify's current-playback response only ever gives a `context` object
    with a type and a URI, never a display name — checked against a live
    account rather than assumed, per HANDOFF.md gotcha #7, since the docs have
    been wrong about response shape twice before. For an album, the currently-
    playing track's own embedded `album` field already carries the name for
    free. For a playlist there's no such shortcut; it takes a real API call.

    Returns None — not a guess — when context is absent, which covers Liked
    Songs, a single queued track, and Spotify radio identically; there's no
    way to tell those apart from the API alone. Also None for context types
    this doesn't specifically handle (a podcast episode's show, etc.)."""
    context = playback.get("context") or {}
    context_type = context.get("type")
    uri = context.get("uri") or ""

    if context_type == "album":
        item = playback.get("item") or {}
        return (item.get("album") or {}).get("name")

    if context_type == "playlist":
        playlist_id = uri.rsplit(":", 1)[-1]
        if playlist_id in _playlist_name_cache:
            return _playlist_name_cache[playlist_id]
        try:
            result = get_client().playlist(playlist_id, fields="name")
        except Exception:
            log.exception("Couldn't resolve playlist name for %r", playlist_id)
            return None
        name = result.get("name")
        if name:
            _playlist_name_cache[playlist_id] = name
        return name

    return None


def get_now_playing_summary() -> dict | None:
    """Everything the home screen's Now Playing card needs, in one call:
    title, artists, album art URL, playing state, and (best-effort) what it's
    playing from. None if nothing is playing or the lookup fails.

    Synchronous — see get_now_playing_summary_async for the version that
    doesn't block the Qt main thread, which is what UI code should actually
    call. This one exists as the plain, easily-testable core."""
    try:
        playback = get_current_playback()
    except Exception:
        log.exception("Couldn't read current playback for the Now Playing card")
        return None
    if not playback or not playback.get("item"):
        return None
    track = playback["item"]
    # Deferred import: album_art doesn't need spotify_client, but importing it
    # at module level here would be an easy accidental cycle to introduce later
    # if that ever changes, for a helper only used in this one function.
    from actions import album_art

    return {
        "track": track,
        "title": track.get("name") or "(unknown title)",
        "artists": ", ".join(a["name"] for a in track.get("artists", [])),
        "art_url": album_art.largest_image_url(track),
        "is_playing": bool(playback.get("is_playing")),
        "source_name": resolve_context_name(playback),
    }


def get_now_playing_summary_async(on_done) -> None:
    """Runs get_now_playing_summary() on a background thread and calls
    on_done(summary_or_None) when it's ready.

    Why this needs to exist rather than the panels' usual "just call it
    synchronously in build_nav()" pattern: unlike a panel (fetched only once
    it's actually opened), the home card's data has to be ready every time the
    overlay itself opens — the PS button — and that path has to stay
    instant. A blocking network call there would put Spotify's response time
    on the critical path for every single controller press, panel or no
    panel. on_done fires on the background thread; the caller must hop back to
    the Qt thread itself (see MusicPanel's _LoginSignal for the same pattern
    applied to login)."""

    def job():
        # The logged-in check belongs on this side of the thread hop too:
        # validating a cached token can refresh it over the network, and the
        # caller here is the menu-open path, where nothing may block.
        #
        # on_done must run whatever happens. It used to be able to raise out
        # of is_logged_in(), the worker swallowed the exception, and the
        # caller's "a refresh is in flight" flag stayed set for the life of
        # the process - so the home card silently never updated again.
        try:
            summary = get_now_playing_summary() if is_logged_in() else None
        except Exception:
            log.exception("Couldn't work out what's playing")
            summary = None
        on_done(summary)

    submit(job)


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


def get_liked_songs_page(limit: int = 20, offset: int = 0) -> tuple[list, int, int]:
    """One page of Liked Songs: (tracks, total, consumed).

    `consumed` is how many entries Spotify actually returned, which is not the
    same as how many are displayable - a removed or unavailable track comes
    back as an entry with no track in it. The caller must advance its offset by
    `consumed`, not by `len(tracks)`, or the next request overlaps this one and
    the same songs appear twice. See _paged_entries."""
    result = _call(get_client().current_user_saved_tracks, limit=limit, offset=offset)
    entries = result.get("items", [])
    tracks = []
    for item in entries:
        track = item.get("track") if isinstance(item, dict) else None
        if isinstance(track, dict):
            tracks.append(track)
    return tracks, int(result.get("total", offset + len(entries))), len(entries)


def get_liked_songs_total() -> int:
    # limit=1 so this is a cheap "just the count" call, not a full fetch.
    result = _call(get_client().current_user_saved_tracks, limit=1)
    return result.get("total", 0)


def get_playlists_page(limit: int = 20, offset: int = 0) -> tuple[list, int, int]:
    """One page of the user's playlists: (playlists, total, consumed). See
    get_liked_songs_page for why `consumed` is reported separately."""
    result = _call(get_client().current_user_playlists, limit=limit, offset=offset)
    entries = result.get("items", [])
    playlists = [item for item in entries if isinstance(item, dict)]
    return playlists, int(result.get("total", offset + len(entries))), len(entries)


def get_playlist_tracks_page(
    playlist_id: str, limit: int = 20, offset: int = 0
) -> tuple[list, int, int]:
    """One page of a playlist's tracks: (tracks, total, consumed). See
    get_liked_songs_page for why `consumed` is reported separately."""
    result = _call(get_client().playlist_items, playlist_id, limit=limit, offset=offset)
    # Docs say each item has the track data under "track", but the real
    # response for this endpoint nests it under "item" instead, with
    # "track" left as a true/false type-discriminator (track vs. episode) —
    # same class of doc-vs-reality mismatch as the playlist track-count
    # field ("tracks" vs "items") found earlier. Checking isinstance(...,
    # dict) rather than just truthiness catches that discriminator so it
    # doesn't get mistaken for real track data.
    entries = result.get("items", [])
    tracks = []
    for entry in entries:
        track = entry.get("track")
        if not isinstance(track, dict):
            track = entry.get("item")
        if isinstance(track, dict):
            tracks.append(track)
    return tracks, int(result.get("total", offset + len(entries))), len(entries)


def play_track(uri: str) -> None:
    _call(get_client().start_playback, uris=[uri])
