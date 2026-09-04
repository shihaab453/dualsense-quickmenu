# Verification for logs.py — that failures actually reach the log file, from
# every place this app can lose them.
#
#   .venv\Scripts\python.exe tests\verify_logging.py
#
# Exits non-zero if anything fails. Redirects settings.data_dir() to a temp
# folder before logs.setup() runs, so this never writes to the real
# %APPDATA%\DualSenseQuickMenu\log.txt or touches the Spotify token beside it.

import logging
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _harness import check, finish

import settings

_TMP = tempfile.mkdtemp(prefix="dsqm_logverify_")
settings.data_dir = lambda: _TMP

import logs

def log_text() -> str:
    with open(logs.log_path(), "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------- setup
print("\n[setup]")
logs.setup()
check("log file created", os.path.exists(logs.log_path()))
check("log path is inside the settings dir", logs.log_path().startswith(_TMP))
import version

# The banner has to carry the version: a log pasted into a bug report is
# useless if it doesn't say which build produced it.
check("start banner written", "start" in log_text())
check("start banner names the version", version.VERSION in log_text(),
      f"(looking for {version.VERSION!r})")
check("setup() is idempotent", (logs.setup() or True))
check(
    "no duplicate handlers after a second setup()",
    len([h for h in logging.getLogger().handlers
         if isinstance(h, logging.handlers.RotatingFileHandler)]) == 1,
)

# ------------------------------------------------------- module-level logging
print("\n[module loggers]")
logs.get("verify.demo").exception_count = 0
try:
    raise ValueError("deliberate test failure")
except ValueError:
    logs.get("verify.demo").exception("something broke while %s", "testing")
text = log_text()
check("message reaches the file", "something broke while testing" in text)
check("traceback reaches the file", "ValueError: deliberate test failure" in text)
check("logger name recorded", "verify.demo" in text)

# --------------------------------------------------------------- excepthooks
print("\n[excepthooks]")
try:
    raise RuntimeError("uncaught on main thread")
except RuntimeError:
    # Hand the live exc_info straight to the hook — the same triple Python
    # would pass if this had gone uncaught.
    sys.excepthook(*sys.exc_info())
check("sys.excepthook logs uncaught errors",
      "uncaught on main thread" in log_text())
check("sys.excepthook marks them critical", "CRITICAL" in log_text())


def exploding_thread():
    raise RuntimeError("uncaught on background thread")


t = threading.Thread(target=exploding_thread, name="exploder")
t.start()
t.join()
text = log_text()
check("threading.excepthook logs background failures",
      "uncaught on background thread" in text)
check("the failing thread is named", "exploder" in text)

# ------------------------------------------------ pythonw has no stderr
print("\n[pythonw simulation]")
# Under pythonw.exe sys.stderr is None. A StreamHandler pointed at None raises
# on every record, so setup() must not add one — this is the exact condition
# the app actually ships in.
real_stderr = sys.stderr
try:
    sys.stderr = None
    logs._configured = False
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
    logs.setup()
    stream_handlers = [
        h for h in logging.getLogger().handlers
        if type(h) is logging.StreamHandler
    ]
    check("no StreamHandler added when stderr is None", stream_handlers == [])
    logs.get("verify.pythonw").error("logged with no stderr present")
    check("logging still works with no stderr",
          "logged with no stderr present" in log_text())
finally:
    sys.stderr = real_stderr

# --------------------------------------------- controller reconnect flood guard
print("\n[controller reconnect flood guard]")
# With no controller plugged in, init() fails every _RECONNECT_INTERVAL
# seconds forever. That's a normal state, so it must be logged once per
# disconnected episode rather than once per attempt.
import controller


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


capture = _Capture()
logging.getLogger("controller").addHandler(capture)


class _AlwaysFailingDualSense:
    connected = False

    def init(self):
        raise OSError("no device (simulated)")

    def close(self):
        pass


controller.pydualsense = _AlwaysFailingDualSense
controller._RECONNECT_INTERVAL = 0.02

listener = controller.DualSenseListener()
listener.start()
time.sleep(0.6)   # ~30 failed init() attempts at this interval
listener.stop()

warnings = [r for r in capture.records if r.levelno >= logging.WARNING]
check(
    "repeated reconnect attempts log exactly once",
    len(warnings) == 1,
    f"(got {len(warnings)} warnings from ~30 attempts)",
)
check(
    "the one warning explains what to check",
    warnings and "DS4Windows" in warnings[0].getMessage(),
)

# ------------------------------------------ controller read-failure recovery
print("\n[controller read-failure recovery]")
# A read can fail while the controller remains physically present. The listener
# must close that broken instance and return to its existing outer reconnect
# loop, rather than letting its background thread die.
capture.records.clear()


class _State:
    ps = DpadUp = DpadDown = DpadLeft = DpadRight = cross = circle = False


class _Battery:
    Level = 77


class _ReadFailureDualSense:
    instances = []

    def __init__(self):
        self.number = len(self.instances)
        self.connected = False
        self.closed = False
        self.instances.append(self)

    def init(self):
        self.connected = True

    @property
    def state(self):
        if self.number == 0:
            raise OSError("simulated HID read failure")
        return _State()

    @property
    def battery(self):
        return _Battery()

    def close(self):
        self.closed = True
        self.connected = False


controller.pydualsense = _ReadFailureDualSense
connections = []
listener = controller.DualSenseListener(on_connection_change=connections.append)
listener.start()
deadline = time.monotonic() + 1
while len(connections) < 3 and time.monotonic() < deadline:
    time.sleep(0.01)
listener.stop()

check(
    "a read failure creates a replacement controller",
    len(_ReadFailureDualSense.instances) >= 2,
    f"(created {len(_ReadFailureDualSense.instances)} instances)",
)
check(
    "a failed read reports disconnect then reconnect",
    connections[:3] == [True, False, True],
    f"(got {connections})",
)
check(
    "the failed controller is closed before reconnecting",
    _ReadFailureDualSense.instances and _ReadFailureDualSense.instances[0].closed,
)
check(
    "the read failure is logged",
    any("read failed" in record.getMessage() for record in capture.records),
)

# ---------------------------------------------------------------- rotation
print("\n[rotation]")
check("rotating handler is capped", any(
    getattr(h, "maxBytes", 0) > 0 for h in logging.getLogger().handlers
))

finish()
