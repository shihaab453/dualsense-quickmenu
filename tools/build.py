# Produces a distributable build, and refuses to hand you one that doesn't
# work.
#
#   .venv\Scripts\python.exe tools\build.py
#
# Steps: source verification -> PyInstaller -> run the built exe's --selftest
# -> zip it. Neither verification stage is optional. Source tests catch
# behavior regressions, while the packaged selftest catches silent frozen-build
# failures such as a missing QtSvg plugin, font, hidapi.dll, or winrt module.

import os
import subprocess
import sys
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = os.path.join(_ROOT, "DualSenseQuickMenu.spec")
_DIST = os.path.join(_ROOT, "dist", "DualSenseQuickMenu")
_EXE = os.path.join(_DIST, "DualSenseQuickMenu.exe")
_ZIP = os.path.join(_ROOT, "dist", "DualSenseQuickMenu-windows.zip")
_VERIFY_SCRIPTS = (
    "verify_settings.py",
    "verify_logging.py",
    "verify_startup.py",
    "verify_diagnostics.py",
    "verify_startup_registry.py",
    "verify_spotify_links.py",
    "verify_panel_anchor.py",
    "verify_now_playing_card.py",
    "verify_power.py",
    "verify_appswitcher.py",
    "verify_hotkey.py",
    "verify_music_pagination.py",
)


def step(text: str) -> None:
    print(f"\n=== {text} ===")


def _log_path() -> str:
    sys.path.insert(0, _ROOT)
    import logs

    return logs.log_path()


def _log_size() -> int:
    """Byte length of the log right now, to read back only what a later step
    appends. Deliberately not "scan backwards for the start-of-run banner":
    that couples this script to the exact wording of a log line somewhere else,
    and silently prints every previous run's results once the wording changes
    (which is exactly what happened)."""
    try:
        return os.path.getsize(_log_path())
    except OSError:
        return 0


def _print_selftest_since(offset: int) -> None:
    """Echoes selftest lines appended to the log after the given byte offset."""
    try:
        size = os.path.getsize(_log_path())
        # Rotation between the two reads would leave the offset past the end.
        with open(_log_path(), "rb") as f:
            f.seek(offset if offset <= size else 0)
            text = f.read().decode("utf-8", errors="replace")
    except OSError as e:
        print(f"  (couldn't read the log: {e})")
        return

    for line in text.splitlines():
        if "selftest:" in line:
            print("  " + line.split("selftest:", 1)[1].strip())


def _run_source_verification(python: str) -> int:
    """Run every checked-in verification script before creating build output."""
    for script in _VERIFY_SCRIPTS:
        result = subprocess.run([python, os.path.join("tests", script)], cwd=_ROOT)
        if result.returncode != 0:
            print(f"\nSOURCE VERIFICATION FAILED: {script}")
            return result.returncode
    return 0


def main() -> int:
    python = sys.executable

    step("source verification")
    result = _run_source_verification(python)
    if result != 0:
        print("not packaging this build.")
        return result

    step("PyInstaller")
    result = subprocess.run(
        [python, "-m", "PyInstaller", _SPEC, "--noconfirm", "--log-level", "WARN"],
        cwd=_ROOT,
    )
    if result.returncode != 0:
        print("build failed")
        return result.returncode
    if not os.path.exists(_EXE):
        print(f"build reported success but {_EXE} is missing")
        return 1

    step("selftest against the built exe")
    # Read the results back out of the log rather than relying on the exe's
    # stdout: it's a windowed build with no console of its own, so whether
    # anything reaches this terminal depends on how we were invoked.
    log_offset = _log_size()
    # CI and remote build shells may not have an interactive desktop. The
    # self-test renders assets but never shows a window, so Qt's offscreen
    # platform is the appropriate backend and avoids waiting for a display.
    selftest_env = os.environ.copy()
    selftest_env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run([_EXE, "--selftest"], cwd=_DIST, env=selftest_env)
    _print_selftest_since(log_offset)
    if result.returncode != 0:
        print("\nSELFTEST FAILED — not packaging this build.")
        return result.returncode

    step("zip")
    if os.path.exists(_ZIP):
        os.remove(_ZIP)
    with zipfile.ZipFile(_ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _dirs, files in os.walk(_DIST):
            for name in files:
                full = os.path.join(folder, name)
                # Paths inside the zip start with DualSenseQuickMenu/, so
                # extracting produces one tidy folder rather than spraying
                # ~800 files into wherever the user extracted it.
                archive.write(full, os.path.relpath(full, os.path.dirname(_DIST)))

    size_mb = os.path.getsize(_ZIP) / (1024 * 1024)
    folder_mb = sum(
        os.path.getsize(os.path.join(f, n))
        for f, _d, ns in os.walk(_DIST)
        for n in ns
    ) / (1024 * 1024)
    print(f"\n{_ZIP}")
    print(f"  {size_mb:.0f} MB zipped, {folder_mb:.0f} MB extracted")
    print("\nReady to distribute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
