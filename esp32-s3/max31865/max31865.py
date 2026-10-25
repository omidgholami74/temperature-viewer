"""MAX31865 RTD front end (PT100 / PT1000) over SPI.

Small enough to keep on the board next to ``main.py`` so no ``mip install`` is
needed. Supports 2-, 3- and 4-wire probes and both the 50 Hz and 60 Hz notch
filter.

Typical use::

    spi = SPI(1, baudrate=1000000, polarity=0, phase=0, sck=Pin(14),
              mosi=Pin(11), miso=Pin(12))
    sensor = MAX31865(spi, cs=15, r_ref=430.0, r_nominal=100.0, wire_mode=3)
    sensor.begin()
    temperature, fault = sensor.read()
"""

import time

from machine import Pin

# Registers
_REG_CONFIG = 0x00
_REG_RTD_MSB = 0x01
_REG_FAULT = 0x03

# Configuration bits
_VBIAS = 0x80     # enable the bias voltage (needed for every conversion)
_AUTO = 0x40      # continuous conversion (no 1-shot)
_3WIRE = 0x10     # 3-wire probe: two leads share one side
_CLEAR_FAULT = 0x02
_FILTER_50HZ = 0x01

# Callendar-Van Dusen constants for platinum (IEC 60751 / DIN 43760)
_RTD_A = 3.9083e-3
_RTD_B = -5.775e-7
_RTD_C = -4.183e-12


def resistance_to_celsius(resistance, r_nominal=100.0):
    """Convert an RTD resistance in ohms to degrees Celsius.

    Above 0 °C the CVD polynomial is quadratic and the root is closed-form.
    Below 0 °C the polynomial has the extra ``C`` term, which has no useful
    closed form, so the root is found with a few Newton steps.
    """
    if resistance is None or resistance <= 0:
        return None

    ratio = resistance / r_nominal

    # R/R0 = 1 + A t + B t^2  ->  B t^2 + A t + (1 - ratio) = 0
    z2 = _RTD_A * _RTD_A - 4.0 * _RTD_B
    z3 = 4.0 * _RTD_B / r_nominal
    z4 = 2.0 * _RTD_B

    discriminant = z2 + z3 * resistance

    if discriminant >= 0:
        celsius = ((-_RTD_A + (discriminant ** 0.5)) / z4) if z4 else 0.0

        if celsius >= 0:
            return celsius

    # Sub-zero: Newton on 1 + A t + B t^2 + C (t - 100) t^3 = ratio
    estimate = (ratio - 1.0) / _RTD_A

    for _ in range(12):
        t2 = estimate * estimate
        t3 = t2 * estimate
        value = 1.0 + _RTD_A * estimate + _RTD_B * t2 + _RTD_C * (estimate - 100.0) * t3
        slope = _RTD_A + 2.0 * _RTD_B * estimate + _RTD_C * (3.0 * t2 * (estimate - 100.0) + t3)

        if slope == 0:
            break

        step = (value - ratio) / slope
        estimate -= step

        if -1e-6 < step < 1e-6:
            break

    return estimate


def fault_bits(code):
    """Map a MAX31865 fault register byte to the API's fault mask.

    1 open element or lead, 2 lead/reference short, 4 over/under voltage,
    8 threshold trip. ``0`` means "no fault".
    """
    if not code & 0x80:
        return 0

    bits = 0

    if code & 0x04:  # RTDIN- below 0.85 x VBIAS: open element or broken lead
        bits |= 1
    if code & 0x02:  # FORCE- open
        bits |= 2
    if code & 0x01:  # over/under voltage
        bits |= 4
    if code & 0x10:  # REFIN- > 0.85 x VBIAS
        bits |= 4
    if code & 0x20 or code & 0x40:  # RTD high/low threshold
        bits |= 8

    return bits or 8


class MAX31865:
    """One RTD channel on a shared SPI bus."""

    def __init__(self, spi, cs, r_ref=430.0, r_nominal=100.0, wire_mode=3, filter_hz=50):
        self.spi = spi
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.r_ref = float(r_ref)
        self.r_nominal = float(r_nominal)

        if wire_mode not in (2, 3, 4):
            raise ValueError("wire_mode must be 2, 3 or 4")

        self.wire_mode = wire_mode
        self.filter_hz = 50 if filter_hz == 50 else 60
        self._fault_cleared = True

    # -- SPI helpers ------------------------------------------------------

    def _config_value(self):
        value = _VBIAS | _AUTO

        if self.wire_mode == 3:
            value |= _3WIRE

        if self.filter_hz == 50:
            value |= _FILTER_50HZ

        return value

    def _write(self, register, value):
        self.cs.value(0)
        self.spi.write(bytes([register | 0x80, value & 0xFF]))
        self.cs.value(1)

    def _read(self, register, length):
        self.cs.value(0)
        self.spi.write(bytes([register & 0x7F]))
        data = self.spi.read(length)
        self.cs.value(1)
        return data

    # -- public API -------------------------------------------------------

    def begin(self):
        """Start continuous conversion with the bias on."""
        self._write(_REG_CONFIG, self._config_value())
        # The first conversion after the bias is enabled needs ~65 ms.
        time.sleep_ms(100)

    def clear_faults(self):
        self._write(_REG_CONFIG, self._config_value() | _CLEAR_FAULT)
        time.sleep_ms(2)
        self._write(_REG_CONFIG, self._config_value())
        self._fault_cleared = True

    def read_resistance(self):
        """Return ``(ohms, fault_mask)``; ohms is ``None`` on a fault."""
        data = self._read(_REG_RTD_MSB, 8)

        if not data or len(data) < 3:
            return None, 8

        raw = ((data[0] << 8) | data[1]) >> 1
        code = data[2]
        bits = fault_bits(code)

        if bits:
            self.clear_faults()
            return None, bits

        return raw * self.r_ref / 32768.0, 0

    def read(self):
        """Return ``(celsius, fault_mask)``; celsius is ``None`` on a fault."""
        resistance, bits = self.read_resistance()

        if bits:
            return None, bits

        return resistance_to_celsius(resistance, self.r_nominal), 0
