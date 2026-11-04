"""Simple MAX31855 serial logger for ESP32-S3 (or Pico).

This file only reads the sensors and prints one CSV line per sample:

    12300,781.25,660.00,940.00

There is no Wi-Fi, HTTP, OTA, settings file, filter or heartbeat here. The
existing collector reads this stream, writes samples.jsonl on the host and
uploads it to the API. A faulted channel is printed as -1000.00; the collector
turns that into null with a fault bit.

Wiring for the ESP32-S3 defaults below:

    SCK  GPIO14
    MISO GPIO12
    CS   GPIO15, 16, 17
    3V3 and GND on every MAX31855

For a Pico, change SPI_ID to 0 and use its pins (for example SCK 6, MISO 4,
CS 5, 7, 9, 11). Add another chip-select to CS_PINS for a fourth furnace.
"""

import time

from machine import Pin, SPI

# --- Settings -------------------------------------------------------------
SPI_ID = 1
SCK_PIN = 14
MISO_PIN = 12
CS_PINS = (15, 16, 17)
SPI_BAUD_RATE = 500_000

# 100 ms is the MAX31855 conversion time; reading faster only repeats data.
SAMPLE_INTERVAL_MS = 100

# Piecewise calibration, applied only to a valid reading. A band is half-open
# (min <= temperature < max) and the first matching band wins. Outside every
# band the offset is 0.0. Edit the offsets for your probe:
OFFSET_BANDS = (
    (-270.0, 200.0, 0.0),
    (200.0, 600.0, 0.0),
    (600.0, 1800.0, 0.0),
)

FAULT_SENTINEL = -1000.0
INTERNAL_MIN_C = -45.0
INTERNAL_MAX_C = 130.0
TEMP_MIN_C = -270.0
TEMP_MAX_C = 1800.0

CSV_HEADER = "ms," + ",".join("t%d" % (index + 1) for index in range(len(CS_PINS)))


def offset_for(temperature):
    """Return the calibration offset for one valid temperature."""
    for low, high, offset in OFFSET_BANDS:
        if low <= temperature < high:
            return offset

    return 0.0


def decode(raw):
    """Decode a 32-bit MAX31855 frame into (temperature_or_None, fault)."""
    if raw is None or raw == 0 or raw == 0xFFFFFFFF:
        return None, 8

    # D17 and D3 are reserved and must be zero. A floating MISO line often
    # produces frames that fail this check and must not look like a furnace.
    if raw & 0x20000 or raw & 0x8:
        return None, 8

    internal_raw = (raw >> 4) & 0x0FFF

    if internal_raw & 0x800:
        internal_raw -= 0x1000

    internal_c = internal_raw * 0.0625

    if internal_c < INTERNAL_MIN_C or internal_c > INTERNAL_MAX_C:
        return None, 8

    fault = raw & 0x07

    if raw & 0x10000:
        fault |= 8

    if fault:
        return None, fault

    temperature_raw = (raw >> 18) & 0x3FFF

    if temperature_raw & 0x2000:
        temperature_raw -= 0x4000

    temperature = temperature_raw * 0.25

    if temperature < TEMP_MIN_C or temperature > TEMP_MAX_C:
        return None, 8

    return temperature, 0


def read_raw(spi, cs):
    """Read one 32-bit frame; return None when the SPI transfer fails."""
    data = None

    try:
        cs.value(0)
        time.sleep_us(2)
        data = spi.read(4)
    except Exception:
        return None
    finally:
        try:
            cs.value(1)
        except Exception:
            pass

    if data is None or len(data) != 4:
        return None

    return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]


def read_channel(spi, cs):
    """Read one channel and return (temperature_or_None, fault)."""
    return decode(read_raw(spi, cs))


def format_sample(uptime_ms, temperatures):
    """Format one CSV row; a fault is a clearly invalid numeric sentinel."""
    values = ",".join(
        "%.2f" % (FAULT_SENTINEL if value is None else value)
        for value in temperatures
    )
    return "%d,%s" % (uptime_ms, values)


def main():
    # Pull MISO high so an unplugged MAX31855 reads as fault data instead of a
    # random floating-bus value. Apply it after SPI init as well, because some
    # ports reset the pad configuration while creating the bus.
    miso = Pin(MISO_PIN, Pin.IN, Pin.PULL_UP)
    spi = SPI(
        SPI_ID,
        baudrate=SPI_BAUD_RATE,
        polarity=0,
        phase=0,
        bits=8,
        firstbit=SPI.MSB,
        sck=Pin(SCK_PIN),
        miso=miso,
    )
    miso.init(Pin.IN, Pin.PULL_UP)

    chip_selects = [Pin(pin, Pin.OUT, value=1) for pin in CS_PINS]

    print("# max31855: %d channels, %d ms, USB only" % (len(CS_PINS), SAMPLE_INTERVAL_MS))
    print(CSV_HEADER)

    started = time.ticks_ms()
    next_tick = started

    try:
        while True:
            delay = time.ticks_diff(next_tick, time.ticks_ms())

            if delay > 0:
                time.sleep_ms(delay)

            taken_at = time.ticks_ms()
            temperatures = []

            for cs in chip_selects:
                temperature, _fault = read_channel(spi, cs)

                if temperature is not None:
                    temperature += offset_for(temperature)

                temperatures.append(temperature)

            print(format_sample(time.ticks_diff(taken_at, started), temperatures))

            next_tick = time.ticks_add(next_tick, SAMPLE_INTERVAL_MS)

            # A USB write or a slow read fell behind: continue from now rather
            # than printing a burst of late samples.
            if time.ticks_diff(time.ticks_ms(), next_tick) > SAMPLE_INTERVAL_MS * 2:
                next_tick = time.ticks_ms()
    except KeyboardInterrupt:
        print("# stopped")


if __name__ == "__main__":
    main()
