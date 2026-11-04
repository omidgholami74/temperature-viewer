"""MAX31855 frame validation and majority-read tests.

    python3 -m unittest discover -s esp32-s3/tests -v

The driver is a MicroPython script, so ``machine`` and the board modules are
stubbed. ``main.py`` is guarded with ``if __name__ == "__main__"``: importing it
for these tests constructs the SPI bus but does not start the sampling loop.
"""

import importlib.util
import os
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakePin:
    OUT = 1
    IN = 0
    PULL_UP = 2

    def __init__(self, number, mode=None, pull=None, value=1, **kwargs):
        self.number = number
        self.mode = mode
        self.pull = pull
        self.value_ = value
        self.init_calls = []

    def init(self, mode=None, pull=None, **kwargs):
        self.mode = mode
        self.pull = pull
        self.init_calls.append((mode, pull))

    def value(self, value=None):
        if value is None:
            return self.value_
        self.value_ = value


class FakeSPI:
    MSB = 1

    def __init__(self, bus=0, **kwargs):
        self.bus = bus
        self.options = kwargs

    def read(self, count):
        return bytes(count)


def install_machine_stub():
    machine = types.ModuleType("machine")
    machine.Pin = FakePin
    machine.SPI = FakeSPI
    return machine


def board_modules():
    config = types.ModuleType("config")
    config.API_URL = "http://127.0.0.1"
    config.HEARTBEAT_S = 15

    settings = types.ModuleType("settings")

    class Settings:
        def __init__(self, *args, **kwargs):
            self.options = kwargs

    settings.Settings = Settings

    ingest = types.ModuleType("ingest")

    class Ingest:
        def __init__(self, *args, **kwargs):
            pass

    ingest.Ingest = Ingest

    ota = types.ModuleType("ota")

    class Updater:
        def __init__(self, *args, **kwargs):
            pass

    ota.Updater = Updater

    return {
        "machine": install_machine_stub(),
        "config": config,
        "settings": settings,
        "supervisor": types.ModuleType("supervisor"),
        "wifi": types.ModuleType("wifi"),
        "ingest": ingest,
        "ota": ota,
    }


def frame(celsius, internal_c=25.0, fault=0, reserved=0):
    """Build a four-byte MAX31855 frame as a 32-bit integer."""

    thermocouple = int(round(celsius / 0.25)) & 0x3FFF
    internal = int(round(internal_c / 0.0625)) & 0x0FFF
    return (thermocouple << 18) | (internal << 4) | fault | reserved


class Max31855Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(sys.modules, board_modules()):
            cls.driver = load(
                "esp32s3_max31855",
                os.path.join(ROOT, "max31855", "main.py"),
            )

        if not hasattr(time, "sleep_us"):
            time.sleep_us = lambda microseconds: time.sleep(microseconds / 1000000.0)

    def test_miso_is_pulled_up_while_no_chip_is_selected(self):
        self.assertEqual(self.driver.miso.pull, FakePin.PULL_UP)
        self.assertEqual(self.driver.miso.init_calls[-1], (FakePin.IN, FakePin.PULL_UP))
        self.assertIs(self.driver.spi.options["miso"], self.driver.miso)

    def test_usb_only_does_not_import_network_modules(self):
        modules = board_modules()
        modules["config"].USB_ONLY = True

        with mock.patch.dict(sys.modules, modules):
            driver = load(
                "esp32s3_max31855_usb",
                os.path.join(ROOT, "max31855", "main.py"),
            )

        self.assertTrue(driver.USB_ONLY)
        self.assertIsNone(driver.wifi)
        self.assertIsNone(driver.Ingest)

    def test_the_datasheet_frame_decodes(self):
        self.assertEqual(self.driver._decode(0x01900000), (25.0, 0))
        self.assertEqual(self.driver._decode(frame(25.0)), (25.0, 0))

    def test_a_negative_temperature_decodes(self):
        self.assertEqual(self.driver._decode(frame(-100.25)), (-100.25, 0))

    def test_a_floating_bus_is_a_read_failure(self):
        self.assertEqual(self.driver._decode(0xFFFFFFFF), (None, 8))

    def test_reserved_bits_are_rejected(self):
        self.assertEqual(
            self.driver._decode(frame(100.0, reserved=0x20000)),
            (None, 8),
        )
        self.assertEqual(
            self.driver._decode(frame(100.0, reserved=0x8)),
            (None, 8),
        )

    def test_an_impossible_internal_temperature_is_rejected(self):
        self.assertEqual(
            self.driver._decode(frame(100.0, internal_c=-128.0)),
            (None, 8),
        )

    def test_sensor_fault_bits_are_preserved(self):
        self.assertEqual(self.driver._decode(frame(100.0, fault=0x1)), (None, 1))
        self.assertEqual(self.driver._decode(frame(100.0, fault=0x2)), (None, 2))
        self.assertEqual(self.driver._decode(frame(100.0, fault=0x4)), (None, 4))

    def read_frames(self, frames):
        values = iter(frames)

        with mock.patch.object(self.driver, "_read_raw", side_effect=lambda cs: next(values)):
            return self.driver.read_channel(None, samples=5, gap_us=0)

    def test_one_valid_frame_among_corrupt_reads_is_not_a_temperature(self):
        temperature, fault = self.read_frames([
            frame(100.0),
            0xFFFFFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
        ])

        self.assertIsNone(temperature)
        self.assertEqual(fault, 8)

    def test_two_valid_frames_are_not_a_majority(self):
        temperature, fault = self.read_frames([
            frame(100.0),
            frame(101.0),
            0xFFFFFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
        ])

        self.assertIsNone(temperature)
        self.assertEqual(fault, 8)

    def test_three_valid_frames_win_and_return_the_median(self):
        temperature, fault = self.read_frames([
            frame(100.0),
            frame(101.0),
            0xFFFFFFFF,
            frame(102.0),
            0xFFFFFFFF,
        ])

        self.assertEqual(temperature, 101.0)
        self.assertEqual(fault, 0)

    def test_a_majority_of_valid_frames_still_survives_one_fault(self):
        temperature, fault = self.read_frames([
            frame(100.0),
            frame(101.0, fault=0x1),
            frame(102.0),
            frame(103.0),
            frame(104.0),
        ])

        self.assertEqual(temperature, 102.5)
        self.assertEqual(fault, 0)


if __name__ == "__main__":
    unittest.main()
