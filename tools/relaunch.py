# Dev convenience: stop any running copy of this app and start a fresh one,
# so testing a code change is one command instead of "find pythonw.exe in Task
# Manager, kill it, remember the run command, retype it." There's no hot
# reload (see HANDOFF.md), so this is the fast path for that.
#
#   .venv\Scripts\python.exe tools\relaunch.py
#
# Add --hidden to launch via pythonw.exe (no console, matching how a real user
# runs it) instead of the default python.exe (a visible console, so a
# traceback during development shows up immediately instead of only in
# log.txt). Unrelated to tools/build.py — this runs from source, never touches
# the packaged build.

import argparse
import os
import subprocess
import sys
import time

import psutil

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.normcase(os.path.join(_ROOT, "main.py"))


def _find_running() -> list:
    """Every python/pythonw process whose command line is running this app's
    main.py — matching by name alone would just as easily catch an unrelated
    Python process (or this very script) running on the same machine."""
    matches = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        if (proc.info["name"] or "").lower() not in ("python.exe", "pythonw.exe"):
            continue
        cmdline = proc.info["cmdline"] or []
        if any(os.path.normcase(os.path.abspath(arg)) == _MAIN for arg in cmdline if arg):
            matches.append(proc)
    return matches


def _stop_running() -> None:
    procs = _find_running()
    if not procs:
        print("No running copy found.")
        return
    for proc in procs:
        print(f"Stopping pid {proc.pid}...")
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass

    # single_instance.py's mutex is released by the OS on process exit, so
    # waiting here (rather than firing the new launch immediately) is what
    # keeps the new copy from occasionally losing the single-instance race
    # against the old one still shutting down. Polled via pid_exists() rather
    # than psutil's own wait_procs()/Process.wait() — those call OpenProcess
    # with SYNCHRONIZE rights, which this app's own pythonw.exe process
    # (spawned without inheriting elevated rights) has intermittently denied
    # even though terminate() on the same handle just succeeded.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        procs = [p for p in procs if psutil.pid_exists(p.pid)]
        if not procs:
            return
        time.sleep(0.2)

    for proc in procs:
        print(f"pid {proc.pid} didn't exit in time, killing...")
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


def _launch(hidden: bool) -> None:
    exe = "pythonw.exe" if hidden else "python.exe"
    python = os.path.join(_ROOT, ".venv", "Scripts", exe)
    print(f"Launching {exe} main.py...")
    subprocess.Popen(
        [python, _MAIN],
        cwd=_ROOT,
        creationflags=subprocess.DETACHED_PROCESS if hidden else subprocess.CREATE_NEW_CONSOLE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hidden", action="store_true",
        help="launch via pythonw.exe (no console) instead of python.exe",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This app is Windows-only.")
        sys.exit(1)

    _stop_running()
    _launch(args.hidden)


if __name__ == "__main__":
    main()
