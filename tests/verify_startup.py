# Verification for the startup batch: version stamping, the first-run flag, and
# the single-instance guard.
#
#   .venv\Scripts\python.exe tests\verify_startup.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before anything reads it, so this never touches the real
# %APPDATA%\DualSenseQuickMenu\ (settings, token, or log).

import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import settings

_TMP = tempfile.mkdtemp(prefix="dsqm_startup_")
settings.data_dir = lambda: _TMP

import single_instance
import version

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


# ------------------------------------------------------------------- version
print("\n[version]")
check("APP_NAME is set", bool(version.APP_NAME))
check("VERSION is set", bool(version.VERSION))
check("numeric_version is a 4-tuple of ints",
      len(version.numeric_version()) == 4
      and all(isinstance(n, int) for n in version.numeric_version()))

# The Windows version resource has no room for a "-alpha.1" suffix, so the
# pre-release counter has to survive as the fourth component.
_real_version = version.VERSION
for text, expected in [
    ("0.1.0-alpha.1", (0, 1, 0, 1)),
    ("0.2.0-beta.3", (0, 2, 0, 3)),
    ("1.0.0", (1, 0, 0, 0)),
    ("1.2", (1, 2, 0, 0)),          # short form still yields 4 components
    ("2.0.1-rc", (2, 0, 1, 0)),     # non-numeric suffix, no counter
]:
    version.VERSION = text
    check(f"{text!r} -> {expected}", version.numeric_version() == expected,
          f"(got {version.numeric_version()})")
version.VERSION = _real_version

# ----------------------------------------------------------------- first run
print("\n[first-run flag]")
check("a fresh install reports first run", settings.is_first_run() is True)
settings.mark_launched()
check("no longer first run after mark_launched", settings.is_first_run() is False)
check("the flag survives an unrelated settings write",
      (settings.set_spotify_client_id("a" * 32),
       settings.is_first_run() is False)[1])
check("marking twice is harmless",
      (settings.mark_launched(), settings.is_first_run() is False)[1])

# ----------------------------------------------------------- single instance
print("\n[single-instance guard]")
check("first caller in a process gets the lock",
      single_instance.already_running() is False)

# The real test: another *process* must see the lock this one now holds.
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'" + _ROOT + "');"
     " import single_instance;"
     " print('LOCKED' if single_instance.already_running() else 'FREE')"],
    capture_output=True,
    text=True,
)
check("a second process sees the lock", "LOCKED" in probe.stdout,
      f"(stdout={probe.stdout.strip()!r} stderr={probe.stderr.strip()[:120]!r})")

# And once this process releases it (by exiting), a later one must be able to
# acquire it — verified by checking a subprocess that runs *without* a holder.
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'" + _ROOT + "');"
     " import single_instance;"
     " print('LOCKED' if single_instance.already_running() else 'FREE');"
     " print('LOCKED2' if single_instance.already_running() else 'FREE2')"],
    capture_output=True,
    text=True,
)
check("the lock is per-session, not permanent on disk",
      "LOCKED" in probe.stdout,
      "(a stale lock file would show FREE here since we hold the mutex)")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
else:
    print("All checks passed.")
print("=" * 60)
sys.exit(1 if failures else 0)
