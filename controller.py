# Why HID instead of XInput:
# XInput is Microsoft's gamepad abstraction API. It intentionally exposes only a
# standardised button set for cross-platform compatibility — the PS (home) button
# is not part of that set and is completely invisible to XInput. Direct HID access
# reads the raw USB report that the DualSense sends, which includes every button
# the hardware exposes, including PS.

import threading
import time

from pydualsense import pydualsense

import logs

log = logs.get(__name__)

# pydualsense state attribute -> name used by the rest of the app
_BUTTONS = {
    "ps": "ps",
    "DpadUp": "up",
    "DpadDown": "down",
    "DpadLeft": "left",
    "DpadRight": "right",
    "cross": "cross",
    "circle": "circle",
}

# D-pad directions auto-repeat while held (like a held keyboard key), so
# holding up keeps raising the volume instead of needing repeated taps.
_REPEATING = {"up", "down", "left", "right"}
_REPEAT_DELAY = 0.40     # held this long before repeating kicks in
_REPEAT_INTERVAL = 0.15  # then repeats this often

_POLL_INTERVAL = 0.01      # 100 Hz — snappy without measurable CPU cost
_RECONNECT_INTERVAL = 2.0  # how often to look for the controller when absent


class DualSenseListener:
    """Watches the DualSense in a background thread and fires callbacks.

    on_button(name):            press events: ps, up, down, left, right, cross, circle
    on_connection_change(bool): controller plugged in / unplugged

    Callbacks run on the background thread. GUI code must hop back to the
    main thread before touching widgets (main.py does this with Qt signals).
    """

    def __init__(self, on_button=None, on_connection_change=None):
        self._on_button = on_button
        self._on_connection_change = on_connection_change
        self._running = False
        self._thread = None
        self.battery_percent = None  # None while disconnected
        # Current (not just edge-triggered) button state — e.g. so the menu
        # can tell "is Cross currently held" while a D-pad press also comes
        # in, for a hold-Cross-for-fine-adjustment modifier.
        self.held = {name: False for name in _BUTTONS.values()}

    def start(self):
        self._running = True
        # Daemon thread: dies automatically when the main program exits,
        # so the app can never hang on shutdown because of us.
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    # ---- everything below runs on the background thread ----

    def _run(self):
        # Outer loop: (re)connect forever. Inner loop (_poll): read buttons
        # until the controller goes away, then come back here and retry.
        # This is what makes unplugging/replugging mid-session painless.
        # Logged only on the transition into "can't find it", not on every
        # retry: with no controller plugged in, init() fails every 2 seconds
        # forever, and that's a normal state — logging each attempt would bury
        # everything else in the file.
        logged_absent = False

        while self._running:
            ds = pydualsense()
            try:
                ds.init()  # raises if no controller is plugged in
            except Exception:
                if not logged_absent:
                    logged_absent = True
                    log.warning(
                        "No DualSense found — will keep retrying every %ss. If one "
                        "is plugged in over USB, another tool (DS4Windows, Steam's "
                        "PlayStation Controller Support) may have claimed it.",
                        _RECONNECT_INTERVAL,
                        exc_info=True,
                    )
                self._sleep(_RECONNECT_INTERVAL)
                continue

            logged_absent = False
            log.info("DualSense connected")
            self._emit_connection(True)
            try:
                self._poll(ds)
            finally:
                self.battery_percent = None
                self.held = {name: False for name in _BUTTONS.values()}
                log.info("DualSense disconnected")
                self._emit_connection(False)
                try:
                    ds.close()
                except Exception:
                    # Device already gone — nothing left to close, and this is
                    # the expected path when the cable is pulled.
                    log.debug("close() on an already-gone DualSense", exc_info=True)

    def _poll(self, ds):
        prev = {name: False for name in _BUTTONS.values()}
        pressed_since = {}  # name -> when it went down (for auto-repeat)
        last_repeat = {}

        # pydualsense flips ds.connected to False when the cable is pulled.
        while self._running and ds.connected:
            state = ds.state
            now = time.monotonic()
            for attr, name in _BUTTONS.items():
                down = bool(getattr(state, attr))
                if down and not prev[name]:
                    # Rising edge: fire once on the press, not while held.
                    self._emit_button(name)
                    pressed_since[name] = now
                    last_repeat[name] = now
                elif down and name in _REPEATING:
                    held = now - pressed_since.get(name, now)
                    since_last = now - last_repeat.get(name, now)
                    if held >= _REPEAT_DELAY and since_last >= _REPEAT_INTERVAL:
                        self._emit_button(name)
                        last_repeat[name] = now
                prev[name] = down
                self.held[name] = down

            self.battery_percent = ds.battery.Level
            time.sleep(_POLL_INTERVAL)

    def _sleep(self, seconds):
        # Sleep in small slices so stop() never waits the full interval.
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.1)

    def _emit_button(self, name):
        if self._on_button:
            self._on_button(name)

    def _emit_connection(self, connected):
        if self._on_connection_change:
            self._on_connection_change(connected)
