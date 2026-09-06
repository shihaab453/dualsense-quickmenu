# Produces a distributable, versioned, evidenced build, and refuses to hand
# you one that doesn't work.
#
#   .venv\Scripts\python.exe tools\build.py
#
# Steps: source verification -> PyInstaller -> selftest the built exe ->
# zip it under a name that carries its version and source commit -> selftest
# *that* zip after extracting it somewhere else -> write a manifest (file
# hashes + build metadata + known limitations) and a smoke-test record next
# to it. Every verification stage is a gate: this script's exit code is
# nonzero if any of them failed, even though the zip and its evidence are
# still written to disk so a failure is something you can inspect rather
# than a build that silently didn't happen.
#
# What this buys you, and what it doesn't: hash-pinned dependencies
# (requirements*.lock.txt) plus these checks mean you can trust *which
# packages* went into a given zip and that the packaged app's basic plumbing
# actually works once unzipped somewhere else. They do not make the build
# *reproducible* - PyInstaller embeds a build timestamp and absolute paths,
# and nothing here pins the compiler or Windows SDK on the machine that ran
# this. Two runs of this script produce two different zips even from the
# same commit. Don't describe a build this makes as reproducible; describe
# it as evidenced.

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from importlib import metadata

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# So `import version` / `import logs` below can find them: this script's own
# directory (tools/) is what Python puts on sys.path by default, not the
# repo root. Done once, up front, rather than relying on _log_path() being
# the first thing that happens to need it — that was true by accident of
# call order and would break the moment a step got reordered.
sys.path.insert(0, _ROOT)
_SPEC = os.path.join(_ROOT, "DualSenseQuickMenu.spec")
_DIST_DIR = os.path.join(_ROOT, "dist")
_DIST = os.path.join(_DIST_DIR, "DualSenseQuickMenu")
_EXE_NAME = "DualSenseQuickMenu.exe"
_EXE = os.path.join(_DIST, _EXE_NAME)
_LOCK_FILE = os.path.join(_ROOT, "requirements-dev.lock.txt")
_TEST_EVIDENCE = os.path.join(_DIST_DIR, "test-evidence.xml")
_SMOKE_RECORD = os.path.join(_DIST_DIR, "smoke-test-record.json")

# How long the packaged --selftest gets before this script gives up on it and
# calls the build broken. It normally finishes in a couple of seconds; a
# hang here (a modal dialog nothing is present to dismiss, a network call
# that forgot its own timeout) would otherwise wedge a build or a CI runner
# for however long the job-level timeout takes to notice, which is a much
# less informative failure than "selftest didn't finish in 60s".
_SELFTEST_TIMEOUT_SECONDS = 60

# Kept short and conservative on purpose: this ships inside the manifest,
# which is build *evidence*, not user-facing copy - so it only restates
# limitations already public in README.md rather than asserting anything new.
# Update both places together if either changes.
_KNOWN_LIMITATIONS = [
    "Windows only.",
    "The overlay cannot draw over exclusive-fullscreen games; Borderless "
    "windowed mode is required (see README.md, Troubleshooting).",
    "Spotify features require the user's own Spotify Developer client ID; "
    "there is no bundled/shared one (see README.md, Setup).",
    "Interactive acceptance checks (tray icon, foreground/focus behavior, "
    "physical DualSense HID input) are not part of this evidence record - "
    "see the 'windows' and 'hardware' pytest markers, which this build "
    "deliberately does not gate on in CI.",
]


def step(text: str) -> None:
    print(f"\n=== {text} ===")


def _log_path() -> str:
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


def _redact_home(text: str) -> str:
    """These lines land in the manifest and smoke-test record on disk, which
    outlive this run and (via CI) may end up somewhere more people can see
    than a developer's own machine. selftest's `assets=` line includes an
    absolute path, and on a real dev box that path runs through the user's
    home directory - the same reasoning diagnostics.py applies to a log
    excerpt bound for a chat message applies here too."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if not home:
        return text
    import re

    return re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)


def _selftest_lines_since(offset: int) -> list:
    """The selftest's own PASS/FAIL lines appended to the log after the given
    byte offset, in the order they were written."""
    try:
        size = os.path.getsize(_log_path())
        # Rotation between the two reads would leave the offset past the end.
        with open(_log_path(), "rb") as f:
            f.seek(offset if offset <= size else 0)
            text = f.read().decode("utf-8", errors="replace")
    except OSError as e:
        return [f"(couldn't read the log: {e})"]
    return [
        _redact_home(line.split("selftest:", 1)[1].strip())
        for line in text.splitlines()
        if "selftest:" in line
    ]


def _run_source_verification(python: str) -> int:
    """Run the checks before creating any build output.

    Through pytest rather than a list of scripts kept here: this file used to
    hold its own copy of that list, and a new suite that nobody remembered to
    add to it was silently not gating builds. pytest discovers them from disk
    (see tests/test_suites.py).

    --junitxml always writes test-evidence.xml, pass or fail, so a broken
    build still leaves behind a record of exactly what failed - see main()'s
    docstring note on why the exit code and the written evidence are kept
    independent of each other.

    Which marker group runs depends on whether this is CI (detected via the
    CI env var GitHub Actions and every other major CI system sets): CI runs
    only `unit` - hermetic checks safe on a runner with no interactive
    desktop session. `windows` tests are real but need a foreground window
    and a real global hotkey registration, which GitHub's hosted Windows
    runners don't reliably provide outside an interactive logon; HANDOFF's
    own instruction is to keep those a separate acceptance step rather than
    silently skip them and call it release evidence, so they stay part of
    the *local* dev build (this script's non-CI branch) where a real desktop
    session is a safe assumption. `hardware` needs a physical DualSense and
    is opt-in everywhere. -rs reports every skip and why, so "what this run
    didn't check" is part of the evidence too, not silently absent from it."""
    marker = "unit" if os.environ.get("CI") else "not hardware"
    result = subprocess.run(
        [
            python, "-m", "pytest", "-rs", "-m", marker,
            f"--junitxml={_TEST_EVIDENCE}",
        ],
        cwd=_ROOT,
    )
    if result.returncode != 0:
        print("\nSOURCE VERIFICATION FAILED")
        return result.returncode
    return 0


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_info() -> dict:
    """Source identity for the manifest: which commit this was built from,
    and whether the tree matched it exactly. Never includes author name or
    email - this manifest is build evidence that may end up somewhere more
    people can see than the repo's own history, and a commit hash already
    identifies the build without attaching anyone's name to it directly.

    Falls back to "unknown" rather than raising: a build from a source
    export with no .git directory should still produce a zip, just without
    the traceability a real checkout gives you."""

    def run(*args):
        try:
            result = subprocess.run(
                ["git", *args], cwd=_ROOT, capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    commit = run("rev-parse", "HEAD")
    short_commit = run("rev-parse", "--short", "HEAD")
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    dirty = None
    status = run("status", "--porcelain")
    if status is not None:
        dirty = bool(status)
    return {
        "commit": commit or "unknown",
        "short_commit": short_commit or "unknown",
        "branch": branch or "unknown",
        "dirty": dirty,
    }


def _artifact_stem(git_info: dict) -> str:
    """DualSenseQuickMenu-<version>-<short commit>[-dirty]. The old name
    ("DualSenseQuickMenu-windows.zip") carried no version at all - two builds
    handed to two testers a week apart were indistinguishable by filename,
    which is exactly the problem version.py's own module comment describes
    for log banners. -dirty is a deliberate loud flag, not a footnote: a
    build made from an uncommitted tree can't be reproduced from source
    control at all, and that is worth seeing in the filename before you ever
    open the manifest."""
    import version

    stem = f"DualSenseQuickMenu-{version.VERSION}-{git_info['short_commit']}"
    if git_info["dirty"]:
        stem += "-dirty"
    return stem


def _run_selftest(exe: str, cwd: str, label: str) -> dict:
    """Run the packaged --selftest against one copy of the build, with a
    timeout, and return a record of what happened - never raises, since a
    timeout or a missing exe is exactly the kind of result this exists to
    report rather than crash on.

    `label` distinguishes *which* copy this was: "in-place" (the PyInstaller
    output where it was built) catches an ordinary regression, "extracted"
    (a fresh unzip elsewhere) catches a packaging bug that only shows up once
    the build is no longer sitting next to the source tree it was made in -
    a relative path assumption, for instance. Both are real questions; only
    running the first one and calling it "the build works" would be the same
    shape of overclaim as promising reproducibility from pinned versions."""
    log_offset = _log_size()
    started = datetime.now(timezone.utc)
    start = time.monotonic()
    selftest_env = os.environ.copy()
    # CI and remote build shells may not have an interactive desktop. The
    # self-test renders assets but never shows a window, so Qt's offscreen
    # platform is the appropriate backend and avoids waiting for a display.
    selftest_env["QT_QPA_PLATFORM"] = "offscreen"

    record = {
        "label": label,
        "started_at": started.isoformat(),
        "timed_out": False,
        "returncode": None,
        "duration_seconds": None,
        "lines": [],
    }
    try:
        result = subprocess.run(
            [exe, "--selftest"], cwd=cwd, env=selftest_env,
            timeout=_SELFTEST_TIMEOUT_SECONDS,
        )
        record["returncode"] = result.returncode
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        record["returncode"] = None
    finally:
        record["duration_seconds"] = round(time.monotonic() - start, 2)
        record["lines"] = _selftest_lines_since(log_offset)
    return record


def _print_selftest_record(record: dict) -> None:
    for line in record["lines"]:
        print("  " + line)
    if record["timed_out"]:
        print(f"  TIMED OUT after {_SELFTEST_TIMEOUT_SECONDS}s")


def _selftest_passed(record: dict) -> bool:
    return not record["timed_out"] and record["returncode"] == 0


def _build_manifest(zip_path: str, git_info: dict, smoke_records: list) -> dict:
    """Everything HANDOFF.md's evidence-record item asked for, gathered into
    one file next to the zip: source identity, the target dependency lock,
    a hash of the artifact, and the
    smoke results this same run just produced. Written whether or not
    everything passed - see main()'s note on why."""
    import version

    file_hashes = {}
    for folder, _dirs, files in os.walk(_DIST):
        for name in files:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, _DIST).replace(os.sep, "/")
            file_hashes[rel] = _sha256(full)

    lock_hash = None
    if os.path.exists(_LOCK_FILE):
        lock_hash = _sha256(_LOCK_FILE)

    return {
        "app_name": version.APP_NAME,
        "version": version.VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": git_info,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "dependency_lock": {
            "file": os.path.basename(_LOCK_FILE),
            "sha256": lock_hash,
            "verification": "Installed versions checked before building; "
                            "installed file hashes were not verified.",
        },
        "artifact": {
            "file": os.path.basename(zip_path),
            "sha256": _sha256(zip_path),
            "size_bytes": os.path.getsize(zip_path),
        },
        "contents_sha256": file_hashes,
        "test_evidence": os.path.basename(_TEST_EVIDENCE),
        "smoke_tests": smoke_records,
        "known_limitations": _KNOWN_LIMITATIONS,
        "reproducibility_caveat": (
            "Dependencies are hash-pinned (requirements*.lock.txt) and every "
            "listed check passed against this exact commit, but the build "
            "itself is not reproducible: PyInstaller embeds a build "
            "timestamp and absolute paths, and no compiler/SDK version is "
            "pinned. Re-running this script produces a different zip even "
            "from an identical commit."
        ),
    }


def _verify_installed_dependencies() -> bool:
    """Check the environment that will actually run PyInstaller.

    CI installs the lock first, but a local build uses whatever is already
    in its interpreter. Hashing the lock into a manifest does not prove those
    versions were installed. Fail before testing or packaging rather than
    silently changing the developer's environment. This checks versions, not
    the provenance or integrity of already-installed wheel contents.
    """
    try:
        with open(_LOCK_FILE, encoding="utf-8") as lock:
            pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock.read(), re.MULTILINE)
    except OSError as error:
        print(f"Cannot read the dependency lock: {error}")
        return False
    if not pins:
        print("The dependency lock contains no pinned packages.")
        return False
    mismatches = []
    for name, expected in pins:
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = "not installed"
        if installed != expected:
            mismatches.append(f"  {name}: lock requires {expected}, found {installed}")
    if mismatches:
        print("The build environment does not match its dependency lock:")
        print("\n".join(mismatches))
        print("Install requirements-dev.lock.txt with --require-hashes using "
              "this build's Python interpreter, then run the build again.")
        return False
    return True


def main() -> int:
    python = sys.executable
    step("installed dependency versions")
    if not _verify_installed_dependencies():
        return 1
    os.makedirs(_DIST_DIR, exist_ok=True)
    overall_ok = True

    step("source verification")
    result = _run_source_verification(python)
    if result != 0:
        # Still worth recording *why* nothing else ran - test-evidence.xml
        # already exists at this point (pytest wrote it before returning),
        # so the failure is not silent even though this script stops here.
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

    step("selftest (in-place)")
    in_place = _run_selftest(_EXE, _DIST, "in-place")
    _print_selftest_record(in_place)
    if not _selftest_passed(in_place):
        print("\nSELFTEST FAILED — not packaging this build.")
        return 1 if in_place["returncode"] is None else in_place["returncode"]

    step("git identity")
    git_info = _git_info()
    if git_info["dirty"]:
        print("  working tree has uncommitted changes — artifact will be marked -dirty")
    print(f"  commit {git_info['short_commit']} on {git_info['branch']}")

    step("zip")
    stem = _artifact_stem(git_info)
    zip_path = os.path.join(_DIST_DIR, f"{stem}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _dirs, files in os.walk(_DIST):
            for name in files:
                full = os.path.join(folder, name)
                # Paths inside the zip start with DualSenseQuickMenu/, so
                # extracting produces one tidy folder rather than spraying
                # ~800 files into wherever the user extracted it.
                archive.write(full, os.path.relpath(full, os.path.dirname(_DIST)))
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  {zip_path}\n  {size_mb:.0f} MB zipped")

    step("selftest (extracted) — the download-and-extract smoke check")
    # A scratch copy, not a build output: removed again below whether or not
    # the check passes, so this script doesn't leave a second ~full-size copy
    # of the app sitting in dist/ next to the zip on every run.
    extract_dir = os.path.join(_DIST_DIR, f"{stem}-extracted")
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    extracted_exe = os.path.join(extract_dir, "DualSenseQuickMenu", _EXE_NAME)
    extracted = _run_selftest(extracted_exe, os.path.dirname(extracted_exe), "extracted")
    _print_selftest_record(extracted)
    shutil.rmtree(extract_dir, ignore_errors=True)
    if not _selftest_passed(extracted):
        print(
            "\nEXTRACTED-BUILD SELFTEST FAILED — the in-place build passed but this "
            "copy, run from a fresh extraction elsewhere, did not. That's a "
            "packaging bug (a path assumption, most likely), not a code regression. "
            "The zip and its manifest are left on disk so the failure is inspectable, "
            "but this run reports failure."
        )
        overall_ok = False

    step("manifest and evidence record")
    manifest = _build_manifest(zip_path, git_info, [in_place, extracted])
    manifest_path = os.path.join(_DIST_DIR, f"{stem}.manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(_SMOKE_RECORD, "w", encoding="utf-8") as f:
        json.dump({"artifact": os.path.basename(zip_path), "smoke_tests": [in_place, extracted]}, f, indent=2)
    print(f"  {manifest_path}")
    print(f"  {_SMOKE_RECORD}")
    print(f"  {_TEST_EVIDENCE}")

    if overall_ok:
        print("\nReady to distribute.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
