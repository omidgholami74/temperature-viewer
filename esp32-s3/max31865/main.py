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
``config.py``, ``wifi.py``, ``ingest.py``, ``supervisor.py``, ``ota.py``,
``device.py``, ``settings.py``, ``max31865.py`` and this ``main.py`` to the
board root and run ``main.py``. Settings and firmware updates come from the
admin panel on demand; there is no periodic update check.

With those files at the board root the firmware also runs on its own from a USB
charger: ``main.py`` starts at every power-up, the watchdog reboots a hung loop
and ``supervisor.run_forever`` restarts a session that crashed.
"""

import time

from machine import Pin, SPI

import config
import settings as settings_module
import supervisor
import wifi
from ingest import Ingest
from max31865 import MAX31865

try:
    import ota
except ImportError:  # a board flashed before OTA existed still samples
    ota = None

# ==========================================
# ESP32-S3 + 4x MAX31865
# ==========================================

SCK_PIN = 14
MOSI_PIN = 11
MISO_PIN = 12

# Chip Select pins for up to four furnaces. FURNACE_CHANNELS is the number
# actually installed; the default is three, so an empty fourth slot is never
# read as a phantom probe. Set FURNACE_CHANNELS = 4 in config.py for four.
CS_GPIO_PINS = [15, 16, 17, 18]
FURNACE_CHANNELS = max(
    1,
    min(int(getattr(config, "FURNACE_CHANNELS", 3)), len(CS_GPIO_PINS)),
)
CS_PINS = CS_GPIO_PINS[:FURNACE_CHANNELS]

SAMPLE_INTERVAL_MS = 100
API_CHANNELS = 4

# --- probe settings ------------------------------------------------------
# R_REF is the reference resistor on the breakout: 430 ohm for a PT100,
# 4300 ohm for a PT1000. Set WIRE_MODE to match how the probe is wired.
R_REF = 430.0
R_NOMINAL = 100.0   # PT100 = 100.0, PT1000 = 1000.0
WIRE_MODE = 3       # 2, 3 or 4
FILTER_HZ = 50      # Iran's mains frequency: keep the 50 Hz notch enabled

CSV_HEADER = "ms," + ",".join("furnace%d" % (number,) for number in range(1, len(CS_PINS) + 1))
FAULT_SENTINEL = -1000.0

# Live settings: frame interval, heartbeat and per-channel enabled/offset.
# The admin panel can change them at runtime; /settings.json survives a reboot.
runtime = settings_module.Settings(
    defaults={
        "sample_ms": SAMPLE_INTERVAL_MS,
        "heartbeat_s": getattr(config, "HEARTBEAT_S", 15),
    },
    channels=[
        {"channel": index + 1, "enabled": True, "offset": 0.0}
        for index in range(len(CS_PINS))
    ],
    log=print,
)

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

def session(watch):
    """One sampling session: connect, then sample until something breaks."""
    for sensor in sensors:
        sensor.begin()

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

    link = None

    try:
        import device

        link = device.DeviceLink(
            config,
            wlan,
            ingest,
            updater,
            channels=list(range(1, len(CS_PINS) + 1)),
            settings=runtime,
            source="max31865",
        )

        if link.start():
            print("# device: %s, heartbeat every %d s" % (link.id, link.heartbeat_s))
        else:
            print("# device: heartbeat unavailable")
    except Exception as error:
        print("# device: disabled: %s" % (error,))

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

            frame_settings = runtime.snapshot()
            channel_settings = {}

            for entry in frame_settings["channels"]:
                channel_settings[entry["channel"]] = entry

            # Read every enabled furnace; a disabled channel is an empty slot
            # (null with no fault), exactly like an uninstalled probe.
            for index, sensor in enumerate(sensors):
                channel = channel_settings.get(index + 1) or {
                    "enabled": True,
                    "offset": 0.0,
                }

                if not channel["enabled"]:
                    temperatures.append(None)
                    faults.append(0)
                    continue

                temperature, fault = read_channel(sensor)

                if temperature is not None:
                    temperature += channel["offset"]

                temperatures.append(temperature)
                faults.append(fault)

            watch.feed()

            elapsed_time = time.ticks_diff(time.ticks_ms(), start_tick)

            # Time_ms, Furnace1, Furnace2, Furnace3 (fourth slot is empty)
            if echo:
                print(",".join(
                    [str(elapsed_time)] + ["%.2f" % csv_value(value) for value in temperatures]
                ))

            at_ms = None if epoch_base_ms is None else epoch_base_ms + elapsed_time

            # The API wire format is four channels wide. An uninstalled
            # furnace is null with no fault, never a phantom temperature.
            payload_t = temperatures + [None] * (API_CHANNELS - len(temperatures))
            payload_f = faults + [0] * (API_CHANNELS - len(faults))
            ingest.add(elapsed_time, payload_t, payload_f, at_ms)

            now = time.ticks_ms()

            # A half-open Wi-Fi association can still report connected. After
            # a minute without a successful upload, force a fresh association.
            stalled_network = ingest.needs_network_reset()

            if (not wlan.isconnected() or stalled_network) and time.ticks_diff(now, last_net_check) >= retry_ms:
                last_net_check = now

                if stalled_network:
                    print("# network: uploads stalled, resetting Wi-Fi")
                    ingest.reset_network()

                if wifi.reconnect(wlan, config, watch=watch, force=stalled_network):
                    ingest.reset_network()

                    if link is not None:
                        link.reset_network()

                    if not clock_ok and getattr(config, "NTP_ENABLED", True):
                        if wifi.sync_time(config):
                            clock_ok = True
                            epoch_base_ms = int(time.time()) * 1000 - time.ticks_diff(now, start_tick)

            # The uploader is a thread, so it can die while the loop runs on.
            # On a charger nobody would start it again: the loop does.
            if time.ticks_diff(now, last_health) >= 15000:
                last_health = now

                if not ingest.alive():
                    print("# upload thread stopped or stalled, resetting it")
                    ingest.reset_network()

                    if not ingest.start():
                        print("# upload unavailable, sending inline")

                if link is not None and not link.alive():
                    print("# device: heartbeat stopped, starting it again")
                    link.start()

            if updater is not None and updater.ready:
                print("# OTA: %s staged, restarting" % (updater.ready,))
                ingest.stop()

                if link is not None:
                    link.stop()

                ota.reboot()

            if not confirmed and time.ticks_diff(now, start_tick) >= 60000:
                confirmed = True

                if ota is not None:
                    ota.confirm()

            if time.ticks_diff(now, last_report) >= 60000:
                last_report = now
                print("# %s" % (ingest.stats(),))

            next_tick = time.ticks_add(next_tick, frame_settings["sample_ms"])
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

        if link is not None:
            link.stop()


def main():
    cause = supervisor.reset_cause()

    if cause is not None:
        print("# boot: reset cause %s" % (cause,))

    watch = supervisor.Watchdog(getattr(config, "WATCHDOG_S", 30))

    # Nobody is watching a board that runs from a charger, so a crash restarts
    # the session instead of leaving an idle REPL behind.
    supervisor.run_forever(session, watch)


main()
