# The single place the app's version is defined. Everything else reads it from
# here: the log's start banner, the Settings window footer, --selftest, and the
# .exe's own file properties (generated in DualSenseQuickMenu.spec).
#
# This exists for testing rounds. With several people on builds handed out over
# weeks, a bug report that doesn't say which build it came from costs more time
# than it saves — you end up chasing something already fixed.
#
# Bump VERSION before handing out a build. The suffix convention:
#   0.1.0-alpha.N  — the close-contact alpha (a couple of testers)
#   0.2.0-beta.N   — the wider, less technical beta
#   1.0.0          — public

APP_NAME = "DualSense Quick Menu"
VERSION = "0.1.0-alpha.1"

# Shown in the Settings window footer. The GPL asks that an interactive program
# make its licensing visible at the interface, not only in the LICENSE file.
COPYRIGHT = "Copyright (C) 2026 shihaab453"
LICENSE_NAME = "GPL v3"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"


def numeric_version() -> tuple:
    """VERSION's leading numbers as a 4-tuple, for the Windows version
    resource — that format has no room for a "-alpha.1" suffix, so the
    pre-release counter becomes the fourth component (0.1.0-alpha.1 ->
    (0, 1, 0, 1)) rather than being dropped."""
    core, _, pre = VERSION.partition("-")
    parts = [int(p) for p in core.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    build = 0
    if pre:
        trailing = pre.rsplit(".", 1)[-1]
        if trailing.isdigit():
            build = int(trailing)
    return tuple(parts[:3]) + (build,)
