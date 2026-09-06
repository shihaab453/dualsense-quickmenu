# Verification that the Windows media-session reading is Spotify-only.
#
#   .venv\Scripts\python.exe tests\verify_media_session.py
#
# Exits non-zero if anything fails.
#
# Why this matters more than it looks. Windows tracks one system-wide "current
# media session", owned by whatever played most recently - a browser, a game
# launcher, another music app. actions/now_playing.get() used to return that
# session's track unconditionally, and panels/nowplaying.py displayed it
# whenever Spotify's own Web API lookup came back empty. So the panel could
# show a YouTube video's title, and did.
#
# Spotify's Developer Policy III prohibits "products integrating streams from
# other services", which made that the one place in this app where a
# non-Spotify stream reached the screen. The fix is a source check on the
# session's AppUserModelID. This suite is what stops it regressing, because
# nothing else would notice: the old behaviour looks *better* day to day (the
# panel is populated more often), so a reviewer could reasonably "fix" the
# filter away without knowing why it exists.
#
# No Qt and no real media session: the WinRT manager is faked, so this runs
# anywhere and does not depend on what happens to be playing on the machine.

import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

from actions import now_playing


# The two identifiers Spotify actually uses, both read from a live session on
# 2026-09-06 rather than guessed. If Spotify ever changes them, this suite is
# where that shows up as a failure - which is the point of writing the real
# strings down rather than testing against a placeholder.
STORE_BUILD = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
DESKTOP_BUILD = "Spotify.exe"


print("\n[Spotify's own identifiers are accepted]")
for app_id, label in [
    (STORE_BUILD, "Microsoft Store build"),
    (DESKTOP_BUILD, "Win32 desktop build"),
    (DESKTOP_BUILD.lower(), "lowercased"),
    (DESKTOP_BUILD.upper(), "uppercased"),
    (f"  {DESKTOP_BUILD}  ", "surrounded by whitespace"),
    ("SpotifyAB.SpotifyMusic_someotherhash!Spotify", "a different package hash"),
]:
    check(f"accepted: {label}", now_playing._is_spotify(app_id), f"({app_id!r})")


print("\n[everything else is rejected]")
# The impostors are the reason the match is anchored rather than a substring
# test. `"spotify" in app_id` would accept the first three of these.
for app_id, label in [
    ("NotSpotify.exe", "an executable that merely ends with the name"),
    ("MySpotifyClone.exe", "an executable that contains the name"),
    ("EvilSpotifyAB.SpotifyMusic_x!Spotify", "a package that contains the prefix"),
    ("msedge.exe", "Edge"),
    ("chrome.exe", "Chrome"),
    ("308046B0AF4A39CB", "Firefox, which uses an opaque id"),
    ("vlc.exe", "another media player"),
    ("", "an empty id"),
    (None, "no id at all"),
]:
    check(f"rejected: {label}", not now_playing._is_spotify(app_id), f"({app_id!r})")


# ---- the fetch itself, against a faked WinRT manager ----

class FakeProperties:
    def __init__(self, title, artist):
        self.title = title
        self.artist = artist


class FakeSession:
    def __init__(self, app_id, title="A Song", artist="An Artist"):
        self.source_app_user_model_id = app_id
        self._props = FakeProperties(title, artist)
        self.properties_read = False

    async def try_get_media_properties_async(self):
        self.properties_read = True
        return self._props


class FakeManager:
    """Stands in for GlobalSystemMediaTransportControlsSessionManager."""

    def __init__(self, session):
        self._session = session

    @classmethod
    def for_session(cls, session):
        manager = cls(session)

        class Factory:
            @staticmethod
            async def request_async():
                return manager

        return Factory

    def get_current_session(self):
        return self._session


def fetch_with(session):
    """Run the real _fetch() against a faked manager."""
    real_manager, real_available = now_playing._MediaManager, now_playing._AVAILABLE
    now_playing._MediaManager = FakeManager.for_session(session)
    now_playing._AVAILABLE = True
    try:
        return now_playing.get()
    finally:
        now_playing._MediaManager = real_manager
        now_playing._AVAILABLE = real_available


print("\n[a Spotify session is read]")
session = FakeSession(STORE_BUILD, "Real Track", "Real Artist")
result = fetch_with(session)
check("the track comes back", result == {"title": "Real Track", "artist": "Real Artist"},
      f"(got {result!r})")

print("\n[a non-Spotify session is not read at all]")
# Stronger than "returns None": the properties must never be *requested*. A
# version that fetched the track and then discarded it would pass a
# return-value check while still reaching for another service's stream.
browser = FakeSession("msedge.exe", "Some Video", "Some Channel")
result = fetch_with(browser)
check("nothing is returned", result is None, f"(got {result!r})")
check("and the track was never even asked for", not browser.properties_read,
      f"(properties_read={browser.properties_read})")

print("\n[no session at all is still None]")
check("an absent session returns None", fetch_with(None) is None)

print("\n[an empty title is preserved rather than invented]")
blank = FakeSession(DESKTOP_BUILD, None, None)
result = fetch_with(blank)
check("missing metadata becomes empty strings, not None",
      result == {"title": "", "artist": ""}, f"(got {result!r})")

print("\n[winrt missing is handled, not crashed]")
real_available = now_playing._AVAILABLE
now_playing._AVAILABLE = False
try:
    check("get() returns None when winrt is unavailable", now_playing.get() is None)
finally:
    now_playing._AVAILABLE = real_available

print("\n[a failing lookup is swallowed, not raised]")
# panels/nowplaying.py has a callback contract to keep: a job that raises
# leaves the panel's in-flight flag set forever. get() must absorb it.
class ExplodingFactory:
    @staticmethod
    async def request_async():
        raise OSError("the media session service is unavailable")


real_manager = now_playing._MediaManager
now_playing._MediaManager = ExplodingFactory
now_playing._AVAILABLE = True
try:
    raised = False
    try:
        outcome = now_playing.get()
    except Exception:
        raised = True
        outcome = "<raised>"
    check("a WinRT failure returns None instead of propagating",
          not raised and outcome is None, f"(got {outcome!r})")
finally:
    now_playing._MediaManager = real_manager
    now_playing._AVAILABLE = real_available

finish()
