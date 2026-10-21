"""Checkpoint persistence tests, including the Windows rename failure.

Run from the repository root:

    python3 -m unittest discover -s collector/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import collector  # noqa: E402


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, "checkpoint.json")
        self.patch = mock.patch.object(collector, "CHECKPOINT_FILE", self.path)
        self.patch.start()
        # Keep the retry loop fast; the delays only matter on a real desktop.
        self.delay = mock.patch.object(collector, "CHECKPOINT_RETRY_DELAY_S", 0)
        self.delay.start()
        collector._checkpoint_warned_at = 0.0

    def tearDown(self):
        self.delay.stop()
        self.patch.stop()

    def read(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_roundtrip(self):
        self.assertTrue(collector.save_offset("log.jsonl", 4096))
        self.assertEqual(collector.load_offset("log.jsonl"), 4096)
        self.assertEqual(self.read(), {"source": "log.jsonl", "offset": 4096})

    def test_locked_target_falls_back_to_in_place_write(self):
        collector.save_offset("log.jsonl", 100)

        with mock.patch.object(
            collector.os,
            "replace",
            side_effect=PermissionError(5, "Access is denied"),
        ):
            self.assertTrue(collector.save_offset("log.jsonl", 200))

        self.assertEqual(self.read(), {"source": "log.jsonl", "offset": 200})
        self.assertEqual(collector.load_offset("log.jsonl"), 200)
        self.assertFalse(os.path.exists(self.path + ".tmp"), "leftover temporary file")

    def test_unwritable_tmp_is_reported_not_raised(self):
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith(".tmp"):
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", fake_open):
            self.assertFalse(collector.save_offset("log.jsonl", 300))

    def test_unreadable_checkpoint_warns_and_starts_at_zero(self):
        collector.save_offset("log.jsonl", 500)
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path) == self.path and args and "r" in args[0]:
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", fake_open):
            self.assertEqual(collector.load_offset("log.jsonl"), 0)

    def test_damaged_json_is_treated_as_zero(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"source": "log.jsonl", "off')

        self.assertEqual(collector.load_offset("log.jsonl"), 0)

    def test_other_source_starts_at_zero(self):
        collector.save_offset("a.jsonl", 900)
        self.assertEqual(collector.load_offset("b.jsonl"), 0)

    def test_upload_thread_survives_an_exception(self):
        calls = []

        def broken(path, keep_waiting=True):
            calls.append(path)

            if len(calls) == 1:
                raise PermissionError(5, "Access is denied")

            raise SystemExit(0)

        with mock.patch.object(collector, "upload", broken), mock.patch.object(
            collector.time, "sleep", lambda *_: None
        ):
            with self.assertRaises(SystemExit):
                collector.upload_forever("log.jsonl")

        self.assertEqual(len(calls), 2, "upload_forever must retry after an error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
