"""ESP32-S3 + 3x MAX31855, Wi-Fi, straight to the PHP API.

Three furnaces are read every 400 ms, each through a median/EMA noise filter;
the ``ms,f1,f2,f3`` line goes out on USB serial for Thonny / Serial Studio /
``serial-web``, and every frame is POSTed to ``/api/ingest`` from the board
itself — no PC collector in the middle.

The API frame is still four channels wide. A board with three probes sends the
fourth slot as ``null`` with no fault bit, so an empty slot is not reported as a
broken probe. Add a fourth pin to ``CS_PINS`` (and an offset) to use it.

Wiring (see README.md):

    SCK  GPIO14   shared clock
    MISO GPIO12   shared data in
    CS   GPIO15, 16, 17             one per MAX31855 board
    3V3  and GND  on every board   (the MAX31855 has no MOSI)

Copy ``common/config.example.py`` to ``config.py``, edit it, then upload
``config.py``, ``wifi.py``, ``ingest.py``, ``supervisor.py``, ``ota.py``,
``device.py``, ``settings.py`` and this ``main.py`` to the board root and run
``main.py``. With those at the root the firmware runs on its own from a USB
charger and accepts settings and updates from the admin panel.

The watchdog reboots a hung loop, ``supervisor.run_forever`` restarts a crashed
session and ``ota.py`` applies a bundle only when the panel queues an update.
"""

import time

from machine import Pin, SPI

import config
import settings as settings_module
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

SAMPLE_INTERVAL_MS = 400

# The wire format is fixed at four channels by the API. Unused slots are sent
# as null (temperature) and 0 (fault), so they read as "no probe installed".
API_CHANNELS = 4

CSV_HEADER = "ms,furnace1,furnace2,furnace3"
FAULT_SENTINEL = -1000.0  # CSV value for a channel that reported a fault

# ---------------------------------------------------------------------------
# Noise reduction
# ---------------------------------------------------------------------------
# Every channel is read several times per slot and the median is used, so one
# corrupt SPI frame (the usual cause of a sudden ±6 °C spike) is thrown away
# instead of averaged into the result. Five reads 6 ms apart take about 76 ms
# for three furnaces. The 400 ms default leaves plenty of headroom, so serial
# output and the uploader cannot push the sampling loop behind.
NOISE_SAMPLES = 5
NOISE_GAP_US = 6000
CHANNEL_GAP_US = 500        # pause between channels on the shared SCK/MISO bus
NOISE_EMA_ALPHA = 0.35      # 1.0 = no smoothing, 0.25-0.4 = smooth but responsive

# Plausible type-K range. A frame outside it is treated as a spike and dropped
# before the median filter instead of being uploaded as a real temperature.
TEMP_MIN_C = -270.0
TEMP_MAX_C = 1800.0

# A MAX31855 also reports its own cold-junction temperature. A disconnected
# module leaves MISO floating, and noise can otherwise look like a valid
# thermocouple frame. These limits reject frames that cannot come from a real
# chip; the internal sensor is specified for -40..125 degC.
INTERNAL_MIN_C = -45.0
INTERNAL_MAX_C = 130.0

# Default calibration offset per furnace, in °C, in CS_PINS order: furnace 1
# is CS 15, furnace 2 is CS 16, and so on. These values are only the fallback
# and the first-boot defaults: the admin panel stores an offset per channel in
# /settings.json and that value wins until the file is deleted. A faulted
# channel is never offset, so it stays -1000.00 on serial and null in upload.
CHANNEL_OFFSETS = [0.0, 0.0, 0.0]

# Live settings: sample rate, filter, heartbeat and per-channel offset/enabled.
# They are loaded from /settings.json and can be changed from the admin panel
# without reflashing; the constants in this file are the defaults.
runtime = settings_module.Settings(
    defaults={
        "sample_ms": SAMPLE_INTERVAL_MS,
        "noise_samples": NOISE_SAMPLES,
        "noise_gap_us": NOISE_GAP_US,
        "ema_alpha": NOISE_EMA_ALPHA,
        "heartbeat_s": getattr(config, "HEARTBEAT_S", 15),
    },
    channels=[
        {"channel": index + 1, "enabled": True, "offset": offset}
        for index, offset in enumerate(CHANNEL_OFFSETS)
    ],
    log=print,
)

# SPI configuration
# 500 kHz instead of 1 MHz: the MAX31855 tolerates 5 MHz, but a slower clock
# rings far less on long unshielded SCK/MISO wires, which is a common source of
# the spikes this filter removes.
# Pull MISO high while no MAX31855 is selected. A disconnected module then
# reads as 0xFFFFFFFF (fault bits) instead of random floating-bus data that can
# look like a plausible temperature.
miso = Pin(MISO_PIN, Pin.IN, Pin.PULL_UP)

spi = SPI(
    1,
    baudrate=500000,
    polarity=0,
    phase=0,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(SCK_PIN),
    miso=miso,
)

# The SPI driver may reset the MISO pad pull while configuring the bus; apply
# the pull-up again after init so it also holds between transactions.
miso.init(Pin.IN, Pin.PULL_UP)

# Initialize chip-select pins
chip_selects = [
    Pin(pin, Pin.OUT, value=1)
    for pin in CS_PINS
]


def _read_raw(cs):
    """Read one raw 32-bit frame; ``None`` when the transfer failed."""

    try:
        cs.value(1)
        cs.value(0)
        time.sleep_us(2)

        data = spi.read(4)

        cs.value(1)

        if data is None or len(data) != 4:
            return None

        return (
            (data[0] << 24)
            | (data[1] << 16)
            | (data[2] << 8)
            | data[3]
        )

    except Exception:
        try:
            cs.value(1)
        except Exception:
            pass
        return None


def _decode(raw):
    """Decode one frame into ``(temperature_or_None, fault_bits)``.

    Fault bits match the API: 1 open thermocouple, 2 short to GND,
    4 short to VCC, 8 read error.

    A MAX31855 frame has two reserved zero bits (D17 and D3) and a 12-bit
    signed internal-temperature field. A missing module leaves MISO floating,
    so without these checks random bus data could become a believable furnace
    temperature. Frames with reserved bits set or an impossible internal
    temperature are treated as read failures.
    """

    if raw & 0x20000 or raw & 0x8:  # reserved D17 / D3 must be zero
        return None, 8

    internal_raw = (raw >> 4) & 0x0FFF

    if internal_raw & 0x800:
        internal_raw -= 0x1000

    internal_c = internal_raw * 0.0625

    if internal_c < INTERNAL_MIN_C or internal_c > INTERNAL_MAX_C:
        return None, 8

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

    # Extract signed 14-bit thermocouple temperature
    temp_raw = (raw >> 18) & 0x3FFF

    if temp_raw & 0x2000:
        temp_raw -= 0x4000

    return temp_raw * 0.25, 0


def _plausible(temperature):
    """Reject obviously wrong values before they reach the median filter."""

    return temperature is not None and TEMP_MIN_C < temperature < TEMP_MAX_C


def _median(values):
    """Median without importing statistics (keeps RAM low)."""

    ordered = sorted(values)
    count = len(ordered)

    if not count:
        return None

    middle = count // 2

    if count % 2:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) * 0.5


# EMA state per channel, ``None`` until the first valid reading.
_ema_state = [None] * len(CS_PINS)


def _smooth(index, temperature, alpha=None):
    """Exponential moving average for one channel; faults pass through."""

    if temperature is None:
        return None

    factor = NOISE_EMA_ALPHA if alpha is None else alpha
    previous = _ema_state[index]

    if previous is None:
        _ema_state[index] = temperature
        return temperature

    smoothed = previous + factor * (temperature - previous)
    _ema_state[index] = smoothed
    return smoothed


def read_channel(cs, samples=None, gap_us=None):
    """Read one channel with median filtering.

    Returns ``(temperature_or_None, fault_bits)`` with the API's bits:
    1 open thermocouple, 2 short to GND, 4 short to VCC, 8 read error.

    Strategy: take ``samples`` frames spaced by ``gap_us``, drop any frame that
    reports a fault, invalid reserved/internal bits, or an out-of-range value,
    and return the median of the rest. A channel needs a majority of valid
    frames: accepting one lucky frame from a floating MISO line is how a
    disconnected furnace can look alive. Both arguments default to the module
    constants and are supplied from the live settings by the loop.
    """

    count = NOISE_SAMPLES if samples is None else int(samples)
    spacing = NOISE_GAP_US if gap_us is None else int(gap_us)
    readings = []
    fault_bits = 0

    for i in range(count):
        raw = _read_raw(cs)

        if raw is None:
            fault_bits |= 8
        else:
            temperature, fault = _decode(raw)

            if fault:
                fault_bits |= fault
            elif _plausible(temperature):
                readings.append(temperature)

        # Wait for a fresh conversion before the next read.
        if i != count - 1:
            time.sleep_us(spacing)

    needed = max(1, (count // 2) + 1)

    if len(readings) < needed:
        return None, fault_bits or 8

    return _median(readings), 0


def read_temperature(cs):
    """Read one channel in Celsius; ``nan`` on a fault (original API)."""

    temperature, _fault = read_channel(cs)
    return float("nan") if temperature is None else temperature


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
    # and the sampling loop must stay on the frame grid while it happens.
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
            source="max31855",
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

            # Read every installed furnace: median of NOISE_SAMPLES reads,
            # then an EMA, then that channel's live calibration offset. The
            # offset is applied only to a valid reading, so a fault stays a
            # fault instead of becoming a plausible-looking temperature.
            for index, cs in enumerate(chip_selects):
                channel = channel_settings.get(index + 1) or {
                    "enabled": True,
                    "offset": 0.0,
                }

                if not channel["enabled"]:
                    # Drop the old average so re-enabling the furnace starts
                    # from the first fresh reading instead of a stale value.
                    _ema_state[index] = None
                    temperatures.append(None)
                    faults.append(0)
                    continue

                temperature, fault = read_channel(
                    cs,
                    frame_settings["noise_samples"],
                    frame_settings["noise_gap_us"],
                )
                temperature = _smooth(index, temperature, frame_settings["ema_alpha"])

                if temperature is not None:
                    temperature += settings_module.offset_for(channel, temperature)

                temperatures.append(temperature)
                faults.append(fault)

                # A short pause keeps one channel's edges out of the next
                # channel's frame; all three CS lines share SCK and MISO.
                time.sleep_us(CHANNEL_GAP_US)

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

            # A dropped link is retried on its own; sampling never stops. If
            # Wi-Fi still claims to be associated but uploads have failed for a
            # minute, force a fresh association rather than trusting a half-open
            # DHCP/AP session forever.
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

            # A staged update is applied at the next boot; flush what is still
            # queued first so the update does not cost a buffer of samples.
            if updater is not None and updater.ready:
                print("# OTA: %s staged, restarting" % (updater.ready,))
                ingest.stop()

                if link is not None:
                    link.stop()

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
            next_tick = time.ticks_add(next_tick, frame_settings["sample_ms"])
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


if __name__ == "__main__":
    main()
