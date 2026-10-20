"""Furnace monitor firmware for Pico + 4x MAX31855 over USB serial.

Use this when the Pico is not a Pico W (or WiFi is not available). It prints
one JSON sample per line at 10 Hz; run bridge.py on the PC to forward those
lines to the PHP API.

Copy config.example.py to config.py, edit the pins, then run this file.
"""

import time

try:
    import ujson as json
except ImportError:  # pragma: no cover - CPython fallback
    import json

try:
    import config
except ImportError:
    raise SystemExit("config.py is missing. Copy config.example.py to config.py and edit it.")

from hardware import build_spi
from max31855 import MAX31855Array
from sampler import Sampler


def main():
    sampler = Sampler(MAX31855Array(build_spi(), config.CS_PINS), config.SAMPLE_INTERVAL_MS)
    print("# furnace-monitor serial stream, one JSON sample per line")

    while True:
        sampler.wait()
        print(json.dumps(sampler.sample()))


try:
    main()
except KeyboardInterrupt:
    print("Stopped by user")
