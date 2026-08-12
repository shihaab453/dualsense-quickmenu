# Builds the text behind Settings -> "Copy diagnostics".
#
# Why this exists: log.txt is only useful if it reaches the person who can read
# it, and the path to that is currently right-click tray -> Settings -> Open log
# folder -> find the file -> attach it somewhere. A tester who is mildly
# annoyed that something didn't work will not do five steps. One button that
# puts a short report on the clipboard makes the ask "click this and paste it",
# which people actually do.
#
# The output is written to be pasted into a chat message, which shapes two
# decisions:
#
#   * It never includes secrets. The Spotify client ID and OAuth token are
#     reported as booleans — "saved" / "logged in" — never as values.
#   * The user's home directory is replaced with %USERPROFILE% in log excerpts,
#     since those lines contain real paths and someone's Windows username is
#     usually their actual name.

import os
import platform
import re
import sys

import hotkey
import logs
import version
from actions import spotify_client as sp

log = logs.get(__name__)

# Matches a log line's header. Traceback continuation lines deliberately don't
# match, so a multi-line exception contributes only its one summary line.
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2} (WARNING|ERROR|CRITICAL)\s+(\S+): (.*)$"
)

_MAX_PROBLEMS = 6
_MAX_LINE_LENGTH = 150

# Set by main.py once the controller listener exists. Left as None when the
# report is generated from somewhere that has no listener (e.g. the Settings
# window opened before startup finished), in which case the controller line
# says so rather than guessing.
_controller_probe = None
_hotkey_probe = None


def register_controller_probe(probe) -> None:
    """probe() should return (connected: bool, battery_percent: int | None)."""
    global _controller_probe
    _controller_probe = probe


def register_hotkey_probe(probe) -> None:
    """probe() should return bool: whether the global hotkey (hotkey.py)
    actually registered with Windows. A silent registration failure — most
    likely another app already holding Ctrl+Alt+Space — would otherwise leave
    someone wondering why the hotkey "does nothing"; this is how that shows
    up in a diagnostics report instead of nowhere."""
    global _hotkey_probe
    _hotkey_probe = probe


def _redact(text: str) -> str:
    """Replaces the user's home directory with %USERPROFILE%. Log lines carry
    real filesystem paths, and a Windows username is usually a person's name —
    this report is meant to be pasted into a group chat."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if not home:
        return text
    return re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)


def _spotify_line() -> str:
    try:
        configured = sp.is_configured()
    except Exception:
        return "unknown (couldn't check)"
    if not configured:
        return "no client ID saved"
    try:
        return "client ID saved, logged in" if sp.is_logged_in() else (
            "client ID saved, not logged in"
        )
    except Exception:
        return "client ID saved, login state unknown"


def _controller_line() -> str:
    if _controller_probe is None:
        return "unknown (app not fully started)"
    try:
        connected, battery = _controller_probe()
    except Exception:
        log.exception("Controller probe failed while building diagnostics")
        return "unknown (probe failed)"
    if not connected:
        return "not connected"
    return f"connected, battery {battery}%" if battery is not None else "connected"


def hotkey_registered() -> bool | None:
    """Whether the global hotkey actually registered with Windows, or None if
    that isn't knowable yet (app still starting up). Public — settings_window
    uses this too, to show live status next to the hotkey it can't itself
    control (that lives in main.py's HotkeyListener)."""
    if _hotkey_probe is None:
        return None
    try:
        return bool(_hotkey_probe())
    except Exception:
        log.exception("Hotkey probe failed")
        return None


def _hotkey_line() -> str:
    registered = hotkey_registered()
    if registered is None:
        return "unknown (app not fully started)"
    return (
        f"active ({hotkey.DISPLAY_NAME} opens/closes the overlay)"
        if registered
        else f"NOT active — {hotkey.DISPLAY_NAME} is likely already bound by another app"
    )


def _recent_problems() -> tuple:
    """(lines, total_count) for the warnings and errors in the log, newest last."""
    try:
        with open(logs.log_path(), "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return [], 0

    matches = []
    for line in text.splitlines():
        match = _LOG_LINE_RE.match(line)
        if not match:
            continue
        date, time_hm, level, source, message = match.groups()
        message = message.strip()
        if len(message) > _MAX_LINE_LENGTH:
            message = message[:_MAX_LINE_LENGTH].rstrip() + "…"
        matches.append(f"{date} {time_hm} {level} {source}: {message}")
    return matches[-_MAX_PROBLEMS:], len(matches)


def report() -> str:
    """The full diagnostics text, ready for the clipboard."""
    try:
        pyside_version = __import__("PySide6").__version__
    except Exception:
        pyside_version = "unknown"

    lines = [
        f"{version.APP_NAME} {version.VERSION}",
        f"Build: {'packaged' if getattr(sys, 'frozen', False) else 'from source'}",
        f"System: {platform.platform()} ({platform.machine()})",
        f"Python {platform.python_version()} / PySide6 {pyside_version}",
        "",
        f"Controller: {_controller_line()}",
        f"Global hotkey: {_hotkey_line()}",
        f"Spotify: {_spotify_line()}",
        f"Log file: {_redact(logs.log_path())}",
    ]

    problems, total = _recent_problems()
    lines.append("")
    if not problems:
        lines.append("No warnings or errors logged.")
    else:
        shown = len(problems)
        header = (
            f"Recent warnings/errors (last {shown} of {total}):"
            if total > shown
            else f"Warnings/errors ({total}):"
        )
        lines.append(header)
        lines.extend("  " + _redact(line) for line in problems)

    return "\n".join(lines)
