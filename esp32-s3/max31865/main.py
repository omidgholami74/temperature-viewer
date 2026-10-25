"""ESP32-S3 + 4x MAX31865 (PT100/PT1000), Wi-Fi, straight to the PHP API.

Same shape as the MAX31855 firmware: a fitment probe is read every 100 ms, the
``ms,f1,f2,f3,f4`` line goes out on USB serial for Thonny / Serial Studio /
``serial-web``, and every frame is POSTed to ``/api/ingest`` from the board
itself — no PC collector in the middle.

Wiring (see README.md):

    SCK  GPIO14   shared clock
    MOSI GPIO11   shared data out   (the MAX31855 does not use MOSI)
    MISO GPIO12   shared data in
    CS   GPIO15, 16, 17, 18         one per MAX31865 board
    3V3  and GND  on every board

Copy ``common/config.example.py`` to ``config.py``, edit it, then upload
``config.py``, ``wifi.py``, ``ingest.py``, ``max31865.py`` and this ``main.py``
to the board root and run ``main.py``.
"""

import time

from machine import Pin, SPI

import config
import wifi
from ingest import Ingest
from max31865 import MAX31865

# ==========================================
# ESP32-S3 + 4x MAX31865
# ==========================================

SCK_PIN = 14
MOSI_PIN = 11
MISO_PIN = 12

# Chip Select pins for four furnaces
CS_PINS = [15, 16, 17, 18]

SAMPLE_INTERVAL_MS = 100

# --- probe settings ------------------------------------------------------
# R_REF is the reference resistor on the breakout: 430 ohm for a PT100,
# 4300 ohm for a PT1000. Set WIRE_MODE to match how the probe is wired.
R_REF = 430.0
R_NOMINAL = 100.0   # PT100 = 100.0, PT1000 = 1000.0
WIRE_MODE = 3       # 2, 3 or 4
FILTER_HZ = 50      # Iran's mains frequency: keep the 50 Hz notch enabled

CSV_HEADER = "ms,furnace1,furnace2,furnace3,furnace4"
FAULT_SENTINEL = -1000.0

# SPI configuration (a real MOSI line is required here). The MAX31865 samples
# on the falling clock edge, which is SPI mode 1: CPOL 0, CPHA 1.
spi = SPI(
    1,
    baudrate=1000000,
    polarity=0,
    phase=1,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(SCK_PIN),
    mosi=Pin(MOSI_PIN),
    miso=Pin(MISO_PIN),
)

sensors = [
    MAX31865(spi, cs=pin, r_ref=R_REF, r_nominal=R_NOMINAL, wire_mode=WIRE_MODE, filter_hz=FILTER_HZ)
    for pin in CS_PINS
]


def read_channel(sensor):
    """Return ``(temperature_or_None, fault_bits)`` for one RTD channel."""
    try:
        return sensor.read()
    except Exception:
        return None, 8


def csv_value(temperature):
    return FAULT_SENTINEL if temperature is None else temperature


# ==========================================
# Main loop
# ==========================================

def main():
    for sensor in sensors:
        sensor.begin()

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
            for sensor in sensors:
                temperature, fault = read_channel(sensor)
                temperatures.append(temperature)
                faults.append(fault)

            elapsed_time = time.ticks_diff(time.ticks_ms(), start_tick)

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

            next_tick = time.ticks_add(next_tick, SAMPLE_INTERVAL_MS)
            delay = time.ticks_diff(next_tick, time.ticks_ms())

            if delay > 0:
                time.sleep_ms(delay)
            else:
                next_tick = time.ticks_ms()
    except KeyboardInterrupt:
        pass
    finally:
        print("# stopping, flushing %d samples" % (len(ingest.pending),))
        ingest.stop()


main()
