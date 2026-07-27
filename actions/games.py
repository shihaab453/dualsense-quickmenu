# Task Switcher backing: a manually-curated list of games (there's no
# Windows API for "recently played games" the way PS5 has, so the user
# maintains config/pinned_games.json themselves — see that file for the
# format). "Recent" games are whichever of those are currently running,
# detected via psutil rather than any launcher-specific API, so this works
# regardless of whether a game came from Epic, Steam, or anywhere else.

import json
import os

import psutil

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "pinned_games.json",
)


def _load_games() -> list:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _running_process_names() -> set:
    names = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info.get("name")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name:
            names.add(name.lower())
    return names


def get_pinned_games() -> list:
    """Every game configured in pinned_games.json, as {"name", "path"}."""
    return _load_games()


def get_recent_games() -> list:
    """Configured games whose executable is currently a running process."""
    running = _running_process_names()
    games = _load_games()
    return [g for g in games if os.path.basename(g.get("path", "")).lower() in running]


def launch(path: str) -> None:
    os.startfile(path)
