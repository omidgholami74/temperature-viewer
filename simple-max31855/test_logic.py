"""Small tests for the pure parts of the simple MAX31855 logger.

The module imports ``machine``; CPython does not have it, so a tiny stand-in is
installed before loading main.py. No hardware and no network are used.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


machine = types.ModuleType("machine")
machine.Pin = object
machine.SPI = object
sys.modules["machine"] = machine

path = pathlib.Path(__file__).with_name("main.py")
spec = importlib.util.spec_from_file_location("simple_max31855", path)
assert spec is not None and spec.loader is not None
simple = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simple)


def frame(temperature, internal=25.0, extra=0):
    raw_temperature = int(temperature / 0.25) & 0x3FFF
    raw_internal = int(internal / 0.0625) & 0x0FFF
    return (raw_temperature << 18) | (raw_internal << 4) | extra


class DecodeTests(unittest.TestCase):
    def test_valid_positive_temperature(self):
        self.assertEqual(simple.decode(frame(25.0)), (25.0, 0))

    def test_valid_negative_temperature(self):
        self.assertEqual(simple.decode(frame(-10.0)), (-10.0, 0))

    def test_open_thermocouple_is_a_fault(self):
        self.assertEqual(simple.decode(frame(25.0, extra=0x1)), (None, 1))

    def test_floating_bus_is_a_read_error(self):
        self.assertEqual(simple.decode(0), (None, 8))
        self.assertEqual(simple.decode(0xFFFFFFFF), (None, 8))

    def test_reserved_bit_is_rejected(self):
        self.assertEqual(simple.decode(frame(25.0, extra=0x8)), (None, 8))


class OffsetTests(unittest.TestCase):
    def test_band_boundaries(self):
        self.assertEqual(simple.offset_for(199.9), 0.0)
        self.assertEqual(simple.offset_for(200.0), 0.0)
        self.assertEqual(simple.offset_for(600.0), 0.0)

    def test_custom_band(self):
        original = simple.OFFSET_BANDS
        simple.OFFSET_BANDS = (
            (-270.0, 200.0, 0.5),
            (200.0, 600.0, -1.5),
            (600.0, 1800.0, 2.0),
        )

        try:
            self.assertEqual(simple.offset_for(-10.0), 0.5)
            self.assertEqual(simple.offset_for(200.0), -1.5)
            self.assertEqual(simple.offset_for(199.9), 0.5)
            self.assertEqual(simple.offset_for(600.0), 2.0)
            self.assertEqual(simple.offset_for(2000.0), 0.0)
        finally:
            simple.OFFSET_BANDS = original


class FormatTests(unittest.TestCase):
    def test_fault_sentinel(self):
        self.assertEqual(
            simple.format_sample(12300, [781.25, None, 940.0]),
            "12300,781.25,-1000.00,940.00",
        )


if __name__ == "__main__":
    unittest.main()
