# Persistent user settings, kept in %APPDATA%\DualSenseQuickMenu\ rather than
# next to the code. Both reasons are about shipping this to other people: an
# installed copy (Program Files, or a PyInstaller bundle that unpacks to a
# temp dir) often can't write to its own directory, and an app update would
# overwrite anything stored there. This is the same folder spotify_client.py
# already caches its OAuth token in.
#
# Every read here tolerates a missing or corrupt file — a fresh install has no
# settings at all, and that's the normal first-run state, not an error.

import json
import os

import logs

log = logs.get(__name__)

_DIR_NAME = "DualSenseQuickMenu"
_FILE_NAME = "settings.json"

# Where the games list used to live, before it moved to %APPDATA%. Read once,
# on the first run after this change, so an existing install doesn't silently
# lose games the user already configured by hand.
_LEGACY_GAMES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "pinned_games.json"
)

_DEFAULTS = {
    "spotify_client_id": "",
    "pinned_games": [],
}


def data_dir() -> str:
    """The per-user folder holding settings.json and the Spotify token."""
    # APPDATA is set on every normal Windows login, but fall back rather than
    # raising KeyError at import time if something exotic has unset it.
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, _DIR_NAME)


def settings_path() -> str:
    return os.path.join(data_dir(), _FILE_NAME)


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
    """Every setting, with defaults filled in for anything not stored yet."""
    values = dict(_DEFAULTS)
    stored = _read_json(settings_path())
    if isinstance(stored, dict):
        values.update(stored)
        return values

    # No usable settings file yet — adopt the old in-repo games list if there
    # is one, then persist it so this migration only ever runs once.
    legacy = _read_json(_LEGACY_GAMES_PATH)
    if isinstance(legacy, list) and legacy:
        values["pinned_games"] = legacy
        try:
            save(values)
        except OSError:
            # Can't write yet — fine, we'll re-migrate next launch.
            log.exception("Couldn't persist the migrated games list")
    return values


def save(values: dict) -> None:
    """Writes via a temp file + os.replace, so an interrupted write can never
    leave a half-written settings.json behind for the next launch to choke on."""
    os.makedirs(data_dir(), exist_ok=True)
    path = settings_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2)
    os.replace(tmp, path)


def _update(key: str, value) -> None:
    values = load()
    values[key] = value
    save(values)


def get_spotify_client_id() -> str:
    return str(load().get("spotify_client_id") or "").strip()


def set_spotify_client_id(client_id: str) -> None:
    _update("spotify_client_id", (client_id or "").strip())


def get_pinned_games() -> list:
    games = load().get("pinned_games")
    return games if isinstance(games, list) else []


def set_pinned_games(games: list) -> None:
    _update("pinned_games", list(games))
