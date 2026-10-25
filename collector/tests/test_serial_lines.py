"""Serial line parsing: the CSV every firmware prints, plus the old JSON logs.

Run from the repository root:

    python3 -m unittest discover -s collector/tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import collector  # noqa: E402


class SerialLineTests(unittest.TestCase):
    def test_csv_line_becomes_an_api_sample(self):
        sample = collector.parse_line(b"105,781.25,660.00,940.50,545.75")

        self.assertEqual(sample["u"], 105)
        self.assertEqual(sample["t"], [781.25, 660.0, 940.5, 545.75])
        self.assertEqual(sample["f"], [0, 0, 0, 0])

    def test_fault_sentinel_becomes_null_and_a_fault(self):
        sample = collector.parse_line(b"100,781.25,-1000.00,940.00,-1000.00")

        self.assertEqual(sample["t"], [781.25, None, 940.0, None])
        self.assertEqual(sample["f"], [0, 8, 0, 8])

    def test_header_banners_and_broken_lines_are_skipped(self):
        for line in (
            b"ms,furnace1,furnace2,furnace3,furnace4",
            b"# furnace monitor: 4 channels every 100 ms",
            b"# log write failed: 28",
            b"",
            b"105,781.25",
            b"105,781.25,660.00,940.50,oops",
        ):
            self.assertIsNone(collector.parse_line(line), line)

    def test_spaces_and_crlf_are_tolerated(self):
        sample = collector.parse_line(b" 105,0.00,0.00,0.00,0.00\r\n")

        self.assertEqual(sample["u"], 105)
        self.assertEqual(sample["t"], [0.0, 0.0, 0.0, 0.0])

    def test_json_lines_from_an_older_firmware_still_parse(self):
        sample = collector.parse_line(
            b'{"u": 7, "t": [null, null, null, null], "f": [1, 8, 8, 8]}'
        )

        self.assertEqual(sample["u"], 7)
        self.assertEqual(sample["f"], [1, 8, 8, 8])


if __name__ == "__main__":
    unittest.main(verbosity=2)
