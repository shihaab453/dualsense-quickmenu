# Regenerates THIRD-PARTY-NOTICES.md from the packages actually installed in
# the venv.
#
#   .venv\Scripts\python.exe tools\make_notices.py
#
# Why this is generated rather than hand-written: it has to be *accurate*, and
# it has to be re-doable when a dependency changes. Reading the licence out of
# each installed distribution's own metadata beats writing down what you
# remember the licence being.
#
# Why the file needs to exist at all: this app isn't open source (see LICENSE),
# but that doesn't remove obligations to its dependencies. MIT and BSD both
# require their copyright notice and permission text to be reproduced in
# distributions, and PySide6 is LGPL v3, which additionally requires that users
# can replace the Qt libraries — the one-folder build keeps them as separate
# DLLs, which is what makes that possible.

import os
import re
import sys
from importlib import metadata

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT = os.path.join(_ROOT, "THIRD-PARTY-NOTICES.md")
_REQUIREMENTS = os.path.join(_ROOT, "requirements.txt")


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _requirement_name(spec: str) -> str | None:
    """The bare package name from a requirement string, or None if it only
    applies to an optional extra.

    Extras matter here: spotipy declares `pymemcache; extra == "memcache"`,
    which is never installed or bundled, while its plain `redis>=3.5.3` is a
    real runtime dependency that does ship."""
    requirement, _, marker = spec.partition(";")
    if "extra ==" in marker:
        return None
    name = re.split(r"[<>=!~\[\s(]", requirement.strip(), maxsplit=1)[0]
    return name or None


def _direct_requirements() -> list[str]:
    names = []
    with open(_REQUIREMENTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            name = _requirement_name(line)
            if name:
                names.append(name)
    return names


def _bundled_distributions() -> list[metadata.Distribution]:
    """Everything reachable from requirements.txt, transitively.

    Deliberately not "every package installed in the venv": that would sweep in
    PyInstaller and its own dependencies, which build the app but are never
    inside it. Walking the declared graph instead means the list reflects what
    actually ships — including transitive dependencies that are easy to forget,
    like psutil, which nothing here imports but pycaw requires."""
    seen: dict[str, metadata.Distribution] = {}
    queue = list(_direct_requirements())
    while queue:
        name = queue.pop(0)
        key = _normalise(name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"  WARNING: {name} is required but not installed — skipped")
            continue
        seen[key] = dist
        for spec in metadata.requires(name) or []:
            child = _requirement_name(spec)
            if child:
                queue.append(child)
    return sorted(seen.values(), key=lambda d: _normalise(d.metadata["Name"]))


def _licence_name(dist: metadata.Distribution) -> str:
    """Best available licence label. Modern wheels use License-Expression
    (PEP 639); older ones put a full licence *text* in License, so fall back to
    the Classifier, which is a short label."""
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()

    classifiers = meta.get_all("Classifier") or []
    labels = [
        c.split("License ::")[-1].strip()
        for c in classifiers
        if c.startswith("License ::")
    ]
    if labels:
        return "; ".join(labels)

    declared = (meta.get("License") or "").strip()
    if declared:
        # Some projects dump the entire licence text into this field.
        first = declared.splitlines()[0].strip()
        return first if len(first) <= 80 else "see licence text below"
    return "not declared in package metadata"


def _licence_text(dist: metadata.Distribution) -> str | None:
    """The distribution's own licence file(s), if the wheel shipped any.

    Resolved through locate_file() and read directly, rather than through
    Distribution.read_text(): read_text resolves names relative to the
    .dist-info directory, so handing it the site-packages-relative path that
    dist.files yields silently finds nothing. Modern wheels put these under
    `<name>.dist-info/licenses/`, which is why nothing turned up at first."""
    parts = []
    for path in dist.files or []:
        text_path = str(path)
        base = os.path.basename(text_path).upper()
        if not base.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        # Only files belonging to the dist's own metadata, not e.g. a LICENSE
        # vendored inside the package's source tree for something else.
        if ".dist-info" not in text_path:
            continue
        try:
            with open(dist.locate_file(text_path), "r", encoding="utf-8",
                      errors="replace") as f:
                content = f.read().strip()
        except OSError:
            continue
        if content:
            parts.append((os.path.basename(text_path), content))

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][1]
    # Some projects ship several (e.g. a dual licence, or LICENSE + NOTICE).
    return "\n\n".join(f"----- {name} -----\n\n{content}" for name, content in parts)


def main() -> int:
    dists = _bundled_distributions()

    lines = [
        "# Third-party notices",
        "",
        "DualSense Quick Menu bundles the third-party packages listed below.",
        "Each remains under its own licence, held by its own copyright holders —",
        "the terms in [LICENSE](LICENSE) cover this application's own code only.",
        "",
        "**PySide6 (Qt for Python) is licensed under the LGPL v3.** Among other",
        "things that licence requires that you be able to replace the Qt",
        "libraries this app uses. The distributed build is a one-folder layout",
        "with the Qt DLLs as separate files in `_internal\\PySide6\\`, so they can",
        "be swapped for compatible versions.",
        "",
        f"Generated by `tools/make_notices.py` from the packages installed for",
        f"the build. {len(dists)} packages.",
        "",
        "## Summary",
        "",
        "| Package | Version | Licence |",
        "| --- | --- | --- |",
    ]
    for dist in dists:
        meta = dist.metadata
        lines.append(
            f"| {meta['Name']} | {meta.get('Version', '?')} | {_licence_name(dist)} |"
        )

    lines += ["", "## Full licence texts", ""]
    missing = []
    for dist in dists:
        meta = dist.metadata
        lines.append(f"### {meta['Name']} {meta.get('Version', '')}".rstrip())
        lines.append("")
        lines.append(f"Licence: {_licence_name(dist)}")
        home = meta.get("Home-page") or meta.get("Project-URL") or ""
        if home:
            lines.append("")
            lines.append(f"Project: {home}")
        text = _licence_text(dist)
        if text:
            lines += ["", "```", text, "```", ""]
        else:
            missing.append(meta["Name"])
            lines += [
                "",
                "_This package's wheel does not ship a licence file. See the "
                "project's own repository for its full terms._",
                "",
            ]

    with open(_OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    print(f"wrote {_OUT}")
    print(f"  {len(dists)} packages")
    if missing:
        print(f"  no bundled licence file for: {', '.join(missing)}")
        print("  (listed with a pointer instead — check these by hand if you")
        print("   are about to publish)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
