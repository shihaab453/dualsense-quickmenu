# Builds the text behind Settings -> "Copy diagnostics".
#
# Why this exists: log.txt is only useful if it reaches the person who can read
# it, and the path to that is currently right-click tray -> Settings -> Open log
# folder -> find the file -> attach it somewhere. A tester who is mildly
# annoyed that something didn't work will not do five steps. One button that
# puts a short report on the clipboard makes the ask "click this and paste it",
# which people actually do.
#
# The output is written to be pasted into a chat message, which shapes how it
# is built. Three layers, in order of how much each can promise:
#
#   1. The fixed fields are an allowlist. Version, OS, controller state, hotkey
#      state, Spotify state: every one of them is a value this file chooses to
#      report. The Spotify client ID and OAuth token appear as booleans —
#      "saved" / "logged in" — never as values. Nothing reaches this part of
#      the report unless it is named here, so nothing can leak into it.
#
#   2. The recent warnings and errors come from logs.recent_problems(), which
#      keeps each record's message *template* apart from its *arguments*. The
#      template is a literal written in this repo; the arguments are runtime
#      values, and they are where a token or a real name would come from. So
#      arguments are sanitised one at a time, and an argument the template
#      itself calls a token or a password is dropped without being looked at.
#      That is the one thing free-text pattern matching cannot do.
#
#   3. The rendered line then goes through _sanitize() anyway, as a net under
#      the case layer 2 can't see: a message built with an f-string, where the
#      value is already baked into what looks like a template. Home directory,
#      credential-shaped text, URL query strings, e-mail addresses and long
#      opaque values all come out. This layer is pattern matching over text the
#      app does not fully control, so it is a net and not a promise.
#
# Which is why Settings shows the whole report in an editable box before
# anything reaches the clipboard (settings_window.DiagnosticsPreview). A person
# reading it is the last layer, and the only one that can catch a shape nobody
# thought to write a pattern for.

import os
import platform
import re
import sys

import hotkey
import logs
import version
from actions import spotify_client as sp

log = logs.get(__name__)

# Matches a log line's header. Traceback continuation lines deliberately don't
# match, so a multi-line exception contributes only its one summary line.
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2} (WARNING|ERROR|CRITICAL)\s+(\S+): (.*)$"
)

_MAX_PROBLEMS = 6
_MAX_LINE_LENGTH = 150

# Set by main.py once the controller listener exists. Left as None when the
# report is generated from somewhere that has no listener (e.g. the Settings
# window opened before startup finished), in which case the controller line
# says so rather than guessing.
_controller_probe = None
_hotkey_probe = None


def register_controller_probe(probe) -> None:
    """probe() should return (connected: bool, battery_percent: int | None)."""
    global _controller_probe
    _controller_probe = probe


def register_hotkey_probe(probe) -> None:
    """probe() should return bool: whether the global hotkey (hotkey.py)
    actually registered with Windows. A silent registration failure — most
    likely another app already holding Ctrl+Alt+Space — would otherwise leave
    someone wondering why the hotkey "does nothing"; this is how that shows
    up in a diagnostics report instead of nowhere."""
    global _hotkey_probe
    _hotkey_probe = probe


# The safety net over text this app does not fully control (layer 3 in the
# note at the top of this file). Not a promise: anything genuinely secret
# should never be logged in the first place. An external review put a token
# into a log record and watched it come out in a diagnostics report intact,
# which is what these exist to stop.

# Anything shaped like a URL. The query string is the interesting part: an
# OAuth redirect carries `?code=` and sometimes `?access_token=`, and an image
# URL's path is of no diagnostic value either way.
_URL_RE = re.compile(r"""\b((?:https?|ftp)://[^\s"'<>\[\]]+)""")

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Key names that mean "what follows is a credential", used two ways: to redact
# `key=value` in free text, and to decide that a log argument introduced by one
# of these words should not be printed at all.
_CREDENTIAL_WORDS = (
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
    r"client[_-]?id|code[_-]?verifier|authorization|api[_-]?key|apikey|"
    r"password|passwd|credential|secret|token|bearer"
)

_SECRET_PATTERNS = (
    # key=value and key: value, for the usual credential-ish key names
    re.compile(
        r"(?i)\b(" + _CREDENTIAL_WORDS + r")"
        r"(\s*[=:]\s*|\s+)"
        r"([\"']?)([A-Za-z0-9._~+/\-]{8,})\3"
    ),
    # Anything that looks like a bare JWT, which no log line needs to carry.
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),
)

# A long unbroken run that mixes letters and digits: an access token, a Spotify
# ID, a session GUID, a hash. Nothing a person needs to read comes out this
# shape, and it is what catches a secret that arrives with no key name in front
# of it. Deliberately blunt — losing a long identifier from a report costs a
# follow-up question, keeping a token costs an account.
_OPAQUE_RE = re.compile(
    r"(?<![A-Za-z0-9._~+/-])"
    r"(?=[A-Za-z0-9._~+/-]*[A-Za-z])(?=[A-Za-z0-9._~+/-]*\d)"
    r"[A-Za-z0-9._~+/-]{20,}"
    r"(?![A-Za-z0-9._~+/-])"
)

# Matched against the template text immediately before a placeholder, to decide
# whether that argument is a credential. Narrower than _CREDENTIAL_WORDS on
# purpose: "client id" in front of a placeholder is usually describing one
# rather than printing one, and that line is more useful with it than without.
_CREDENTIAL_INTRO_RE = re.compile(
    r"(?i)(token|secret|password|passwd|credential|api[_-]?key|apikey|"
    r"bearer|authorization|code[_-]?verifier)"
)
_INTRO_WINDOW = 48

# %-style placeholders in a logging template, so each argument can be matched
# to the words in front of it. `%%` is a literal percent and takes no argument.
_PLACEHOLDER_RE = re.compile(
    r"%[#0\- +]*[0-9*]*(?:\.[0-9*]+)?[hlL]?([diouxXeEfFgGcrsa%])"
)


def _strip_url_query(match: "re.Match") -> str:
    url = match.group(1)
    for separator in ("?", "#"):
        head, sep, tail = url.partition(separator)
        if sep and tail:
            url = head + sep + "[redacted]"
    return url


def _sanitize(text: str) -> str:
    """Strips what we can recognise as private from text bound for the report:
    the user's home directory (a Windows username is usually a real name), URL
    query strings, e-mail addresses, credential-shaped key/value pairs and long
    opaque values.

    Read the limitation before relying on this. It is pattern matching over
    arbitrary text, so it catches the shapes below and cannot promise anything
    about a secret in a shape it has not seen. It is the third of the three
    layers described at the top of this file, and the weakest of them: the
    report is "checked, not guaranteed", which is why a person reads it before
    it is copied."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home:
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.IGNORECASE)

    # URLs are handled first and then held out of the rest of the pass. Their
    # query string is dropped here; what remains is a long run of letters,
    # digits and slashes, which is exactly the shape _OPAQUE_RE exists to
    # delete. Left in, "https://api.spotify.com/v1/me" came out as
    # "https:[redacted]", which throws away the one part worth reading.
    urls = []

    def hold(match: "re.Match") -> str:
        urls.append(_strip_url_query(match))
        # NUL and digits only, so nothing below can match the placeholder.
        return f"\x00{len(urls) - 1}\x00"

    text = _URL_RE.sub(hold, text)
    text = _EMAIL_RE.sub("[redacted e-mail]", text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 4:
            text = pattern.sub(r"\1\2\3[redacted]\3", text)
        else:
            text = pattern.sub("[redacted]", text)
    text = _OPAQUE_RE.sub("[redacted]", text)
    for index, url in enumerate(urls):
        text = text.replace(f"\x00{index}\x00", url)
    return text


def _template_prefixes(template: str) -> list:
    """The template text in front of each placeholder, one entry per argument.

    `"refreshing %s with token %s"` gives `["refreshing ", " with token "]`,
    which is what tells us the second argument must never be printed and the
    first one probably can be."""
    prefixes = []
    last = 0
    for match in _PLACEHOLDER_RE.finditer(template):
        if match.group(1) == "%":  # a literal percent, not an argument
            continue
        prefixes.append(template[last:match.start()])
        last = match.end()
    return prefixes


def _sanitize_args(template: str, args):
    """Sanitise a log record's arguments before they are formatted into it.

    Layer 2 from the note at the top: an argument introduced by a credential
    word is dropped without being inspected, and every other string argument
    goes through the text sanitiser. Numbers are left as they are, so that a
    `%d` placeholder still formats."""
    if isinstance(args, dict):
        return {
            key: (
                "[redacted]"
                if isinstance(value, str) and _CREDENTIAL_INTRO_RE.search(key)
                else _sanitize(value) if isinstance(value, str) else value
            )
            for key, value in args.items()
        }
    if not args:
        return ()
    prefixes = _template_prefixes(template)
    out = []
    for index, value in enumerate(args):
        if not isinstance(value, str):
            out.append(value)
            continue
        intro = prefixes[index][-_INTRO_WINDOW:] if index < len(prefixes) else ""
        out.append("[redacted]" if _CREDENTIAL_INTRO_RE.search(intro) else _sanitize(value))
    return tuple(out)


def _spotify_line() -> str:
    try:
        configured = sp.is_configured()
    except Exception:
        return "unknown (couldn't check)"
    if not configured:
        return "no client ID saved"
    try:
        # Deliberately the local, non-refreshing check: this report is what
        # someone reaches for when things are already broken, so it must not
        # be able to hang on a network call of its own.
        return "client ID saved, logged in" if sp.has_cached_token() else (
            "client ID saved, not logged in"
        )
    except Exception:
        return "client ID saved, login state unknown"


def _controller_line() -> str:
    if _controller_probe is None:
        return "unknown (app not fully started)"
    try:
        connected, battery = _controller_probe()
    except Exception:
        log.exception("Controller probe failed while building diagnostics")
        return "unknown (probe failed)"
    if not connected:
        return "not connected"
    return f"connected, battery {battery}%" if battery is not None else "connected"


def hotkey_registered() -> bool | None:
    """Whether the global hotkey actually registered with Windows, or None if
    that isn't knowable yet (app still starting up). Public — settings_window
    uses this too, to show live status next to the hotkey it can't itself
    control (that lives in main.py's HotkeyListener)."""
    if _hotkey_probe is None:
        return None
    try:
        return bool(_hotkey_probe())
    except Exception:
        log.exception("Hotkey probe failed")
        return None


def _hotkey_line() -> str:
    registered = hotkey_registered()
    if registered is None:
        return "unknown (app not fully started)"
    return (
        f"active ({hotkey.DISPLAY_NAME} opens/closes the overlay)"
        if registered
        else f"NOT active — {hotkey.DISPLAY_NAME} is likely already bound by another app"
    )


def _problem_from_record(record: dict) -> dict:
    """One buffered log record, turned into a line a stranger can safely read.

    This is where layers 2 and 3 from the note at the top meet: the arguments
    are sanitised as separate values, formatted into the template, and the
    result is then sanitised again in case the "template" was really an
    f-string with a value already inside it."""
    template = record.get("template") or ""
    args = _sanitize_args(template, record.get("args") or ())
    if args:
        try:
            summary = template % args
        except Exception:
            # A template and its arguments that don't agree: log.py never
            # formatted them either, so print what we have rather than nothing.
            values = args.values() if isinstance(args, dict) else args
            summary = template + " " + " ".join(str(value) for value in values)
    else:
        summary = template
    if record.get("exc_type"):
        # The exception type, never its message: "failed to save (OSError)"
        # says what a reader needs, and an exception's message routinely
        # carries the path or value that caused it.
        summary = f"{summary} ({record['exc_type']})"
    return {
        "when": record.get("when", ""),
        "level": record.get("level", ""),
        "source": record.get("source", ""),
        "summary": _trim(_sanitize(summary.strip())),
    }


def _trim(message: str) -> str:
    if len(message) > _MAX_LINE_LENGTH:
        return message[:_MAX_LINE_LENGTH].rstrip() + "…"
    return message


def _problems_from_log_file() -> tuple:
    """The fallback for when the in-memory buffer is empty, which is what a
    crash and restart looks like — and that is exactly when someone reaches
    for this report. The file has no template/argument split left in it, so
    these lines get the text sanitiser only, and the report says so."""
    try:
        with open(logs.log_path(), "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return [], 0

    problems = []
    for line in text.splitlines():
        match = _LOG_LINE_RE.match(line)
        if not match:
            continue
        date, time_hm, level, source, message = match.groups()
        problems.append({
            "when": f"{date} {time_hm}",
            "level": level,
            "source": source,
            "summary": _trim(_sanitize(message.strip())),
        })
    return problems, len(problems)


def _recent_problems() -> tuple:
    """(problems, total, from_this_run).

    Structured records from this run when there are any, and last run's log
    file when there aren't. Newest last, capped at _MAX_PROBLEMS."""
    records = logs.recent_problems()
    if records:
        problems = [_problem_from_record(record) for record in records]
        return problems[-_MAX_PROBLEMS:], len(problems), True
    problems, total = _problems_from_log_file()
    return problems[-_MAX_PROBLEMS:], total, False


def report_data() -> dict:
    """The report as fields rather than as text.

    This is the allowlist: the two field lists below are the complete set of
    facts the report states about a machine, and adding to them is a decision
    someone has to make on purpose. report() renders this; the preview in
    Settings shows what report() returned. Keeping the structure means the
    privacy rules are enforced somewhere other than inside a format string."""
    try:
        pyside_version = __import__("PySide6").__version__
    except Exception:
        pyside_version = "unknown"

    # Gathered before the fields below, and that ordering matters: reading
    # Spotify's cached token can itself log a warning, and a report that lists
    # the errors it caused by being generated is a confusing thing to read.
    problems, total, from_this_run = _recent_problems()
    return {
        "title": f"{version.APP_NAME} {version.VERSION}",
        "environment": [
            ("Build", "packaged" if getattr(sys, "frozen", False) else "from source"),
            ("System", f"{platform.platform()} ({platform.machine()})"),
            ("Python", f"{platform.python_version()} / PySide6 {pyside_version}"),
        ],
        "state": [
            ("Controller", _controller_line()),
            ("Global hotkey", _hotkey_line()),
            ("Spotify", _spotify_line()),
            ("Log file", _sanitize(logs.log_path())),
        ],
        "problems": problems,
        "problem_total": total,
        "problems_from_this_run": from_this_run,
    }


def report() -> str:
    """The full diagnostics text, ready to be shown to the user and then
    copied. Nothing calls this and copies straight to the clipboard — see
    settings_window.DiagnosticsPreview for why."""
    data = report_data()

    lines = [data["title"]]
    lines += [f"{label}: {value}" for label, value in data["environment"]]
    lines.append("")
    lines += [f"{label}: {value}" for label, value in data["state"]]
    lines.append("")

    problems = data["problems"]
    total = data["problem_total"]
    if not problems:
        lines.append("No warnings or errors logged.")
    else:
        shown = len(problems)
        header = (
            f"Recent warnings/errors (last {shown} of {total}):"
            if total > shown
            else f"Warnings/errors ({total}):"
        )
        if not data["problems_from_this_run"]:
            # Worth saying out loud: these came out of the log file rather than
            # from this run, so they are older, and they had the weaker of the
            # two redaction paths applied to them.
            header = header[:-1] + ", from before this run:"
        lines.append(header)
        lines.extend(
            f"  {problem['when']} {problem['level']} {problem['source']}: "
            f"{problem['summary']}"
            for problem in problems
        )

    return "\n".join(lines)
