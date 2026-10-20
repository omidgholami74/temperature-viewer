"""One-shot hardware check for the Pico + 4x MAX31855.

Run this on the board (Thonny: Run current script) when main.py prints
samples with ``null`` temperatures. It lists the files on the Pico, shows the
configuration in use and prints three raw frames per channel, including the
chip's own internal temperature, so wiring and probe faults are easy to see.

The temperature value is only meaningful when the fault bits are zero; an
open thermocouple (bit 1) leaves the chip talking but with no usable reading.
"""

import os
import time

try:
    import config
except ImportError:
    print("ERROR: config.py is not on the Pico.")
    print("Save config.example.py as config.py on the board, edit it, and run this again.")
    raise SystemExit

from hardware import build_spi
from max31855 import MAX31855Array

FAULT_NAMES = (
    (0x01, "open thermocouple"),
    (0x02, "short to GND"),
    (0x04, "short to VCC"),
    (0x08, "SPI read error"),
)


def describe(fault):
    if fault == 0:
        return "ok"

    return ", ".join(name for bit, name in FAULT_NAMES if fault & bit) or "unknown"


def decode(raw):
    """Return (thermocouple °C or None, internal °C or None, fault bits)."""
    if raw in (0x00000000, 0xFFFFFFFF):
        return None, None, 0x08

    internal = (raw >> 4) & 0x0FFF

    if internal & 0x800:
        internal -= 0x1000

    faults = raw & 0x07
    probe = (raw >> 18) & 0x3FFF

    if probe & 0x2000:
        probe -= 0x4000

    return (None if faults else probe * 0.25), internal * 0.0625, faults


def main():
    print("# furnace monitor check")
    print("files:", sorted(os.listdir()))
    print(
        "SPI {} SCK GP{} MISO GP{} CS {} at {} Hz".format(
            config.SPI_ID,
            config.SCK_PIN,
            config.MISO_PIN,
            config.CS_PINS,
            config.SPI_BAUD_RATE,
        )
    )

    if getattr(config, "LOG_TO_FLASH", False):
        print("log file:", config.LOG_FILE, "| max", config.MAX_LOG_BYTES, "bytes")

    sensors = MAX31855Array(build_spi(), config.CS_PINS).sensors

    for round_number in range(3):
        print("-- round", round_number + 1)

        for index, sensor in enumerate(sensors, start=1):
            try:
                raw = sensor.read_raw()
            except OSError as error:
                print("channel {} SPI error: {}".format(index, error))
                continue

            probe, internal, faults = decode(raw)
            print(
                "channel {} raw=0x{:08X} probe={} internal={} fault={} ({})".format(
                    index,
                    raw,
                    "--" if probe is None else "{:.2f}".format(probe),
                    "--" if internal is None else "{:.2f}".format(internal),
                    faults,
                    describe(faults),
                )
            )

        time.sleep_ms(100)

    print("# done: 0x00000000 means no chip answers on that CS pin")


main()
