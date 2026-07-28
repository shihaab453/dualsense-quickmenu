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
VERSION = "0.1.0-alpha.1.1"

# Shown in the Settings window footer, so the app states its authorship and
# terms at the interface rather than only in a file someone has to go find.
COPYRIGHT = "Copyright (C) 2026 shihaab453"
LICENSE_NAME = "Licence"
LICENSE_URL = "https://polyformproject.org/licenses/strict/1.0.0"


def numeric_version() -> tuple:
    """VERSION's numbers as a 4-tuple, for the Windows version resource.

    That format is four integers and nothing else, so a "-alpha.1.1" suffix has
    to be folded into the one component the core version leaves spare. The
    pre-release's numeric parts are packed as `first * 100 + second`:

        0.1.0-alpha.1    -> (0, 1, 0, 100)
        0.1.0-alpha.1.1  -> (0, 1, 0, 101)
        0.2.0-beta.3     -> (0, 2, 0, 300)
        1.0.0            -> (1, 0, 0, 0)

    Packing rather than just taking the last number, because two builds that
    differ only in a second-level counter would otherwise get an identical
    numeric version — which defeats the point of stamping builds so a tester's
    report says which one they're on. The second counter is capped at 99 so
    ordering can't invert (alpha.1.150 must not outrank alpha.2)."""
    core, _, pre = VERSION.partition("-")
    parts = [int(p) for p in core.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)

    counters = [int(p) for p in pre.split(".") if p.isdigit()] if pre else []
    build = 0
    if counters:
        build = min(counters[0], 655) * 100 + (min(counters[1], 99) if len(counters) > 1 else 0)
    return tuple(parts[:3]) + (build,)
