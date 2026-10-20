"""MAX31855 thermocouple amplifier driver for MicroPython.

Four sensors share one SPI bus and use separate chip-select pins. The
MAX31855 needs a falling chip select, a 32-bit read and a short delay before
the next conversion is available.
"""

import time

from machine import Pin

FAULT_OPEN_CIRCUIT = 0x01
FAULT_SHORT_TO_GND = 0x02
FAULT_SHORT_TO_VCC = 0x04
FAULT_READ_ERROR = 0x08


class MAX31855:
    """One thermocouple channel."""

    def __init__(self, spi, cs_pin):
        self.spi = spi
        self.cs = Pin(cs_pin, Pin.OUT, value=1)

    def read_raw(self):
        """Read the 32-bit conversion register."""
        self.cs.value(0)
        time.sleep_us(2)

        try:
            data = self.spi.read(4)
        finally:
            self.cs.value(1)

        return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]

    def read(self):
        """Return (temperature_celsius, fault_bits).

        The temperature is None whenever the channel reports a fault, so the
        API never stores a value that the sensor itself flagged.
        """
        try:
            raw = self.read_raw()
        except OSError:
            return None, FAULT_READ_ERROR

        # A floating MISO line reads as all zeroes or all ones instead of
        # producing a valid conversion, so treat both as a read error.
        if raw == 0x00000000 or raw == 0xFFFFFFFF:
            return None, FAULT_READ_ERROR

        faults = raw & 0x07

        if faults:
            return None, faults

        # Bits 18..31 hold a signed 14-bit value in 0.25 °C steps.
        raw_temperature = (raw >> 18) & 0x3FFF

        if raw_temperature & 0x2000:
            raw_temperature -= 0x4000

        return raw_temperature * 0.25, 0


class MAX31855Array:
    """Four MAX31855 channels sharing one SPI bus."""

    def __init__(self, spi, cs_pins):
        if len(cs_pins) != 4:
            raise ValueError("Exactly four chip-select pins are required")

        self.sensors = [MAX31855(spi, pin) for pin in cs_pins]

    def read_all(self):
        temperatures = []
        faults = []

        for sensor in self.sensors:
            temperature, fault = sensor.read()
            temperatures.append(None if temperature is None else round(temperature, 2))
            faults.append(fault)

        return temperatures, faults
