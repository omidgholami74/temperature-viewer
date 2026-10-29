"""Keep the firmware running when no computer is attached.

A board on a charger has no console: nobody presses Ctrl-D after a crash and
nobody sees a traceback. Two safety nets stand in for that person:

* the hardware watchdog (``machine.WDT``) reboots the board when the sampling
  loop stops feeding it, so a hung SPI read or a stuck network call cannot
  freeze the monitor for a whole weekend;
* ``run_forever`` catches whatever a session raises, waits, and starts a fresh
  session instead of dropping into a REPL that is not there.

A reboot empties the RAM queue, but every sample already POSTed is on the
server, and NTP timestamps keep the history in place. ``reset_cause()`` makes
the reboot visible on the console the next time the board is plugged into a PC.
"""

import time

try:
    import machine
except ImportError:  # the host tests load this file without a board
    machine = None


# Named in machine.* on every port that reports a reset cause.
_RESET_NAMES = (
    "PWRON_RESET",
    "HARD_RESET",
    "WDT_RESET",
    "DEEPSLEEP_RESET",
    "SOFT_RESET",
)

# Waking up every 250 ms keeps the watchdog fed during the restart delay.
_FEED_STEP_MS = 250


def _sleep_ms(milliseconds):
    """MicroPython has sleep_ms, CPython (the host tests) only has sleep."""
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def reset_cause():
    """Why the board booted: ``'WDT_RESET'``, ``'PWRON_RESET'``, or None."""
    if machine is None or not hasattr(machine, "reset_cause"):
        return None

    try:
        cause = machine.reset_cause()
    except Exception:
        return None

    for name in _RESET_NAMES:
        if getattr(machine, name, None) == cause:
            return name

    return "cause %r" % (cause,)


class Watchdog:
    """The hardware watchdog, fed by whichever loop is making progress."""

    def __init__(self, seconds, log=print):
        self.wdt = None

        try:
            seconds = float(seconds or 0)
        except (TypeError, ValueError):
            seconds = 0.0

        if seconds <= 0:
            return  # WATCHDOG_S = 0 disables it

        if machine is None or not hasattr(machine, "WDT"):
            return

        try:
            self.wdt = machine.WDT(timeout=int(seconds * 1000))
        except Exception as error:  # a build without the WDT module
            log("# watchdog off: %s" % (error,))

    def feed(self):
        """Reset the countdown; a no-op when the watchdog is not armed."""
        if self.wdt is None:
            return

        try:
            self.wdt.feed()
        except Exception:
            pass


def run_forever(session, watch=None, delay_s=5, log=print):
    """Run ``session(watch)`` and restart it after a crash, with a backoff.

    ``KeyboardInterrupt`` is passed through, so a normal Ctrl-C in a REPL still
    stops the firmware. Any other exception is logged and the session is
    rebuilt: on a board with nobody watching, that is the only way back.
    """
    restarts = 0

    while True:
        try:
            session(watch)
            reason = "the session returned"
        except KeyboardInterrupt:
            raise
        except Exception as error:
            reason = "%s: %s" % (error.__class__.__name__, error)

        restarts += 1
        wait_s = delay_s * (2 ** min(restarts - 1, 4))

        log("# firmware stopped: %s" % (reason,))
        log("# restarting in %d s (restart %d)" % (wait_s, restarts))

        remaining = int(wait_s * 1000)

        while remaining > 0:
            step = _FEED_STEP_MS if remaining > _FEED_STEP_MS else remaining
            _sleep_ms(step)
            remaining -= step

            if watch is not None:
                watch.feed()
