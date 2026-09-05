# Verification for diagnostics.report() — the text behind Settings ->
# "Copy diagnostics".
#
#   .venv\Scripts\python.exe tests\verify_diagnostics.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before anything reads it, so this never touches the real
# %APPDATA%\DualSenseQuickMenu\.
#
# The leak checks matter most: this text is designed to be pasted into a group
# chat, so a regression that starts including the client ID, the OAuth token, or
# the tester's Windows username is a privacy bug, not a formatting one.

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

import settings

_TMP = tempfile.mkdtemp(prefix="dsqm_diag_")
settings.data_dir = lambda: _TMP

import logs

logs.setup()

import diagnostics
import version

CLIENT_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
settings.set_spotify_client_id(CLIENT_ID)

# ------------------------------------------------------------------- contents
print("\n[report contents]")
text = diagnostics.report()
check("names the app and version", version.VERSION in text)
check("says whether it's packaged", "Build:" in text)
check("reports the OS", "Windows" in text)
check("reports Spotify as configured", "client ID saved" in text)
check("names the log file", "log.txt" in text)

print("\n[no controller probe registered]")
check("says the state is unknown rather than guessing",
      "unknown" in [l for l in text.splitlines() if l.startswith("Controller:")][0])

print("\n[with a controller probe]")
diagnostics.register_controller_probe(lambda: (True, 88))
text = diagnostics.report()
check("reports a connected controller and battery",
      "Controller: connected, battery 88%" in text,
      f"(got {[l for l in text.splitlines() if l.startswith('Controller:')]})")

diagnostics.register_controller_probe(lambda: (False, None))
check("reports a disconnected controller",
      "Controller: not connected" in diagnostics.report())

# A probe that raises must not take the whole report down — the report is what
# someone reaches for when things are already broken.
diagnostics.register_controller_probe(lambda: 1 / 0)
text = diagnostics.report()
check("survives a probe that raises", "Controller: unknown" in text,
      f"(got {[l for l in text.splitlines() if l.startswith('Controller:')]})")
diagnostics.register_controller_probe(lambda: (False, None))

# ---------------------------------------------------------------- leak checks
print("\n[does not leak secrets]")
text = diagnostics.report()
check("the client ID is never included", CLIENT_ID not in text)

# Write a token file like the real one, then confirm none of it appears.
token_path = os.path.join(_TMP, "spotify_token.json")
with open(token_path, "w", encoding="utf-8") as f:
    f.write('{"access_token": "SECRETTOKENVALUE", "refresh_token": "SECRETREFRESH"}')
text = diagnostics.report()
check("the access token is never included", "SECRETTOKENVALUE" not in text)
check("the refresh token is never included", "SECRETREFRESH" not in text)

# The home directory must be redacted out of log excerpts.
home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
logs.get("verify.diag").error("Failed while reading %s", os.path.join(home, "secret.txt"))
text = diagnostics.report()
check("the home directory is redacted from log lines",
      home not in text and "%USERPROFILE%" in text,
      f"(home={home!r})")

# ------------------------------------------------------------------- problems
print("\n[recent problems]")
for i in range(12):
    logs.get("verify.noise").warning("synthetic problem number %s", i)
text = diagnostics.report()
problem_lines = [l for l in text.splitlines() if "synthetic problem" in l]
check("caps how many problems are listed", len(problem_lines) <= 6,
      f"(listed {len(problem_lines)})")
check("says how many were omitted", "last 6 of" in text,
      f"(got {[l for l in text.splitlines() if 'arnings' in l]})")
check("keeps the newest ones", "synthetic problem number 11" in text)
check("drops the oldest ones", "synthetic problem number 0" not in text)

# A traceback's continuation lines shouldn't each become a listed "problem".
try:
    raise ValueError("a deliberate traceback")
except ValueError:
    logs.get("verify.tb").exception("something with a traceback")
text = diagnostics.report()
check("a traceback contributes one line, not many",
      len([l for l in text.splitlines() if "something with a traceback" in l]) == 1)
check("traceback body is not listed as problems",
      "a deliberate traceback" not in text)

# A very long message gets truncated so one line can't flood the paste.
logs.get("verify.long").error("x" * 400)
text = diagnostics.report()
longest = max(len(l) for l in text.splitlines())
check("long lines are truncated", longest < 200, f"(longest was {longest})")

print("\n[clean log]")
# Both sources have to be empty: the report reads the in-memory records first
# and only falls back to parsing the file when there are none.
empty = tempfile.mkdtemp(prefix="dsqm_diag_empty_")
_real_log_path = logs.log_path
logs.log_path = lambda: os.path.join(empty, "log.txt")
logs.forget_recent()
check("says so when there are no problems",
      "No warnings or errors logged." in diagnostics.report())

# With nothing in memory but something in the file - what a crash and restart
# looks like - the file is still read, and the report says where it came from.
with open(os.path.join(empty, "log.txt"), "w", encoding="utf-8") as f:
    f.write("2026-09-05 10:00:00 ERROR    prev.run: something failed last time\n")
# Cleared again immediately before the call: building a report reads the
# cached Spotify token, and this suite's token file is deliberately malformed,
# so the previous report left a record of its own behind.
logs.forget_recent()
text = diagnostics.report()
check("falls back to the log file when nothing was logged this run",
      "something failed last time" in text)
check("says the fallback lines are older", "from before this run" in text,
      f"(got {[l for l in text.splitlines() if 'arnings' in l]})")
logs.log_path = _real_log_path

print("\n[a secret that reached the log file]")
# The checks above plant secrets in the *token file*, which the report never
# reads. Nothing planted one in a log record, and that was the gap: an
# external review logged a token, and it came out in the report intact,
# because redaction only rewrote the home directory. Log text is arbitrary and
# not under this app's control, so this is a safety net rather than a promise
# - but the shapes below are the ones that actually turn up.
log = logs.get("probe")
log.error("request failed access_token=PLANTED_ACCESS_TOKEN user=someone")
log.error("Authorization: Bearer PLANTED_BEARER_VALUE")
log.warning('refresh_token: "PLANTED_REFRESH_VALUE"')
for handler in logs.logging.getLogger().handlers:
    handler.flush()
text = diagnostics.report()
for planted in ("PLANTED_ACCESS_TOKEN", "PLANTED_BEARER_VALUE", "PLANTED_REFRESH_VALUE"):
    check(f"{planted} does not survive into the report", planted not in text)
check("and the surrounding log line is still there to read",
      "request failed" in text, "(redaction shouldn't blank the whole message)")

print("\n[structured fields]")
# The fixed part of the report is an allowlist, and that is the property worth
# pinning: a field appears because this list names it, not because some string
# happened to end up in the text.
data = diagnostics.report_data()
labels = [label for label, _value in data["environment"] + data["state"]]
check("the fields are the ones the report promises",
      labels == ["Build", "System", "Python", "Controller", "Global hotkey",
                 "Spotify", "Log file"],
      f"(got {labels})")
check("problems come back as fields, not as a formatted line",
      all(set(p) == {"when", "level", "source", "summary"} for p in data["problems"]),
      f"(got {data['problems'][:1]})")
check("the rendered report is built from that structure",
      data["title"] in diagnostics.report())

print("\n[secrets that arrive as log arguments]")
# The layer free-text matching can't reach. A log record keeps its message
# template apart from its arguments, so an argument the template itself calls
# a token can be dropped without anyone having to guess its shape - even when
# the value looks like an ordinary word.
logs.forget_recent()
log = logs.get("probe")
log.error("refresh rejected, token %s is stale", "PlantedShortArg")
log.warning("saving %s failed with password %s", "settings.json", "hunter2")
log.error("fetching %s failed",
          "https://api.spotify.com/v1/me?access_token=PLANTEDURLTOKEN")
log.error("unexpected value %s", "A1b2C3d4E5f6G7h8J9k0L1")
log.error("could not read %s", os.path.join(home, "Documents", "notes.txt"))
log.warning("contact %s about it", "someone@example.com")
text = diagnostics.report()

check("an argument the template calls a token is dropped",
      "PlantedShortArg" not in text and "is stale" in text,
      "(a plain-looking word, caught only because of the words in front of it)")
check("an argument the template calls a password is dropped",
      "hunter2" not in text)
check("the argument next to it is kept", "settings.json" in text)
check("a URL's query string is dropped", "PLANTEDURLTOKEN" not in text)
check("but the URL itself survives", "api.spotify.com" in text)
check("a long opaque value is dropped", "A1b2C3d4E5f6G7h8J9k0L1" not in text)
check("a home path in an argument is rewritten",
      home not in text and "notes.txt" in text)
check("an e-mail address is dropped", "someone@example.com" not in text)

print("\n[what an exception contributes]")
logs.forget_recent()
try:
    raise FileNotFoundError(os.path.join(home, "a-real-path-nobody-should-see.txt"))
except FileNotFoundError:
    logs.get("probe").exception("Couldn't open the settings file")
text = diagnostics.report()
check("the exception type is reported", "FileNotFoundError" in text)
check("the exception's own message is not",
      "a-real-path-nobody-should-see" not in text,
      "(an exception message routinely carries the value that caused it)")

finish()
