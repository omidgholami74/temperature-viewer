"""ESP32-S3 + 3x MAX31855, Wi-Fi, straight to the PHP API.

Three furnaces are read every 100 ms; the ``ms,f1,f2,f3`` line goes out on USB
serial for Thonny / Serial Studio / ``serial-web``, and every frame is POSTed
to ``/api/ingest`` from the board itself — no PC collector in the middle.

The API frame is still four channels wide. A board with three probes sends the
fourth slot as ``null`` with no fault bit, so an empty slot is not reported as a
broken probe. Add a fourth pin to ``CS_PINS`` (and an offset) to use it.

Wiring (see README.md):

    SCK  GPIO14   shared clock
    MISO GPIO12   shared data in
    CS   GPIO15, 16, 17             one per MAX31855 board
    3V3  and GND  on every board   (the MAX31855 has no MOSI)

Copy ``common/config.example.py`` to ``config.py``, edit it, then upload
``config.py``, ``wifi.py``, ``ingest.py``, ``supervisor.py``, ``ota.py`` and this
``main.py`` to the board root and run ``main.py``. With those at the root the
firmware runs on its own from a USB charger and updates itself from the API.

The watchdog reboots a hung loop, ``supervisor.run_forever`` restarts a crashed
session and ``ota.py`` checks the server for a newer bundle in the background.
"""

import time

from machine import Pin, SPI

import config
import supervisor
import wifi
from ingest import Ingest

try:
    import ota
except ImportError:  # a board flashed before OTA existed still samples
    ota = None

# ==========================================
# ESP32-S3 + 3x MAX31855
# ==========================================

SCK_PIN = 14
MISO_PIN = 12

# Chip Select pins, one per furnace. Add a fourth pin here to grow the build;
# every list below follows this length.
CS_PINS = [15, 16, 17]

SAMPLE_INTERVAL_MS = 100

# The wire format is fixed at four channels by the API. Unused slots are sent
# as null (temperature) and 0 (fault), so they read as "no probe installed".
API_CHANNELS = 4

CSV_HEADER = "ms,furnace1,furnace2,furnace3"
FAULT_SENTINEL = -1000.0  # CSV value for a channel that reported a fault

# Fixed calibration offset per furnace, in °C, in CS_PINS order: furnace 1 is
# CS 15, furnace 2 is CS 16, and so on. The value is added to every reading of
# that channel — both in the CSV line and in the uploaded sample — which is
# what corrects a probe that is consistently off (cold-junction error, a probe
# touching the wall, a long lead). Calibrate against a reference thermometer at
# working temperature and leave a channel at 0.0 for no correction.
# A faulted channel is not offset: it stays -1000.00 on the serial line and
# null in the upload, so the dashboard still shows it as broken.
CHANNEL_OFFSETS = [0.0, 0.0, 0.0]

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

def session(watch):
    """One sampling session: connect, then sample until something breaks."""
    wlan = wifi.connect(config, watch=watch)

    clock_ok = False

    if wlan.isconnected() and getattr(config, "NTP_ENABLED", True):
        clock_ok = wifi.sync_time(config)

    watch.feed()

    ingest = Ingest(config)

    # Upload from a thread: a request (or a TLS handshake) takes 0.5-1 s here,
    # and the sampling loop must stay on the 100 ms grid while it happens.
    if ingest.start():
        print("# upload runs in a background thread")

    updater = None

    if ota is not None:
        try:
            updater = ota.Updater(config)
        except Exception as error:
            print("# OTA: disabled: %s" % (error,))

        if updater is not None:
            if updater.start():
                print("# OTA: %s, checking every %d s" % (
                    updater.channel, updater.check_s))
            else:
                print("# OTA: waiting for the next boot")

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
    last_health = start_tick
    confirmed = False

    try:
        while True:
            temperatures = []
            faults = []

            # Read every installed furnace
            for index, cs in enumerate(chip_selects):
                temperature, fault = read_channel(cs)

                if temperature is not None:
                    # A short list in CHANNEL_OFFSETS is treated as 0.0 rather
                    # than an IndexError, which would restart the board.
                    offset = CHANNEL_OFFSETS[index] if index < len(CHANNEL_OFFSETS) else 0.0
                    temperature += offset

                temperatures.append(temperature)
                faults.append(fault)

            watch.feed()

            # Increment time: 100, 200, 300, ... (measured, not assumed, so a
            # slow upload cannot make the time column drift)
            elapsed_time = time.ticks_diff(time.ticks_ms(), start_tick)

            # Time_ms, Furnace1, Furnace2, ...
            if echo:
                print(",".join(
                    [str(elapsed_time)] + ["%.2f" % csv_value(value) for value in temperatures]
                ))

            at_ms = None if epoch_base_ms is None else epoch_base_ms + elapsed_time

            # Pad the frame to the API's four channels: an unused slot is null
            # with no fault, not a broken probe.
            payload_t = temperatures + [None] * (API_CHANNELS - len(temperatures))
            payload_f = faults + [0] * (API_CHANNELS - len(faults))

            ingest.add(elapsed_time, payload_t, payload_f, at_ms)

            now = time.ticks_ms()

            # A dropped link is retried on its own; sampling never stops.
            if not wlan.isconnected() and time.ticks_diff(now, last_net_check) >= retry_ms:
                last_net_check = now
                wifi.reconnect(wlan, config, watch=watch)

                if wlan.isconnected() and not clock_ok and getattr(config, "NTP_ENABLED", True):
                    if wifi.sync_time(config):
                        clock_ok = True
                        epoch_base_ms = int(time.time()) * 1000 - time.ticks_diff(now, start_tick)

            # The uploader is a thread, so it can die while the loop runs on.
            # On a charger nobody would start it again: the loop does.
            if time.ticks_diff(now, last_health) >= 15000:
                last_health = now

                if not ingest.alive():
                    print("# upload thread stopped, starting it again")

                    if not ingest.start():
                        print("# upload unavailable, sending inline")

                if updater is not None and not updater.alive():
                    print("# OTA thread stopped, starting it again")
                    updater.start()

            # A staged update is applied at the next boot; flush what is still
            # queued first so the update does not cost a buffer of samples.
            if updater is not None and updater.ready:
                print("# OTA: %s staged, restarting" % (updater.ready,))
                ingest.stop()
                updater.stop()
                ota.reboot()

            # A freshly applied bundle keeps a backup until the firmware has
            # survived a minute, which is what arms the boot-loop rollback.
            if not confirmed and time.ticks_diff(now, start_tick) >= 60000:
                confirmed = True

                if ota is not None:
                    ota.confirm()

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

        if updater is not None:
            updater.stop()


def main():
    cause = supervisor.reset_cause()

    if cause is not None:
        print("# boot: reset cause %s" % (cause,))

    watch = supervisor.Watchdog(getattr(config, "WATCHDOG_S", 30))

    # Nobody is watching a board that runs from a charger, so a crash restarts
    # the session instead of leaving an idle REPL behind.
    supervisor.run_forever(session, watch)


main()
