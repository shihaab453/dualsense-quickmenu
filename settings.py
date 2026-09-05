# Persistent user settings, kept in %APPDATA%\DualSenseQuickMenu\ rather than
# next to the code. Both reasons are about shipping this to other people: an
# installed copy (Program Files, or a PyInstaller bundle that unpacks to a
# temp dir) often can't write to its own directory, and an app update would
# overwrite anything stored there. This is the same folder spotify_client.py
# already caches its OAuth token in.
#
# Every read here tolerates a missing or corrupt file — a fresh install has no
# settings at all, and that's the normal first-run state, not an error.
#
# Every *write* tolerates failure too, which is a different problem. If this
# folder cannot be created or written to (permissions that deny the user, a
# full disk, a redirected AppData pointing somewhere that no longer exists),
# the app still has to run: it is a tray overlay, not a document editor, and
# there is nothing here worth refusing to start over. So a failed write is
# recorded in storage_error() for the interface to report, the value is kept
# in memory for the rest of this session, and save() returns False instead of
# raising. Anything that tells the user "Saved" has to check that return, or
# it promises something that did not happen.

import json
import os

import logs

log = logs.get(__name__)

_DIR_NAME = "DualSenseQuickMenu"
_FILE_NAME = "settings.json"
# Written and deleted by check_writable(). Named with a leading dot so it does
# not look like something the user is meant to open if it is ever left behind.
_PROBE_NAME = ".write-check"

_DEFAULTS = {
    "spotify_client_id": "",
    # "auto" uses the normal Ctrl+Alt+P shortcut, then its documented
    # fallback if another application has claimed it.
    "hotkey_shortcut": "auto",
    # False until the app has completed one startup. Drives the first-run
    # experience: the app has no window of its own, so without something
    # happening on first launch, double-clicking it looks like nothing
    # installed.
    "has_launched": False,
}

# Why the last write to data_dir() failed, or "" if the last one worked. Empty
# until something has actually tried, so it is a record of what happened rather
# than a prediction; call check_writable() to find out before anything needs
# saving.
_storage_error = ""

# Values a failed write is holding. load() lays these over whatever is on disk,
# so a session on an unwritable AppData is still coherent: the client ID you
# just typed is the one the app uses for as long as it stays open. It just is
# not there next launch.
_unsaved = {}


def data_dir() -> str:
    """The per-user folder holding settings.json and the Spotify token."""
    # APPDATA is set on every normal Windows login, but fall back rather than
    # raising KeyError at import time if something exotic has unset it.
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, _DIR_NAME)


def settings_path() -> str:
    return os.path.join(data_dir(), _FILE_NAME)


def storage_error() -> str:
    """Why settings cannot be saved right now, or "" if they can be.

    Phrased for a person to read, because it is shown verbatim in the tray
    notification at startup and in the Settings window's banner.
    """
    return _storage_error


def _record_storage_failure(exc: OSError) -> None:
    global _storage_error
    # strerror is the readable half ("Access is denied"); str(exc) alone leads
    # with an errno that means nothing to the person reading it.
    reason = exc.strerror or str(exc)
    _storage_error = f"Windows could not write to {data_dir()} ({reason})."
    # A warning carrying the error, not log.exception: this is a condition the
    # app handles and then reports to the user, and check_writable() runs every
    # time the Settings window is opened. A stack trace on each of those would
    # bury the failures that really are unexpected.
    log.warning("Couldn't write to %s: %s", data_dir(), exc)


def check_writable() -> str:
    """Create the data folder and write a throwaway file into it.

    Returns storage_error(): "" if that worked. This exists so startup can warn
    someone before they change a setting and watch it not stick, rather than
    their finding out at the first save.
    """
    global _storage_error
    probe = os.path.join(data_dir(), _PROBE_NAME)
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
    except OSError as exc:
        _record_storage_failure(exc)
        return _storage_error
    # A probe that cannot be cleaned up afterwards is not a storage failure:
    # the write itself is what was being tested, and it worked.
    try:
        os.remove(probe)
    except OSError:
        pass
    _storage_error = ""
    return ""


def _read_json(path: str):
    """Parsed JSON, or None if the file is missing/unreadable/not valid JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None  # normal on a fresh install — not worth logging
    except (OSError, ValueError):
        # Corrupt or unreadable: the caller silently falls back to defaults,
        # which from the user's side looks like their settings vanished.
        log.exception("Couldn't read %s — falling back to defaults", path)
        return None


def load() -> dict:
    """Every setting, with defaults filled in for anything not stored yet.

    Unrecognised keys in an existing settings.json are kept as-is rather than
    pruned — a settings file written by an older version may hold entries for
    features since removed (`pinned_games`, from the Task Switcher), and they're
    inert."""
    values = dict(_DEFAULTS)
    stored = _read_json(settings_path())
    if isinstance(stored, dict):
        values.update(stored)
    # Anything a failed write is still holding wins over the file: the user
    # asked for it during this session, and what is on disk is what it was
    # before they did.
    values.update(_unsaved)
    return values


def save(values: dict) -> bool:
    """Writes via a temp file + os.replace, so an interrupted write can never
    leave a half-written settings.json behind for the next launch to choke on.

    Returns whether it reached disk. False is not fatal and is not an exception:
    the values are kept in memory instead (see _unsaved), so the app carries on
    with the settings the user asked for and forgets them at exit.
    """
    global _storage_error
    path = settings_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        _unsaved.update(values)
        _record_storage_failure(exc)
        # Best effort: a tmp file that was written but could not be renamed
        # would otherwise sit there forever. If this fails too, there is
        # nothing further to be done about it.
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    _unsaved.clear()
    _storage_error = ""
    return True


def _update(key: str, value) -> bool:
    values = load()
    values[key] = value
    return save(values)


def get_spotify_client_id() -> str:
    return str(load().get("spotify_client_id") or "").strip()


def set_spotify_client_id(client_id: str) -> bool:
    """Returns whether it was written to disk. It takes effect either way."""
    return _update("spotify_client_id", (client_id or "").strip())


def get_hotkey_shortcut() -> str:
    return str(load().get("hotkey_shortcut") or "auto")


def set_hotkey_shortcut(shortcut: str) -> bool:
    """Returns whether it was written to disk. It takes effect either way."""
    return _update("hotkey_shortcut", shortcut if shortcut else "auto")


def is_first_run() -> bool:
    return not load().get("has_launched", False)


def mark_launched() -> bool:
    """Records that the first-run experience has been shown.

    Returns whether that reached disk. If it did not, this session still counts
    as launched (so nothing re-triggers the welcome while the app is open) and
    the next launch is treated as a first one again, which is the right way
    round: better to greet someone twice than to leave them staring at a tray
    icon nobody told them about.
    """
    return _update("has_launched", True)
