# Locating files that ship *with* the app (fonts, icons) as opposed to files
# belonging to the user (settings, logs — those are settings.py's job).
#
# The two cases differ: running from source, bundled assets sit next to this
# file; running from a PyInstaller build, they've been unpacked into a
# temporary directory whose path is in sys._MEIPASS. Anything that reaches for
# an asset with os.path.dirname(__file__) works from source and silently finds
# nothing once packaged — which is how a frozen build ends up rendering with
# the wrong font and no obvious error.

import os
import sys


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return getattr(sys, "frozen", False)


def base_dir() -> str:
    # _MEIPASS is set for both onefile (a temp dir) and onedir (the app dir)
    # builds, so this one branch covers every packaged case.
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return bundled
    return os.path.dirname(os.path.abspath(__file__))


def path(*parts: str) -> str:
    """Absolute path to a bundled asset, e.g. resources.path("assets", "fonts",
    "Manrope.ttf") — correct whether running from source or from a build."""
    return os.path.join(base_dir(), *parts)
