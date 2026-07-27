# Produces a distributable build, and refuses to hand you one that doesn't
# work.
#
#   .venv\Scripts\python.exe tools\build.py
#
# Steps: PyInstaller -> run the built exe's --selftest -> zip it. The selftest
# is not optional, because the ways a packaged build of this app breaks are all
# silent: a missing QtSvg plugin renders every icon blank, a missing font
# quietly downgrades all text, an unbundled hidapi.dll means the PS button
# never responds, and a missing winrt kills the Now Playing fallback. None of
# them raise, so "it built" tells you almost nothing.

import os
import subprocess
import sys
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = os.path.join(_ROOT, "DualSenseQuickMenu.spec")
_DIST = os.path.join(_ROOT, "dist", "DualSenseQuickMenu")
_EXE = os.path.join(_DIST, "DualSenseQuickMenu.exe")
_ZIP = os.path.join(_ROOT, "dist", "DualSenseQuickMenu-windows.zip")


def step(text: str) -> None:
    print(f"\n=== {text} ===")


def _print_selftest_from_log() -> None:
    """Echoes the most recent run's selftest lines out of log.txt."""
    sys.path.insert(0, _ROOT)
    import logs

    try:
        with open(logs.log_path(), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"  (couldn't read the log: {e})")
        return

    # Only the lines since the last app start, so an earlier run's results
    # can't be mistaken for this one's.
    for index in range(len(lines) - 1, -1, -1):
        if "app start" in lines[index]:
            lines = lines[index:]
            break
    for line in lines:
        if "selftest:" in line:
            print("  " + line.split("selftest:", 1)[1].strip())


def main() -> int:
    python = sys.executable

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
    result = subprocess.run([_EXE, "--selftest"], cwd=_DIST)
    # Read the results back out of the log rather than relying on the exe's
    # stdout: it's a windowed build with no console of its own, so whether
    # anything reaches this terminal depends on how we were invoked.
    _print_selftest_from_log()
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
