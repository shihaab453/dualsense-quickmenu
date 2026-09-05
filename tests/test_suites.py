# Runs each verification suite and reports it to pytest.
#
# Each suite runs in its own process, and that is load-bearing rather than
# lazy. The suites replace module attributes outright and never put them back
# (verify_settings makes get_playlists_page raise; verify_music_loading swaps
# spotify_client.submit for a queue), and most of them build their own
# QApplication, of which a process gets one. Imported into a shared process
# they would quietly poison each other, and the failures would depend on
# collection order - the worst kind. A process boundary is the isolation.
#
# So: don't "modernise" this by importing the suites. If you want a test that
# runs in-process, write it as a normal pytest test in a new tests\test_*.py
# file - that is the direction for new tests, and nothing stops the two living
# side by side.
#
# Suites are discovered from disk, so a new tests\verify_*.py is picked up
# with no list to remember to update. It lands in the `unit` group unless it
# is named in _NEEDS_REAL_WINDOWS below.

import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).parent

# Suites that touch real Windows state rather than a temp folder: the actual
# registry (under a test-only value name), real window enumeration and
# foreground switching, and a really-registered global hotkey. They pass on a
# normal desktop, but they will take focus for a moment while they run, and
# they are the ones to exclude first on a machine where that matters.
_NEEDS_REAL_WINDOWS = {
    "verify_startup.py",       # creates a real named Windows mutex
    "verify_startup_registry.py",
    "verify_appswitcher.py",
    "verify_hotkey.py",
}

# No suite needs a controller today. The group exists so that the first one to
# need it has somewhere to go, and so CI can be told about it up front rather
# than being rewritten later. See conftest.py for the opt-in.
_NEEDS_HARDWARE: set[str] = set()

# A suite exits with this when it can't run *here* rather than because
# something is broken - verify_hotkey when another process already holds
# Ctrl+Alt+P, which is usually a running copy of the app. Reporting that as a
# failure would train people to ignore failures, so it becomes a pytest skip.
_EXIT_SKIPPED = 2

# Generous: verify_appswitcher launches a real subprocess and waits for its
# window to appear, and verify_hotkey waits on real Windows messages. This is
# here to stop a hung suite hanging the whole run, not to police speed.
_TIMEOUT_SECONDS = 300


def _suites():
    for path in sorted(_TESTS.glob("verify_*.py")):
        if path.name in _NEEDS_HARDWARE:
            mark = pytest.mark.hardware
        elif path.name in _NEEDS_REAL_WINDOWS:
            mark = pytest.mark.windows
        else:
            mark = pytest.mark.unit
        yield pytest.param(path, marks=mark, id=path.stem)


@pytest.mark.parametrize("suite", list(_suites()))
def test_suite(suite):
    try:
        result = subprocess.run(
            [sys.executable, str(suite)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            cwd=_TESTS.parent,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{suite.name} did not finish within {_TIMEOUT_SECONDS}s")

    if result.returncode == _EXIT_SKIPPED:
        reason = next(
            (line for line in result.stdout.splitlines() if line.startswith("SKIPPED")),
            f"{suite.name} reported it couldn't run here",
        )
        pytest.skip(reason)

    if result.returncode != 0:
        # The suite already prints a PASS/FAIL line per check and a summary of
        # what failed, so hand that straight to the reader rather than
        # paraphrasing it into a pytest assertion message.
        pytest.fail(
            f"{suite.name} exited {result.returncode}\n\n"
            f"--- its output ---\n{result.stdout}\n"
            f"--- its stderr ---\n{result.stderr}",
            pytrace=False,
        )


def test_every_suite_is_accounted_for():
    """The group lists name files; a rename would silently drop a suite into
    `unit` (or, worse, name one that no longer exists) with nothing to say so."""
    on_disk = {path.name for path in _TESTS.glob("verify_*.py")}
    named = _NEEDS_REAL_WINDOWS | _NEEDS_HARDWARE
    missing = named - on_disk
    assert not missing, f"listed in a group but not on disk: {sorted(missing)}"
    assert on_disk, "no verification suites found at all"
