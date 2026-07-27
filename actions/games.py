# Task Switcher backing: a curated list of games (there's no Windows API for
# "recently played games" the way a PS5 has, so the list is maintained by the
# user through the Settings window, and stored in %APPDATA% via settings.py).
# "Recent" games are whichever of those are currently running, detected via
# psutil rather than any launcher-specific API, so this works regardless of
# whether a game came from Epic, Steam, or anywhere else.

import os

import psutil

import settings


def _load_games() -> list:
    return settings.get_pinned_games()


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
