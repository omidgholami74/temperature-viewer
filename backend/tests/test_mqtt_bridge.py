import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mqtt"))

import bridge  # noqa: E402  (the path above is what makes this importable)


def telemetry(**overrides):
    """A valid v2 message; overrides replace top-level keys."""
    message = {
        "v": 2,
        "device_id": "esp32_001",
        "boot_id": "0f3a9c",
        "seq": 7,
        "at": 1792523131000,
        "source": "ntp",
        "interval_ms": 100,
        "rssi": -61,
        "frames": [
            {"u": 1200000, "t": [781.25, 660.5, None, 545.75], "f": [0, 0, 1, 0]},
            {"u": 1200100, "t": [781.5, 660.75, None, 545.5], "f": [0, 0, 1, 0]},
        ],
    }
    message.update(overrides)
    return message


TOPIC = "factory/razi/esp32_001/telemetry"


def encoded(message):
    return json.dumps(message).encode("utf-8")


class ParseTests(unittest.TestCase):
    def test_valid_message_becomes_ingest_samples(self):
        batch = bridge.parse_telemetry(TOPIC, encoded(telemetry()))

        self.assertEqual(batch["device_id"], "esp32_001")
        self.assertEqual(batch["site"], "razi")
        self.assertEqual(batch["boot_id"], "0f3a9c")
        self.assertEqual(batch["seq"], 7)
        self.assertEqual(len(batch["samples"]), 2)

        first, second = batch["samples"]
        self.assertEqual(first["u"], 1200000)
        self.assertEqual(first["t"], [781.25, 660.5, None, 545.75])
        self.assertEqual(first["f"], [0, 0, 1, 0])
        # at is the time of the first frame, then the uptime grid decides.
        self.assertEqual(first["at"], 1792523131000)
        self.assertEqual(second["at"], 1792523131100)

    def test_without_a_clock_no_sample_carries_at(self):
        batch = bridge.parse_telemetry(
            TOPIC, encoded(telemetry(at=None, source="uptime"))
        )

        self.assertTrue(all("at" not in sample for sample in batch["samples"]))

    def test_device_id_must_match_the_topic(self):
        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, encoded(telemetry(device_id="esp32_002")))

    def test_unknown_payload_version_is_rejected(self):
        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, encoded(telemetry(v=3)))

    def test_three_channel_device_must_send_a_null_fourth(self):
        message = telemetry()
        message["frames"] = [{"u": 1, "t": [700.0, 700.0, 700.0], "f": [0, 0, 0]}]

        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, encoded(message))

    def test_out_of_range_temperature_is_rejected(self):
        message = telemetry()
        message["frames"] = [{"u": 1, "t": [700.0, 1900.0, None, None], "f": [0] * 4}]

        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, encoded(message))

    def test_frame_count_is_bounded(self):
        message = telemetry(frames=[{"u": i, "t": [1, 1, 1, 1], "f": [0] * 4} for i in range(6)])

        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, encoded(message), max_frames=5)

    def test_topic_without_the_expected_shape_is_rejected(self):
        for topic in (
            "factory/razi/esp32_001",
            "other/razi/esp32_001/telemetry",
            "factory/Razi/esp32_001/telemetry",
        ):
            with self.subTest(topic=topic):
                with self.assertRaises(bridge.Rejected):
                    bridge.parse_telemetry(topic, encoded(telemetry()))

    def test_payload_that_is_not_json_is_rejected(self):
        with self.assertRaises(bridge.Rejected):
            bridge.parse_telemetry(TOPIC, b"not json")


class SequenceTests(unittest.TestCase):
    def test_gap_duplicate_and_reorder(self):
        tracker = bridge.SequenceTracker()

        self.assertEqual(tracker.observe("d1", "b1", 0), ("first", 0))
        self.assertEqual(tracker.observe("d1", "b1", 1), ("ok", 0))
        self.assertEqual(tracker.observe("d1", "b1", 4), ("gap", 2))
        self.assertEqual(tracker.observe("d1", "b1", 4), ("duplicate", 0))
        self.assertEqual(tracker.observe("d1", "b1", 2), ("reordered", 0))

        totals = tracker.totals()
        self.assertEqual(totals["gaps"], 2)
        self.assertEqual(totals["duplicates"], 1)

    def test_a_new_boot_restarts_the_sequence(self):
        tracker = bridge.SequenceTracker()
        tracker.observe("d1", "b1", 900)

        self.assertEqual(tracker.observe("d1", "b2", 0), ("first", 0))

    def test_tracked_boots_are_bounded(self):
        tracker = bridge.SequenceTracker(max_boots=3)

        for boot in range(10):
            tracker.observe("d1", "b%d" % (boot,), 0)

        self.assertEqual(tracker.totals()["boots"], 3)


class Recorder:
    """Stands in for ApiForwarder."""

    def __init__(self, stored=True, status=200):
        self.stored = stored
        self.status = status
        self.batches = []

    def post(self, samples):
        self.batches.append(samples)
        return self.stored, self.status, "{}"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.recorder = Recorder()
        self.bridge = bridge.Bridge(self.recorder, log=self.logs.append)

    def test_telemetry_is_forwarded(self):
        self.assertEqual(self.bridge.handle(TOPIC, encoded(telemetry())), "stored")
        self.assertEqual(len(self.recorder.batches), 1)
        self.assertEqual(self.bridge.stats["stored"], 2)

    def test_rejected_message_is_never_forwarded(self):
        self.assertEqual(
            self.bridge.handle(TOPIC, encoded(telemetry(device_id="other"))), "rejected"
        )
        self.assertEqual(self.recorder.batches, [])
        self.assertEqual(self.bridge.stats["rejected"], 1)

    def test_gap_is_logged(self):
        self.bridge.handle(TOPIC, encoded(telemetry(seq=1)))
        self.bridge.handle(TOPIC, encoded(telemetry(seq=4)))

        self.assertTrue(any("lost 2 message" in line for line in self.logs))

    def test_status_and_event_are_logged(self):
        status = {"v": 2, "device_id": "esp32_001", "state": "online", "boot_id": "b"}
        event = {"v": 2, "kind": "sensor_fault", "channel": 2}

        self.assertEqual(
            self.bridge.handle(
                "factory/razi/esp32_001/status", json.dumps(status).encode()
            ),
            "status:online",
        )
        self.assertEqual(
            self.bridge.handle(
                "factory/razi/esp32_001/event", json.dumps(event).encode()
            ),
            "event:sensor_fault",
        )
        self.assertTrue(any("is online" in line for line in self.logs))
        self.assertEqual(self.recorder.batches, [])

    def test_unknown_topic_is_ignored(self):
        self.assertEqual(self.bridge.handle("factory/razi/esp32_001/other", b"{}"), "ignored")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )
        status = self.server.responses.pop(0) if self.server.responses else 200
        payload = b'{"ok": true}' if status == 200 else b'{"error": "refused"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        pass


class ForwarderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.requests = []
        cls.server.responses = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.requests.clear()
        self.server.responses.clear()
        self.logs = []

    def forwarder(self, **kwargs):
        url = "http://127.0.0.1:%d/api/ingest" % (self.server.server_address[1],)

        return bridge.ApiForwarder(
            url,
            key=kwargs.pop("key", "secret"),
            log=self.logs.append,
            sleep=lambda seconds: None,
            **kwargs,
        )

    def test_batch_reaches_the_api_with_the_key(self):
        samples = bridge.parse_telemetry(TOPIC, encoded(telemetry()))["samples"]
        forwarder = self.forwarder()

        self.assertEqual(forwarder.post(samples)[0], True)

        request = self.server.requests[0]
        self.assertEqual(request["path"], "/api/ingest")
        self.assertEqual(request["headers"].get("X-Api-Key"), "secret")
        self.assertEqual(request["body"]["samples"], samples)
        self.assertEqual(forwarder.sent, 2)

    def test_a_refused_payload_is_not_retried(self):
        self.server.responses.append(422)
        forwarder = self.forwarder(retries=3)

        stored, status, _ = forwarder.post([{"u": 1, "t": [1, 1, 1, 1], "f": [0] * 4}])

        self.assertFalse(stored)
        self.assertEqual(status, 422)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(forwarder.refused, 1)

    def test_a_server_error_is_retried_then_given_up(self):
        self.server.responses.extend([500, 500, 200])
        forwarder = self.forwarder(retries=2)

        stored, status, _ = forwarder.post([{"u": 1, "t": [1, 1, 1, 1], "f": [0] * 4}])

        self.assertTrue(stored)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.server.requests), 3)

    def test_a_dead_server_reports_failure_without_raising(self):
        # A port nothing listens on: the request must fail, not hang or crash.
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()

        forwarder = bridge.ApiForwarder(
            "http://127.0.0.1:%d/api/ingest" % (port,),
            timeout=1,
            retries=1,
            log=self.logs.append,
            sleep=lambda seconds: None,
        )

        stored, status, detail = forwarder.post([{"u": 1, "t": [1, 1, 1, 1], "f": [0] * 4}])

        self.assertFalse(stored)
        self.assertIsNone(status)
        self.assertIn("gave up", detail)
        self.assertEqual(forwarder.failed, 1)


if __name__ == "__main__":
    unittest.main()
