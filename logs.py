# Error and crash logging to %APPDATA%\DualSenseQuickMenu\log.txt.
#
# Why this exists: this app is meant to run under pythonw.exe, so no console
# window sits behind your game — but pythonw has no stdout or stderr, and
# anything written there is discarded. Combined with the deliberate
# `except Exception:` blocks that stop one failing panel from taking down the
# whole overlay, that meant every failure on someone else's machine was both
# invisible to them and unreportable to us. Nothing here changes how the app
# behaves; it only makes failures leave a trace.
#
# Three hooks are needed, because Python and Qt lose exceptions in three
# different places:
#   sys.excepthook         — uncaught on the main thread, including inside Qt
#                            slots (PySide6 routes those here)
#   threading.excepthook   — uncaught on a background thread, and this app
#                            runs several: controller polling, the global
#                            hotkey listener, the Spotify API worker and the
#                            two workers.Worker instances (SYSTEM, MEDIA) for
#                            the whole process, plus one per login attempt and
#                            one per artwork URL in flight. Deliberately not a
#                            count — the transient ones make it a moving
#                            number, and a stale count in a comment is how
#                            HANDOFF ended up asserting "four" long after it
#                            stopped being true
#   qInstallMessageHandler — Qt's own diagnostics (missing plugins, bad
#                            stylesheets), which are emitted from Qt's C++ side
#                            and never become Python exceptions at all
#
# The Qt one matters more than it sounds: a missing QtSvg plugin in a packaged
# build makes every icon in this app render blank, and Qt reports that only
# through this channel.

import collections
import logging
import logging.handlers
import os
import sys
import threading
import time

_LOG_NAME = "log.txt"
_MAX_BYTES = 512 * 1024
_BACKUP_COUNT = 2

_configured = False
# qInstallMessageHandler doesn't keep the callable alive on the Python side,
# so it's parked here rather than left as a local to be garbage collected.
_qt_handler = None


def _data_dir() -> str:
    # Imported lazily: settings.py logs through this module, so a module-level
    # import here would be circular.
    import settings

    return settings.data_dir()


def log_path() -> str:
    return os.path.join(_data_dir(), _LOG_NAME)


def get(name: str) -> logging.Logger:
    """The logger a module should use: `log = logs.get(__name__)`."""
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Recent warnings and errors, kept in memory as structured records.
#
# Why this exists rather than diagnostics.py re-reading log.txt: by the time a
# record has been formatted into the file, the message *template* (a literal
# written in this repo, so safe by construction) and its *arguments* (runtime
# values, which are where a token or a username would come from) have been
# glued into one string that nothing can tell apart again. Keeping them apart
# is what lets the diagnostics report redact the risky half and keep the
# useful half. diagnostics.py still falls back to parsing the file when this
# buffer is empty, which is what happens after a crash and restart.
#
# Bounded, because this app sits in the tray for days and a log-spamming bug
# must not turn into a memory leak. Only WARNING and worse are kept: that is
# all the report shows.

_RECENT_LIMIT = 40
_recent = collections.deque(maxlen=_RECENT_LIMIT)
_recent_lock = threading.Lock()


def _record_args(record: logging.LogRecord):
    """The record's arguments, in a form that is safe to hold on to.

    Numbers pass through as themselves so that a `%d` placeholder still
    formats; everything else becomes a string right now rather than a live
    reference, so the buffer can't pin a whole Spotify response (or a Qt
    object) in memory for the life of the process."""
    def convert(value):
        if value is None or isinstance(value, (int, float, bool)):
            return value
        try:
            return str(value)
        except Exception:
            return "<unprintable>"

    args = record.args
    if not args:
        return ()
    if isinstance(args, dict):
        return {key: convert(value) for key, value in args.items()}
    if not isinstance(args, tuple):
        args = (args,)
    return tuple(convert(value) for value in args)


class _RecentRecords(logging.Handler):
    """Keeps the last few warnings/errors as fields instead of as text."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            exc_type = None
            if record.exc_info and record.exc_info[0] is not None:
                # The type only. An exception's message routinely carries the
                # path or value that caused it, which is exactly the kind of
                # free-form text this buffer exists to keep out of a report.
                exc_type = record.exc_info[0].__name__
            entry = {
                "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(record.created)),
                "level": record.levelname,
                "source": record.name,
                "template": record.msg if isinstance(record.msg, str) else str(record.msg),
                "args": _record_args(record),
                "exc_type": exc_type,
            }
        except Exception:
            # A logging handler that raises turns one failure into two, and
            # this one is not important enough to be that.
            return
        with _recent_lock:
            _recent.append(entry)


def recent_problems() -> list:
    """The buffered warnings and errors, oldest first. Each is a dict of
    when/level/source/template/args/exc_type; see diagnostics.py for what is
    done with them before a human sees them."""
    with _recent_lock:
        return [dict(entry) for entry in _recent]


def forget_recent() -> None:
    """Drop the buffer. Nothing in the app calls this during normal use; it is
    here so a test can start from a known-empty state, and so there is one
    obvious place to call from if a "clear my data" action ever wants it."""
    with _recent_lock:
        _recent.clear()


def setup() -> None:
    """Call once, as early in startup as possible — before anything that could
    fail. Safe to call again; later calls do nothing."""
    global _configured
    if _configured:
        return
    _configured = True

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Added first and outside the try below: an unwritable log file must not
    # also cost us the in-memory records the diagnostics report is built from.
    recent_handler = _RecentRecords()
    recent_handler.setLevel(logging.WARNING)
    root.addHandler(recent_handler)

    try:
        os.makedirs(_data_dir(), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # Read-only or otherwise unwritable location. Nothing useful to do —
        # and failing to set up logging must never be what stops the app.
        pass

    # Only when there's a console to write to: under pythonw.exe sys.stderr is
    # None, and a StreamHandler pointed at None raises on every single record.
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _install_qt_handler()

    # The version goes in the banner so a log pasted into a bug report always
    # says which build produced it.
    import version

    get(__name__).info(
        "=== %s %s start (pid %s, frozen=%s) ===",
        version.APP_NAME,
        version.VERSION,
        os.getpid(),
        getattr(sys, "frozen", False),
    )


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    get("unhandled").critical(
        "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
    )


def _thread_excepthook(args) -> None:
    thread_name = args.thread.name if args.thread is not None else "unknown"
    get("unhandled").critical(
        "Unhandled exception on background thread %s",
        thread_name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _install_qt_handler() -> None:
    global _qt_handler
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt_log = get("qt")

    def handler(msg_type, context, message):
        qt_log.log(levels.get(msg_type, logging.INFO), "%s", message)

    _qt_handler = handler
    qInstallMessageHandler(handler)
