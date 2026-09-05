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
empty = tempfile.mkdtemp(prefix="dsqm_diag_empty_")
_real_log_path = logs.log_path
logs.log_path = lambda: os.path.join(empty, "log.txt")
check("says so when there are no problems",
      "No warnings or errors logged." in diagnostics.report())
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

finish()
