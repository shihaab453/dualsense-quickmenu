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
#   threading.excepthook   — uncaught on a background thread, and this app has
#                            three: controller polling, album art fetching,
#                            and the Spotify login flow
#   qInstallMessageHandler — Qt's own diagnostics (missing plugins, bad
#                            stylesheets), which are emitted from Qt's C++ side
#                            and never become Python exceptions at all
#
# The Qt one matters more than it sounds: a missing QtSvg plugin in a packaged
# build makes every icon in this app render blank, and Qt reports that only
# through this channel.

import logging
import logging.handlers
import os
import sys
import threading

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

    get(__name__).info("=== app start (pid %s) ===", os.getpid())


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
