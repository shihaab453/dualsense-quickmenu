# Verification for workers.py — the background-work plumbing every panel's
# loading now goes through.
#
#   .venv\Scripts\python.exe tests\verify_workers.py
#
# Exits non-zero if anything fails.
#
# This one deliberately uses real threads rather than running jobs inline. The
# panel suites stub submit() to run jobs on the spot so their assertions stay
# ordered, which is the right trade there but means nothing in them would
# notice if the threading itself broke. The three properties the UI actually
# leans on are all here: work leaves the Qt thread, results come back onto it,
# and a result the user has already navigated past is dropped instead of
# landing on top of what replaced it.

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from _harness import check, finish

from PySide6.QtCore import QCoreApplication, QEventLoop

from workers import Commands, Loader, Worker

app = QCoreApplication(sys.argv)
MAIN_THREAD = threading.get_ident()


def pump(until, timeout=3.0):
    """Run the Qt event loop until `until()` is true or we give up. Queued
    signals from a worker thread are only delivered while the loop runs, so
    every wait in this file has to go through here rather than sleep()."""
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 10)
    return until()


print("\n[Worker: one thread, in order]")
worker = Worker("test")
order = []
threads = set()
for n in range(5):
    worker.submit(lambda n=n: (order.append(n), threads.add(threading.get_ident())))
check("every job ran", pump(lambda: len(order) == 5), f"(got {order})")
check("they ran in submission order", order == [0, 1, 2, 3, 4], f"(got {order})")
check("all on one thread, and not the caller's",
      len(threads) == 1 and MAIN_THREAD not in threads,
      f"(got {len(threads)} thread(s))")

print("\n[Worker: a job that raises doesn't kill the thread]")
after = []
worker.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom: job")))
worker.submit(lambda: after.append("still running"))
check("the next job still ran", pump(lambda: after == ["still running"]), f"(got {after})")

print("\n[Loader: the result comes back on the Qt thread]")
loader = Loader(worker.submit, "test")
got = []
loader.start(lambda: ("value", threading.get_ident()),
             lambda value, error: got.append((value, error, threading.get_ident())))
check("the callback ran", pump(lambda: got), f"(got {got})")
if got:
    (value, ident), error, callback_thread = got[0]
    check("it carries the work's return value", value == "value", f"(got {value!r})")
    check("no error is reported for a job that worked", error is None, f"(got {error!r})")
    check("the work itself ran off the Qt thread", ident != MAIN_THREAD)
    check("but the callback runs on it", callback_thread == MAIN_THREAD)

print("\n[Loader: a failing job reports the exception rather than raising]")
got = []
loader.start(lambda: (_ for _ in ()).throw(ValueError("boom: work")),
             lambda value, error: got.append((value, error)))
check("the callback still ran", pump(lambda: got), f"(got {got})")
if got:
    value, error = got[0]
    check("value is None when the work failed", value is None, f"(got {value!r})")
    check("the exception itself is handed over", isinstance(error, ValueError),
          f"(got {error!r})")
    check("with its message intact", str(error) == "boom: work", f"(got {str(error)!r})")

print("\n[Loader: a superseded result is dropped]")
# The real scenario: open playlist A, press Circle, open playlist B. A's
# answer must never land under B's heading. A gate the first job blocks on
# guarantees the ordering this test is about, instead of hoping a sleep is
# long enough.
gate = threading.Event()
delivered = []
loader.start(lambda: (gate.wait(3), "first")[1],
             lambda value, error: delivered.append(value))
loader.start(lambda: "second", lambda value, error: delivered.append(value))
gate.set()
check("the newer result was delivered", pump(lambda: "second" in delivered),
      f"(got {delivered})")
# The first job was already running when it was superseded, so it finishes;
# what matters is that its answer is thrown away rather than shown.
app.processEvents(QEventLoop.AllEvents, 50)
check("the superseded result never reached the UI", "first" not in delivered,
      f"(got {delivered})")

print("\n[Loader: logout-style cancellation drops queued and active work]")
cancel_loader = Loader(worker.submit, "cancel-test")
queue_gate = threading.Event()
queue_drained = threading.Event()
ran = []
worker.submit(lambda: queue_gate.wait(3))
cancel_loader.start(lambda: ran.append("queued"), lambda _value, _error: ran.append("callback"))
cancel_loader.cancel()
worker.submit(queue_drained.set)
queue_gate.set()
check("cancelled queued work never runs", pump(queue_drained.is_set) and not ran,
      f"(got {ran})")

active_started = threading.Event()
active_gate = threading.Event()
active_drained = threading.Event()
delivered = []
cancel_loader.start(
    lambda: (active_started.set(), active_gate.wait(3), "old account")[2],
    lambda value, _error: delivered.append(value),
)
check("sanity: work was active before cancellation", active_started.wait(1))
cancel_loader.cancel()
worker.submit(active_drained.set)
active_gate.set()
check("the active job finishes in the worker", pump(active_drained.is_set))
app.processEvents(QEventLoop.AllEvents, 50)
check("its late result never reaches the UI", delivered == [], f"(got {delivered})")

print("\n[Loader: superseded work is skipped before it runs]")
# Cheaper than dropping it on arrival: a request the user has already moved
# past shouldn't spend a network round trip at all.
gate = threading.Event()
ran = []
blocker = Loader(worker.submit, "test")
blocker.start(lambda: gate.wait(3), lambda value, error: None)
for n in range(3):
    blocker.start(lambda n=n: ran.append(n), lambda value, error: None)
gate.set()
check("only the newest queued job actually ran",
      pump(lambda: ran == [2], timeout=3.0), f"(got {ran})")

print("\n[Commands: every one runs, in order]")
# The distinction this class exists for. A Loader drops work the user has
# navigated past, which is right for "fetch the library" and catastrophic for
# "press Next": the second press would silently eat the first.
ran = []
gate = threading.Event()
commands = Commands(worker.submit, "test")
commands.run(lambda: (gate.wait(3), ran.append("first"))[1])
commands.run(lambda: ran.append("second"))
commands.run(lambda: ran.append("third"))
gate.set()
check("no command is dropped for a later one",
      pump(lambda: len(ran) == 3), f"(got {ran})")
check("and they run in the order they were pressed",
      ran == ["first", "second", "third"], f"(got {ran})")

print("\n[Commands: logout-style cancellation drops queued presses]")
queue_gate = threading.Event()
queue_drained = threading.Event()
ran = []
worker.submit(lambda: queue_gate.wait(3))
commands.run(lambda: ran.append("old-account command"))
commands.cancel_all()
worker.submit(queue_drained.set)
queue_gate.set()
check("a queued command is cancelled at the session boundary",
      pump(queue_drained.is_set) and ran == [], f"(got {ran})")

print("\n[Commands: each one reports back]")
results = []
commands.run(lambda: "done", lambda value, error: results.append((value, error)))
check("a result is delivered", pump(lambda: results), f"(got {results})")
if results:
    check("carrying the return value", results[0][0] == "done",
          f"(got {results[0][0]!r})")
    check("with no error", results[0][1] is None, f"(got {results[0][1]!r})")

results = []
commands.run(lambda: (_ for _ in ()).throw(ValueError("boom: command")),
             lambda value, error: results.append((value, error)))
check("a failing command still reports", pump(lambda: results), f"(got {results})")
if results:
    check("handing over the exception", isinstance(results[0][1], ValueError),
          f"(got {results[0][1]!r})")

print("\n[Commands: a failure doesn't stop the queue]")
after = []
commands.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
commands.run(lambda: after.append("still going"))
check("the next command still ran", pump(lambda: after == ["still going"]),
      f"(got {after})")

finish()
