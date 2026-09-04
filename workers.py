# Running slow work without freezing the overlay.
#
# Every panel used to fetch its data inline in build_nav(), which meant the
# overlay stopped responding to the controller for as long as the fetch took —
# fine for reading a volume level, not fine for a Spotify request over a phone
# hotspot. This module is the piece that lets a panel say "show this now, fill
# it in when the answer arrives".
#
# Two separate jobs, deliberately kept apart:
#
#   Worker  runs queued functions on one background thread, in order. Order
#           matters more than it looks: spotipy talks through a single shared
#           requests.Session, and a Session is not safe to use from two
#           threads at once, so every Spotify call in the app goes through
#           spotify_client.submit rather than a thread of its own.
#
#   Commands runs what the user pressed. Every command runs, in order, and
#           reports back. Never route a button press through a Loader: its
#           supersession is designed to discard work, and a press is not
#           work to discard.
#
#   Loader  is the Qt half. It owns one *slot* of work (a panel's library
#           list, its tracklist, the window list) and guarantees two things
#           the UI depends on: the callback runs on the Qt main thread, and a
#           result the UI has already moved past is thrown away instead of
#           overwriting what's on screen now.
#
# The thread -> Qt signal -> main thread hop is the same shape actions/
# album_art.py has used since album art was added; this generalises it rather
# than inventing a second pattern.

import queue
import threading

from PySide6.QtCore import QObject, Signal

import logs

log = logs.get(__name__)


class Worker:
    """One background thread running submitted jobs one at a time, in the
    order they were submitted. Daemon, so it never holds up app exit, and it
    never dies: a job that raises is logged and the next one still runs."""

    def __init__(self, name: str):
        self._name = name
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name=f"{name}-worker", daemon=True
        )
        self._thread.start()

    def submit(self, job) -> None:
        self._jobs.put(job)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                # A job is expected to handle its own failures; reaching here
                # means a bug in the plumbing rather than a failed request,
                # and swallowing it would make the panel wait forever.
                log.exception("A %s job raised", self._name)


# Windows-side lookups (currently the App Switcher's window list). Separate
# from the Spotify thread on purpose: the two have nothing to do with each
# other, and opening the switcher shouldn't wait behind a music request.
SYSTEM = Worker("system")


class Commands(QObject):
    """Runs what the user pressed, in order, dropping nothing.

    The counterpart to Loader, and the distinction matters more than the small
    amount of code here suggests. A Loader answers "what is in this playlist?"
    and is right to throw away an answer the user has already navigated past.
    A command is "play this", "skip to the next track", "like this" — a thing
    the user asked for, which either happens or visibly fails. Running commands
    through a Loader means a second press silently eats the first, which is
    what this app shipped for a day.

    Commands are not cancelled when the overlay closes. Picking a song and
    closing the menu to get back to the game is the normal way to use this app,
    so a queued play must still start. Feedback is the part that has to be
    guarded: the caller decides whether a result is still worth showing, since
    the user may be looking at something else by the time it lands.
    """

    _finished = Signal(int, object, object)  # id, value, error

    def __init__(self, submit, name: str = ""):
        super().__init__()
        self._submit = submit
        self._name = name
        self._next_id = 0
        self._callbacks: dict[int, object] = {}
        self._finished.connect(self._deliver)

    def run(self, work, on_done=None) -> None:
        """Run work() in the background. on_done(value, error), if given, runs
        on the Qt main thread once it has finished, whatever happened."""
        self._next_id += 1
        command_id = self._next_id
        if on_done is not None:
            self._callbacks[command_id] = on_done

        def job():
            try:
                self._finished.emit(command_id, work(), None)
            except Exception as e:
                self._finished.emit(command_id, None, e)

        self._submit(job)

    def _deliver(self, command_id: int, value, error) -> None:
        on_done = self._callbacks.pop(command_id, None)
        if on_done is not None:
            on_done(value, error)


class Loader(QObject):
    """One slot of background work for a panel.

    start() replaces whatever that slot was doing: the previous request's
    result is dropped rather than delivered late over newer content. That is
    the difference between "open playlist A, press Circle, open playlist B"
    working and showing A's songs under B's heading a second later.
    """

    # Carries the result back to the Qt main thread. Emitting a signal from a
    # background thread is the supported way to do this; calling the callback
    # directly there would touch widgets from the wrong thread, which Qt
    # tolerates right up until it crashes.
    _finished = Signal(int, object, object)  # token, value, error

    def __init__(self, submit, name: str = ""):
        """submit(job) puts a callable on some background thread — normally
        spotify_client.submit, so Spotify work stays on the one thread that
        owns the HTTP session."""
        super().__init__()
        self._submit = submit
        self._name = name
        self._token = 0
        self._callbacks: dict[int, object] = {}
        self._finished.connect(self._deliver)

    def start(self, work, on_ready) -> None:
        """Run work() in the background, then on_ready(value, error) on the Qt
        main thread. error is the exception if work() raised, otherwise None,
        so callers keep the try/except shape they had when this was inline."""
        self._token += 1
        token = self._token
        self._callbacks[token] = on_ready

        def job():
            # Superseded while it sat in the queue — don't spend a network
            # round trip on an answer that is already going to be discarded.
            if token != self._token:
                self._finished.emit(token, None, None)
                return
            try:
                self._finished.emit(token, work(), None)
            except Exception as e:
                self._finished.emit(token, None, e)

        self._submit(job)

    def _deliver(self, token: int, value, error) -> None:
        on_ready = self._callbacks.pop(token, None)
        if on_ready is None or token != self._token:
            return
        on_ready(value, error)
