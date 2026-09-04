# Verification for startup.py — the "Start with Windows" registry entry.
#
#   .venv\Scripts\python.exe tests\verify_startup_registry.py
#
# Exits non-zero if anything fails.
#
# This one genuinely touches the real registry, because a fake would only prove
# the fake works. It writes under HKCU\...\Run using a test-only value name, so
# it can never disturb a real "Start with Windows" setting, and it removes what
# it created. HKCU needs no admin rights.

import os
import sys
import winreg

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import startup

def raw_read(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup._RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return None


# Never touch the real entry — swap in a test-only value name for the duration.
REAL_NAME = startup._VALUE_NAME
TEST_NAME = "DualSenseQuickMenu__TEST__DELETE_ME"
real_before = raw_read(REAL_NAME)
startup._VALUE_NAME = TEST_NAME

try:
    print("\n[command]")
    cmd = startup.command()
    check("command is non-empty", bool(cmd))
    check("command is quoted (paths contain spaces)", cmd.startswith('"'),
          f"(got {cmd!r})")
    check("command points at something that exists",
          os.path.exists(cmd.split('"')[1]), f"(got {cmd!r})")
    if not getattr(sys, "frozen", False):
        check("from source, launches via pythonw (no console window)",
              "pythonw.exe" in cmd.lower(), f"(got {cmd!r})")
        check("from source, passes main.py", "main.py" in cmd, f"(got {cmd!r})")

    print("\n[enable / disable]")
    check("starts disabled", startup.is_enabled() is False)
    check("enable() reports success", startup.enable() is True)
    check("is_enabled() now True", startup.is_enabled() is True)
    check("the registry really holds the command", raw_read(TEST_NAME) == cmd,
          f"(got {raw_read(TEST_NAME)!r})")
    check("enabling twice is harmless", startup.enable() is True)

    check("disable() reports success", startup.disable() is True)
    check("is_enabled() now False", startup.is_enabled() is False)
    check("the registry value is gone", raw_read(TEST_NAME) is None)
    check("disabling when already off is fine", startup.disable() is True)

    print("\n[stale path self-heal]")
    # Simulates the app folder having been moved or renamed: the entry exists
    # but points somewhere else. Without the refresh it silently stops launching
    # at login while still showing as enabled.
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, startup._RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, TEST_NAME, 0, winreg.REG_SZ,
                          r'"C:\Somewhere\Old\DualSenseQuickMenu.exe"')
    check("a stale entry still reads as enabled", startup.is_enabled() is True)
    startup.refresh_if_stale()
    check("refresh_if_stale repoints it at this copy", raw_read(TEST_NAME) == cmd,
          f"(got {raw_read(TEST_NAME)!r})")

    startup.disable()
    check("refresh_if_stale does nothing when disabled",
          (startup.refresh_if_stale(), raw_read(TEST_NAME) is None)[1])

finally:
    # Belt and braces: make sure the test value can't survive a failure above.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup._RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, TEST_NAME)
    except FileNotFoundError:
        pass
    startup._VALUE_NAME = REAL_NAME

print("\n[the real setting was left alone]")
check("real Run entry is unchanged", raw_read(REAL_NAME) == real_before,
      f"(before={real_before!r} after={raw_read(REAL_NAME)!r})")
check("the test value cleaned itself up", raw_read(TEST_NAME) is None)

finish()
