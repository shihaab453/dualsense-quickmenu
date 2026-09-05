# Verification for bounded and cancellable Spotify browser authentication.
#
#   .venv\Scripts\python.exe tests\verify_auth_lifecycle.py
#
# Exits non-zero if anything fails. No browser or network access is used.

import os
import sys
import tempfile
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

_data_dir = tempfile.mkdtemp(prefix="dsqm_auth_")
settings.data_dir = lambda: _data_dir
settings.get_spotify_client_id = lambda: "a" * 32

from actions import spotify_client as sp
from spotipy.cache_handler import CacheHandler


class MemoryCache(CacheHandler):
    def __init__(self):
        self.token = None

    def get_cached_token(self):
        return self.token

    def save_token_to_cache(self, token_info):
        self.token = token_info


class FakeServer:
    def __init__(self, code_after_request=False):
        self.auth_code = None
        self.error = None
        self.state = None
        self.timeout = None
        self.closed = False
        self.code_after_request = code_after_request

    def handle_request(self):
        if self.code_after_request:
            self.auth_code = "authorization-code"

    def server_close(self):
        self.closed = True


def auth_for(attempt, timeout):
    auth = sp._CancellableSpotifyPKCE(
        client_id="a" * 32,
        redirect_uri=sp.REDIRECT_URI,
        scope=sp.SCOPE,
        open_browser=True,
        cache_handler=MemoryCache(),
        requests_timeout=sp._TOKEN_TIMEOUT_SECONDS,
        login_attempt=attempt,
        login_timeout=timeout,
    )
    auth._open_auth_url = lambda: None
    return auth


print("\n[the loopback callback wait has a deadline]")
original_server_factory = sp.start_local_http_server
server = FakeServer()
sp.start_local_http_server = lambda _port: server
try:
    auth_for(sp.LoginAttempt(), 0)._get_auth_response_local_server(8888)
    timed_out = False
except sp.LoginTimedOut:
    timed_out = True
check("an abandoned browser login times out", timed_out)
check("the loopback server closes after timeout", server.closed)


print("\n[the loopback callback wait can be cancelled]")
server = FakeServer()
sp.start_local_http_server = lambda _port: server
attempt = sp.LoginAttempt()
attempt.cancel()
try:
    auth_for(attempt, 10)._get_auth_response_local_server(8888)
    cancelled = False
except sp.LoginCancelled:
    cancelled = True
check("a cancelled browser login stops waiting", cancelled)
check("the loopback server closes after cancellation", server.closed)


print("\n[a successful callback still passes through]")
server = FakeServer(code_after_request=True)
sp.start_local_http_server = lambda _port: server
code = auth_for(sp.LoginAttempt(), 10)._get_auth_response_local_server(8888)
check("the authorization code is returned", code == "authorization-code")
check("the loopback server closes after success", server.closed)
sp.start_local_http_server = original_server_factory


print("\n[tokens are staged until success]")
cache_path = os.path.join(_data_dir, "staged-token.json")
staged = sp._StagedCacheHandler(cache_path, sp.session_generation())
staged.save_token_to_cache({"access_token": "test-token"})
check("staging does not write credentials", not os.path.exists(cache_path))
staged.commit()
check("a successful commit writes credentials", os.path.exists(cache_path))


class FakeLoginAuth:
    def __init__(self, get_access_token):
        self.get_access_token = get_access_token
        self.commits = 0
        self.cache_handler = self

    def commit(self):
        self.commits += 1


def run_login(fake_auth):
    completed = threading.Event()
    result = []
    sp._auth_manager = lambda **_kwargs: fake_auth
    attempt = sp.login_async(
        lambda ok, error: (result.append((ok, error)), completed.set()), timeout=1
    )
    return attempt, completed, result


print("\n[successful async login commits once]")
fake_auth = FakeLoginAuth(lambda: "token")
attempt, completed, result = run_login(fake_auth)
check("the completion callback runs", completed.wait(1))
check("success is reported", result == [(True, None)], f"(got {result})")
check("the staged token is committed once", fake_auth.commits == 1)
check("the successful attempt is finished", attempt.done)


print("\n[cancelling async login prevents token persistence]")
started = threading.Event()
release = threading.Event()


def blocked_login():
    started.set()
    release.wait(1)
    return "token"


fake_auth = FakeLoginAuth(blocked_login)
attempt, completed, result = run_login(fake_auth)
check("the login worker started", started.wait(1))
check("the active attempt accepts cancellation", attempt.cancel())
release.set()
check("the cancelled attempt completes", completed.wait(1))
check(
    "cancellation is reported",
    len(result) == 1 and not result[0][0] and "cancelled" in result[0][1].lower(),
    f"(got {result})",
)
check("a cancelled token is never committed", fake_auth.commits == 0)
check("the cancelled attempt is finished", attempt.done)


print("\n[timeout is reported as a retryable result]")


def timeout_login():
    raise sp.LoginTimedOut()


fake_auth = FakeLoginAuth(timeout_login)
attempt, completed, result = run_login(fake_auth)
check("the timed-out attempt completes", completed.wait(1))
check(
    "timeout is reported clearly",
    len(result) == 1 and not result[0][0] and "timed out" in result[0][1].lower(),
    f"(got {result})",
)
check("a timed-out token is never committed", fake_auth.commits == 0)
check("the timed-out attempt is finished", attempt.done)


print("\n[logout invalidates token work already in flight]")
settings.set_spotify_client_id("a" * 32)
session_cache_path = os.path.join(_data_dir, "session-token.json")
generation = sp.session_generation()
session_cache = sp._SessionCacheHandler(session_cache_path, generation)
session_cache.save_token_to_cache({"access_token": "before-logout"})
check("sanity: the session token was written", os.path.exists(session_cache_path))
original_cache_path = sp._cache_path
sp._cache_path = lambda: session_cache_path
check("logout removes the current token", sp.forget_login())
try:
    session_cache.save_token_to_cache({"access_token": "restored-after-logout"})
    stale_write_rejected = False
except sp.SessionEnded:
    stale_write_rejected = True
check("an old refresh cannot restore its token", stale_write_rejected)
check("the token stays deleted", not os.path.exists(session_cache_path))

old_generation = generation
sp._job_session.generation = old_generation
try:
    sp._call(lambda: (_ for _ in ()).throw(AssertionError("network ran")))
    stale_call_rejected = False
except sp.SessionEnded:
    stale_call_rejected = True
finally:
    del sp._job_session.generation
check("old queued work is rejected before its network call", stale_call_rejected)
sp._cache_path = original_cache_path

finish()
