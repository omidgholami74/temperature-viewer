"""Furnace monitor firmware for a plain Raspberry Pi Pico + 4x MAX31855.

This Pico has no WiFi and MicroPython for the board ships no HTTP client, so
the firmware never talks to the server and imports no networking module. Each
sample is printed as one JSON line on USB serial and, optionally, appended to a
log file on the Pico's own flash.

A PC runs ``collector/collector.py``. It writes the serial lines into a file on
the computer and uploads that file to the PHP API in batches.

Copy config.example.py to config.py, edit it, upload this folder to the Pico
(Thonny: save each file to the board) and run main.py.
"""

import os
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


def rotate_if_full(path, max_bytes):
    """Keep the newest data: move a full log aside and start a fresh file."""
    try:
        size = os.stat(path)[6]
    except OSError:
        return

    if size < max_bytes:
        return

    try:
        os.remove(path + ".old")
    except OSError:
        pass

    try:
        os.rename(path, path + ".old")
        print("# log rotated ->", path + ".old")
    except OSError as error:
        print("# rotation failed:", error)


def open_log():
    """Open the log for appending and report how much is already in it."""
    rotate_if_full(config.LOG_FILE, config.MAX_LOG_BYTES)
    handle = open(config.LOG_FILE, "a")

    try:
        size = os.stat(config.LOG_FILE)[6]
    except OSError:
        size = 0

    return handle, size


def main():
    sensors = MAX31855Array(build_spi(), config.CS_PINS)
    sampler = Sampler(sensors, config.SAMPLE_INTERVAL_MS)

    log, log_bytes = open_log() if config.LOG_TO_FLASH else (None, 0)

    print("# furnace monitor: 4 channels every {} ms".format(config.SAMPLE_INTERVAL_MS))
    print("# serial: one JSON sample per line, log file:", config.LOG_FILE if log else "off")

    try:
        while True:
            sampler.wait()
            sample = sampler.sample()
            line = json.dumps(sample)

            if config.SERIAL_ECHO:
                print(line)

            if log is not None:
                try:
                    log.write(line + "\n")
                    log.flush()
                    log_bytes += len(line) + 1
                except OSError as error:
                    print("# log write failed:", error)
                    log = None

                if log is not None and log_bytes >= config.MAX_LOG_BYTES:
                    try:
                        log.close()
                    except OSError:
                        pass

                    log, log_bytes = open_log()
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        if log is not None:
            try:
                log.close()
            except OSError:
                pass


main()
