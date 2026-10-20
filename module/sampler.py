"""Drift-free sampling loop shared by the WiFi and USB firmware."""

import time


class Sampler:
    """Reads all channels on a fixed cadence.

    The next slot is calculated from the previous slot rather than from
    ``sleep``, so the sample interval stays at 100 ms even when an HTTP
    request or a serial write takes a few milliseconds.
    """

    def __init__(self, sensors, interval_ms=100):
        if interval_ms < 20:
            raise ValueError("interval_ms must be at least 20 ms")

        self.sensors = sensors
        self.interval_ms = interval_ms
        self.started_at = time.ticks_ms()
        self.next_slot = self.started_at

    def wait(self):
        """Sleep until the next slot. Returns True when the cadence is intact."""
        now = time.ticks_ms()
        remaining = time.ticks_diff(self.next_slot, now)

        if remaining > 0:
            time.sleep_ms(remaining)
            return True

        if -remaining > self.interval_ms * 2:
            # A blocking upload fell far behind: continue from now instead of
            # replaying every missed slot.
            self.next_slot = now
            return False

        return True

    def sample(self):
        """Read all channels and return an API sample dict."""
        taken_at = time.ticks_ms()
        temperatures, faults = self.sensors.read_all()
        self.next_slot = time.ticks_add(self.next_slot, self.interval_ms)

        return {
            "u": time.ticks_diff(taken_at, self.started_at),
            "t": temperatures,
            "f": faults,
        }
