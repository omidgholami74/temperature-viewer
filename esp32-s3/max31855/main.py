"""ESP32-S3 + 4x MAX31855, Wi-Fi, straight to the PHP API.

The original sketch is kept as it was — same pins, same ``read_temperature``
and same ``ms,f1,f2,f3,f4`` line on USB serial — with two things added:

* the board joins a Wi-Fi network at boot (``wifi.py``), and
* every frame is POSTed to ``/api/ingest`` itself (``ingest.py``), so the PC
  collector in ``collector/`` is no longer needed.

Copy ``common/config.example.py`` to ``config.py``, edit it, then upload
``config.py``, ``wifi.py``, ``ingest.py`` and this ``main.py`` to the board root
and run ``main.py``. See README.md in this folder for the wiring.
"""

import time

from machine import Pin, SPI

from common import config
from common import wifi
from common.ingest import Ingest

# ==========================================
# ESP32-S3 + 4x MAX31855
# ==========================================

SCK_PIN = 14
MISO_PIN = 12

# Chip Select pins for four furnaces
CS_PINS = [15, 16, 17, 18]

SAMPLE_INTERVAL_MS = 100

CSV_HEADER = "ms,furnace1,furnace2,furnace3,furnace4"
FAULT_SENTINEL = -1000.0  # CSV value for a channel that reported a fault

# SPI configuration
spi = SPI(
    1,
    baudrate=1000000,
    polarity=0,
    phase=0,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(SCK_PIN),
    miso=Pin(MISO_PIN),
)

# Initialize chip-select pins
chip_selects = [
    Pin(pin, Pin.OUT, value=1)
    for pin in CS_PINS
]


def read_temperature(cs):
    """Read thermocouple temperature in Celsius."""

    try:
        cs.value(1)
        cs.value(0)
        time.sleep_us(2)

        data = spi.read(4)

        cs.value(1)

        if data is None or len(data) != 4:
            return float("nan")

        # Combine four bytes into a 32-bit value
        raw = (
            (data[0] << 24)
            | (data[1] << 16)
            | (data[2] << 8)
            | data[3]
        )

        # Check MAX31855 general fault flag
        if raw & 0x00010000:
            return float("nan")

        # Extract signed 14-bit thermocouple temperature
        temp_raw = (raw >> 18) & 0x3FFF

        if temp_raw & 0x2000:
            temp_raw -= 0x4000

        return temp_raw * 0.25

    except Exception:
        cs.value(1)
        return float("nan")


def read_channel(cs):
    """Same read as read_temperature, plus the fault mask the API wants.

    Returns ``(temperature_or_None, fault_bits)`` with the API's bits:
    1 open thermocouple, 2 short to GND, 4 short to VCC, 8 read error.
    """

    try:
        cs.value(1)
        cs.value(0)
        time.sleep_us(2)

        data = spi.read(4)

        cs.value(1)

        if data is None or len(data) != 4:
            return None, 8

        raw = (
            (data[0] << 24)
            | (data[1] << 16)
            | (data[2] << 8)
            | data[3]
        )

        fault = 0

        if raw & 0x1:      # OC  open circuit
            fault |= 1
        if raw & 0x2:      # SCG short to ground
            fault |= 2
        if raw & 0x4:      # SCV short to VCC
            fault |= 4
        if raw & 0x10000:  # general fault
            fault |= 8

        if fault:
            return None, fault

        temp_raw = (raw >> 18) & 0x3FFF

        if temp_raw & 0x2000:
            temp_raw -= 0x4000

        return temp_raw * 0.25, 0

    except Exception:
        cs.value(1)
        return None, 8


def csv_value(temperature):
    return FAULT_SENTINEL if temperature is None else temperature


# ==========================================
# Main loop
# ==========================================

def main():
    wlan = wifi.connect(config)

    clock_ok = False

    if wlan.isconnected() and getattr(config, "NTP_ENABLED", True):
        clock_ok = wifi.sync_time(config)

    ingest = Ingest(config)

    # Upload from a thread: a request (or a TLS handshake) takes 0.5-1 s here,
    # and the sampling loop must stay on the 100 ms grid while it happens.
    if ingest.start():
        print("# upload runs in a background thread")
    echo = bool(getattr(config, "SERIAL_ECHO", True))

    if not clock_ok:
        print("# no clock: samples are stamped by the server on arrival")

    if echo:
        print(CSV_HEADER)

    start_tick = time.ticks_ms()
    epoch_base_ms = int(time.time()) * 1000 if clock_ok else None
    next_tick = start_tick
    retry_ms = int(float(getattr(config, "WIFI_RETRY_S", 15)) * 1000)
    last_net_check = start_tick
    last_report = start_tick

    try:
        while True:
            temperatures = []
            faults = []

            # Read all four furnaces
            for cs in chip_selects:
                temperature, fault = read_channel(cs)
                temperatures.append(temperature)
                faults.append(fault)

            # Increment time: 100, 200, 300, ... (measured, not assumed, so a
            # slow upload cannot make the time column drift)
            elapsed_time = time.ticks_diff(time.ticks_ms(), start_tick)

            # Print five comma-separated numeric columns:
            # Time_ms, Furnace1, Furnace2, Furnace3, Furnace4
            if echo:
                print("{},{:.2f},{:.2f},{:.2f},{:.2f}".format(
                    elapsed_time,
                    csv_value(temperatures[0]),
                    csv_value(temperatures[1]),
                    csv_value(temperatures[2]),
                    csv_value(temperatures[3]),
                ))

            at_ms = None if epoch_base_ms is None else epoch_base_ms + elapsed_time
            ingest.add(elapsed_time, temperatures, faults, at_ms)

            now = time.ticks_ms()

            # A dropped link is retried on its own; sampling never stops.
            if not wlan.isconnected() and time.ticks_diff(now, last_net_check) >= retry_ms:
                last_net_check = now
                wifi.reconnect(wlan, config)

                if wlan.isconnected() and not clock_ok and getattr(config, "NTP_ENABLED", True):
                    if wifi.sync_time(config):
                        clock_ok = True
                        epoch_base_ms = int(time.time()) * 1000 - time.ticks_diff(now, start_tick)

            if time.ticks_diff(now, last_report) >= 60000:
                last_report = now
                print("# %s" % (ingest.stats(),))

            # Wait for the next sample
            next_tick = time.ticks_add(next_tick, SAMPLE_INTERVAL_MS)
            delay = time.ticks_diff(next_tick, time.ticks_ms())

            if delay > 0:
                time.sleep_ms(delay)
            else:
                # Uploads took longer than one frame: resynchronise instead of
                # burning through a backlog of missed slots.
                next_tick = time.ticks_ms()
    except KeyboardInterrupt:
        pass
    finally:
        print("# stopping, flushing %d samples" % (len(ingest.pending),))
        ingest.stop()


main()
