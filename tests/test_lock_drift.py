# Catches a requirements*.txt edit that was never compiled into the matching
# lock file.
#
# Why this needs to exist. The lock files are what actually get installed:
# CI and tools/build.py both run `pip install --require-hashes -r
# requirements-dev.lock.txt`, and requirements.txt is never installed from
# directly. So bumping a pin in requirements.txt and forgetting to re-run
# pip-compile does not fail anywhere. It silently keeps installing the old
# version, the tests pass against the old version, and the build ships the old
# version - while the file a human reads says otherwise. Nothing in the
# repository noticed that before this test.
#
# This is a *consistency* check, not a re-compile. It does not resolve
# dependencies, reach the network, or need pip-tools installed, which is what
# lets it sit in the hermetic `unit` group and run on every push. It cannot
# know whether a transitive dependency needs updating - only pip-compile knows
# that. It does catch the case that actually happens, which is a direct pin
# edited in one file and not the other.
#
# When this fails, regenerate rather than hand-editing the lock (the hashes
# have to match the real artifacts):
#
#   .venv\Scripts\python.exe -m pip install pip-tools
#   .venv\Scripts\python.exe -m piptools compile --generate-hashes --no-header
#       --output-file=requirements.lock.txt requirements.txt
#   .venv\Scripts\python.exe -m piptools compile --generate-hashes --no-header
#       --output-file=requirements-dev.lock.txt requirements-dev.txt

import re
from pathlib import Path

import pytest

# Plain test_*.py files carry no marker of their own, and CI runs `-m unit`
# (see tools/build.py) - without this the check would be collected locally and
# silently deselected in the one place it most needs to run.
pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parent.parent

# (source, lock) pairs. requirements-dev.txt starts with `-r requirements.txt`,
# so its lock has to satisfy both files - hence the runtime pins appear in both
# columns of the check below.
_PAIRS = [
    ("requirements.txt", "requirements.lock.txt"),
    ("requirements-dev.txt", "requirements-dev.lock.txt"),
]


def _normalise(name: str) -> str:
    """PEP 503 name normalisation. pip-compile writes `pyside6`, the source
    file says `PySide6`, and `winrt-Windows.Media.Control` becomes
    `winrt-windows-media-control` - all the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements(path: Path) -> dict[str, str]:
    """The `name==version` pins declared directly in a requirements file, as
    {normalised name: version}. Follows `-r other.txt` includes, because
    requirements-dev.txt is mostly one of those."""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r"):
            included = line[2:].strip()
            pins.update(_requirements(path.parent / included))
            continue
        if line.startswith("-"):
            continue  # some other pip flag; not a pin
        name, sep, version = line.partition("==")
        assert sep, f"{path.name}: expected a pinned `name==version`, got {line!r}"
        pins[_normalise(name)] = version.strip()
    return pins


def _locked(path: Path) -> dict[str, str]:
    """{normalised name: version} for every package in a hash-pinned lock.

    A lock entry spans several lines - `name==version \\` then one indented
    `--hash=...` per artifact - so only the unindented lines are pins."""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw[0].isspace() or raw.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", raw)
        if match:
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


@pytest.mark.parametrize(("source", "lock"), _PAIRS)
def test_every_declared_pin_is_in_the_lock(source, lock):
    """A package named in requirements*.txt must appear in the lock at the
    same version. This is the check that catches an edit to one file and not
    the other."""
    declared = _requirements(_ROOT / source)
    locked = _locked(_ROOT / lock)
    assert declared, f"{source} parsed to no pins at all - the parser is wrong"

    missing = sorted(name for name in declared if name not in locked)
    assert not missing, (
        f"{source} declares {missing} but {lock} does not contain "
        f"{'them' if len(missing) > 1 else 'it'}. Regenerate the lock - see "
        f"this file's header."
    )

    drifted = {
        name: (version, locked[name])
        for name, version in declared.items()
        if locked[name] != version
    }
    assert not drifted, (
        f"{source} and {lock} disagree about "
        + ", ".join(
            f"{name} ({source} says {want}, lock installs {got})"
            for name, (want, got) in sorted(drifted.items())
        )
        + ". The lock is what gets installed, so the version actually shipped "
        "is the second one. Regenerate the lock - see this file's header."
    )


@pytest.mark.parametrize(("source", "lock"), _PAIRS)
def test_every_locked_package_carries_hashes(source, lock):
    """--require-hashes refuses to install an entry with no hashes, so a lock
    that lost them fails at install time in CI rather than here. Catching it
    here names the package instead of leaving a pip error to interpret."""
    text = (_ROOT / lock).read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    unhashed = [
        block.split("==")[0].strip()
        for block in blocks
        if re.match(r"^[A-Za-z0-9_.\-]+==", block.strip()) and "--hash=" not in block
    ]
    assert not unhashed, (
        f"{lock} has no hashes for {sorted(unhashed)}, which "
        f"`pip install --require-hashes` will reject at install time."
    )


def test_the_dev_lock_covers_the_runtime_lock():
    """requirements-dev.txt includes requirements.txt, so anything in the
    runtime lock has to be in the dev lock too - otherwise CI, which installs
    only the dev lock, would be running against a different dependency set
    than a release build resolves."""
    runtime = _locked(_ROOT / "requirements.lock.txt")
    dev = _locked(_ROOT / "requirements-dev.lock.txt")
    assert runtime, "requirements.lock.txt parsed to no pins at all"

    missing = sorted(name for name in runtime if name not in dev)
    assert not missing, (
        f"requirements-dev.lock.txt is missing {missing}, which "
        f"requirements.lock.txt pins. Regenerate both."
    )

    drifted = {
        name: (version, dev[name])
        for name, version in runtime.items()
        if dev[name] != version
    }
    assert not drifted, (
        "the two locks disagree about "
        + ", ".join(
            f"{name} (runtime {a}, dev {b})"
            for name, (a, b) in sorted(drifted.items())
        )
        + ". CI installs the dev lock, so that is the version being tested."
    )
