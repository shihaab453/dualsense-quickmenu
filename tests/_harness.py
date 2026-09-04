# The PASS/FAIL harness the verification suites share.
#
# Every suite used to carry its own copy of this, and the copies had drifted:
# nine printed a check's detail only when it failed while five printed it
# either way, and four summarised failures on one comma-joined line while ten
# listed them. Nothing depended on the difference, which is exactly why it went
# unnoticed - so this is one copy, and the more informative of each pair won.
#
# Deliberately not a pytest plugin, and deliberately importable with a plain
# `from _harness import ...`: each suite still has to run as a standalone
# script (`python tests\verify_settings.py`), which is both how they are
# documented and how tests\test_suites.py runs them. Python puts the script's
# own directory on sys.path, so that import works with no packaging.

import sys

# "Couldn't run here", as opposed to "something is broken" — tests/
# test_suites.py turns this exit code into a pytest skip. Kept in step with
# _EXIT_SKIPPED there.
EXIT_SKIPPED = 2

_failures: list[str] = []


def check(label: str, condition, detail: str = "") -> bool:
    """Record one check and print its result. Returns whether it passed, so a
    caller can skip work that only makes sense after something held.

    `detail` prints on a pass as well as a failure. It costs a wider line and
    earns its keep: "(got ['Liked Songs', 'Playlist A'])" next to a PASS is
    what tells you an assertion is checking what you think it is, which is the
    failure mode a green test suite can't otherwise show you."""
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)
    return ok


def failures() -> list[str]:
    """Labels of the checks that have failed so far."""
    return list(_failures)


def finish(app=None) -> None:
    """Print the summary and end the run: exit 0 if everything passed, 1 if
    anything didn't.

    Pass `app` from a suite that runs inside a Qt event loop. Those have to
    end through `app.exit(code)`, with the process's status coming from the
    `sys.exit(app.exec())` at the bottom of the file — calling sys.exit() from
    inside a Qt callback raises SystemExit in the middle of the event loop
    instead, which is not a reliable way to stop one."""
    print("\n" + "=" * 60)
    if _failures:
        print(f"{len(_failures)} FAILURE(S):")
        for label in _failures:
            print(f"  - {label}")
    else:
        print("All checks passed.")
    print("=" * 60)

    code = 1 if _failures else 0
    if app is not None:
        app.exit(code)
    else:
        sys.exit(code)


def skip(reason: str) -> None:
    """Bail out because this machine can't run the suite — not because
    anything is wrong with the code. Ends the process immediately.

    Use it sparingly, and only for a condition the suite genuinely cannot
    create for itself: verify_hotkey uses it when another process already
    holds the key combination it has to register. Skipping a check because it
    is failing is how a suite stops meaning anything."""
    print(f"SKIPPED: {reason}")
    sys.exit(EXIT_SKIPPED)
