"""Tests for the MQTT bridge. No broker, no API and no paho needed."""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from furnace_bridge import (  # noqa: E402
    ApiPoster,
    Bridge,
    BoundedQueue,
    Config,
    PayloadError,
    PermanentError,
    RetryableError,
    clean_temp,
    parse_payload,
    parse_topic,
    topic_for,
)


def frame(uptime=0, temps=(700.0, 710.0, 720.0, 730.0), faults=(0, 0, 0, 0)):
    return [uptime, list(temps), list(faults)]


def batch(seq=1, frames=None, **extra):
    payload = {"v": 1, "seq": seq, "frames": frames if frames is not None else [frame()]}
    payload.update(extra)
    return json.dumps(payload).encode()


class TopicTests(unittest.TestCase):
    def test_samples_topic(self):
        self.assertEqual(parse_topic("furnace/lab/kiln-1/samples"), ("lab", "kiln-1"))

    def test_ack_topic_is_accepted_too(self):
        self.assertEqual(parse_topic("furnace/lab/kiln-1/ack"), ("lab", "kiln-1"))

    def test_wrong_depth_is_rejected(self):
        for topic in ("furnace/lab/samples", "furnace/lab/kiln/samples/extra", "other/lab/kiln/samples"):
            with self.assertRaises(PayloadError):
                parse_topic(topic)

    def test_empty_parts_are_rejected(self):
        with self.assertRaises(PayloadError):
            parse_topic("furnace//kiln/samples")

    def test_topic_for(self):
        self.assertEqual(topic_for("lab", "kiln-1", "ack"), "furnace/lab/kiln-1/ack")


class PayloadTests(unittest.TestCase):
    def test_relative_batch(self):
        seq, samples = parse_payload(batch(seq=7, frames=[frame(0), frame(100)]), "kiln-1")
        self.assertEqual(seq, 7)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[1]["u"], 100)
        self.assertNotIn("at", samples[0])

    def test_absolute_batch_keeps_spacing(self):
        _, samples = parse_payload(
            batch(seq=1, frames=[frame(1000), frame(1100)], at=1_730_000_000_000),
            "kiln",
        )
        # `at` is the publish time of the newest frame, so the older one sits
        # 100 ms of uptime before it.
        self.assertEqual(samples[0]["at"], 1_730_000_000_000 - 100)
        self.assertEqual(samples[1]["at"], 1_730_000_000_000)

    def test_fault_channel_becomes_null(self):
        _, samples = parse_payload(
            batch(frames=[frame(0, (700.0, -1000.0, 720.0, 730.0), (0, 1, 0, 0))]),
            "kiln",
        )
        self.assertEqual(samples[0]["t"][1], None)

    def test_fault_bit_without_sentinel_is_null(self):
        _, samples = parse_payload(
            batch(frames=[frame(0, (700.0, 710.0, 720.0, 730.0), (0, 0, 4, 0))]),
            "kiln",
        )
        self.assertEqual(samples[0]["t"][2], None)

    def test_out_of_range_temperature_is_null(self):
        _, samples = parse_payload(
            batch(frames=[frame(0, (700.0, 9999.0, 720.0, 730.0))]),
            "kiln",
        )
        self.assertIsNone(samples[0]["t"][1])

    def test_bad_frames_are_rejected(self):
        cases = [
            {"v": 2, "seq": 1, "frames": [frame()]},
            {"v": 1, "seq": 1, "frames": []},
            {"v": 1, "frames": [frame()]},
            {"v": 1, "seq": 1, "frames": [[0, [1, 2, 3], [0, 0, 0, 0]]]},
            {"v": 1, "seq": 1, "frames": [[0, [1, 2, 3, 4], [0, 0, 0]]]},
            {"v": 1, "seq": 1, "frames": [[0, [1, 2, 3, 4], [0, 0, 999, 0]]]},
            {"v": 1, "seq": 1, "frames": [[-1, [1, 2, 3, 4], [0, 0, 0, 0]]]},
        ]

        for payload in cases:
            with self.assertRaises(PayloadError):
                parse_payload(json.dumps(payload).encode(), "kiln")

    def test_non_json_and_non_object(self):
        for payload in (b"hello", b"[]", b"\xff\xfe"):
            with self.assertRaises(PayloadError):
                parse_payload(payload, "kiln")

    def test_too_many_frames(self):
        with self.assertRaises(PayloadError):
            parse_payload(batch(frames=[frame(i * 100) for i in range(601)]), "kiln")

    def test_too_many_frames_boundary_is_accepted(self):
        _, samples = parse_payload(batch(frames=[frame(i * 100) for i in range(600)]), "kiln")
        self.assertEqual(len(samples), 600)

    def test_clean_temp(self):
        self.assertEqual(clean_temp("21.5", 0), 21.5)
        self.assertIsNone(clean_temp(None, 0))
        self.assertIsNone(clean_temp(700, 1))
        self.assertIsNone(clean_temp(True, 0))
        self.assertIsNone(clean_temp(float("nan"), 0))
        self.assertIsNone(clean_temp(-1000, 0))


class QueueTests(unittest.TestCase):
    def test_drops_oldest_when_full(self):
        queue = BoundedQueue(limit=2)
        self.assertTrue(queue.push("a"))
        self.assertTrue(queue.push("b"))
        self.assertFalse(queue.push("c"))
        self.assertEqual(queue.pop(0), "b")
        self.assertEqual(queue.pop(0), "c")

    def test_pop_times_out(self):
        self.assertIsNone(BoundedQueue(limit=1).pop(timeout=0.01))


class FakePoster:
    def __init__(self, behaviour=None):
        self.calls = []
        self.behaviour = behaviour

    def post(self, samples):
        self.calls.append(samples)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return len(samples)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(site="lab", queue_limit=4)
        self.poster = FakePoster()
        self.messages = []
        self.bridge = Bridge(
            self.config,
            self.poster,
            publish=lambda topic, payload, qos, retain: self.messages.append(
                (topic, json.loads(payload), qos, retain)
            ),
        )

    def test_stores_and_acknowledges(self):
        self.bridge.handle_message("furnace/lab/kiln/samples", batch(seq=3, frames=[frame()]))
        self.assertTrue(self.bridge.flush_once())

        self.assertEqual(len(self.poster.calls), 1)
        self.assertEqual(self.poster.calls[0][0]["u"], 0)

        topic, payload, qos, retain = self.messages[-1]
        self.assertEqual(topic, "furnace/lab/kiln/ack")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["seq"], 3)
        self.assertEqual(payload["accepted"], 1)
        self.assertEqual(qos, 1)
        self.assertFalse(retain)

    def test_duplicate_seq_is_not_posted_twice_but_is_acked(self):
        payload = batch(seq=3)
        self.bridge.handle_message("furnace/lab/kiln/samples", payload)
        self.bridge.flush_once()
        self.bridge.handle_message("furnace/lab/kiln/samples", payload)
        self.bridge.flush_once()

        self.assertEqual(len(self.poster.calls), 1)
        self.assertTrue(self.messages[-1][1]["ok"])

    def test_reboot_sequence_is_accepted(self):
        self.bridge.handle_message("furnace/lab/kiln/samples", batch(seq=5000))
        self.bridge.handle_message("furnace/lab/kiln/samples", batch(seq=0))

        self.assertTrue(self.bridge.flush_once())
        self.assertTrue(self.bridge.flush_once())
        self.assertFalse(self.bridge.flush_once())
        self.assertEqual(len(self.poster.calls), 2)  # both batches stored, seq reset accepted
        self.assertEqual(len(self.bridge.queue), 0)

    def test_other_site_is_ignored(self):
        self.bridge.handle_message("furnace/other/kiln/samples", batch())
        self.assertEqual(len(self.bridge.queue), 0)

    def test_bad_payload_is_rejected_and_not_queued(self):
        self.bridge.handle_message("furnace/lab/kiln/samples", b"{not json")
        self.assertEqual(self.bridge.stats.rejected, 1)
        self.assertEqual(len(self.bridge.queue), 0)
        # No seq was parsed, so there is nothing to acknowledge and no message
        # is published; the device keeps the batch and retries.
        self.assertEqual(self.messages, [])

    def test_retry_keeps_the_batch(self):
        self.poster.behaviour = RetryableError("down")
        self.bridge.handle_message("furnace/lab/kiln/samples", batch())

        with self.assertRaises(RetryableError):
            self.bridge.flush_once()

        self.assertEqual(len(self.bridge.queue), 1)
        self.assertTrue(self.bridge.stats.failures >= 1)
        self.assertEqual(self.messages, [])

        self.poster.behaviour = None
        self.assertTrue(self.bridge.flush_once())
        self.assertEqual(len(self.bridge.queue), 0)
        self.assertTrue(self.bridge.queue.push("x"))  # queue is empty again

    def test_permanent_error_drops_and_reports(self):
        self.poster.behaviour = PermanentError("API 422")
        self.bridge.handle_message("furnace/lab/kiln/samples", batch())

        self.assertTrue(self.bridge.flush_once())
        self.assertEqual(len(self.bridge.queue), 0)
        topic, payload = self.messages[-1][0], self.messages[-1][1]
        self.assertEqual(topic, "furnace/lab/kiln/ack")
        self.assertFalse(payload["ok"])
        self.assertIn("422", payload["error"])

    def test_status_is_retained(self):
        self.bridge.handle_message("furnace/lab/kiln/samples", batch())
        self.bridge.flush_once()
        self.bridge.publish_status(True)

        topic, payload, qos, retain = self.messages[-1]
        self.assertEqual(topic, "furnace/lab/_bridge/status")
        self.assertTrue(payload["online"])
        self.assertEqual(payload["forwarded"], 1)
        self.assertEqual(payload["samples"], 1)
        self.assertEqual(qos, 0)
        self.assertTrue(retain)


class PosterTests(unittest.TestCase):
    def test_posts_json_with_key(self):
        response = mock.MagicMock()
        response.read.return_value = b'{"accepted": 2}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            poster = ApiPoster("http://api.test/api/ingest", "secret", timeout=3)
            accepted = poster.post([{"u": 0, "t": [1, 2, 3, 4], "f": [0, 0, 0, 0]}])

        self.assertEqual(accepted, 2)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header("X-api-key"), "secret")
        self.assertEqual(json.loads(request.data)["samples"][0]["u"], 0)
        self.assertEqual(urlopen.call_args[1]["timeout"], 3)

    def _http_error(self, code):
        return urllib.error.HTTPError("http://api.test", code, "boom", {}, None)

    def test_server_errors_are_retryable(self):
        for code in (500, 502, 503, 429):
            with mock.patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                with self.assertRaises(RetryableError):
                    ApiPoster("http://api.test", "").post([{"u": 0}])

    def test_validation_errors_are_permanent(self):
        for code in (400, 413, 422):
            with mock.patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                with self.assertRaises(PermanentError):
                    ApiPoster("http://api.test", "").post([{"u": 0}])

    def test_auth_errors_are_retryable(self):
        for code in (401, 403):
            with mock.patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                with self.assertRaises(RetryableError):
                    ApiPoster("http://api.test", "").post([{"u": 0}])

    def test_unreachable_api_is_retryable(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(RetryableError):
                ApiPoster("http://api.test", "").post([{"u": 0}])

    def test_broken_json_answer_is_retryable(self):
        response = mock.MagicMock()
        response.read.return_value = b"not json"
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(RetryableError):
                ApiPoster("http://api.test", "").post([{"u": 0}])


class ConfigTests(unittest.TestCase):
    def test_env_overrides(self):
        config = Config.from_env(
            {
                "FURNACE_MQTT_HOST": "broker.internal",
                "FURNACE_MQTT_PORT": "8883",
                "FURNACE_MQTT_TLS": "true",
                "FURNACE_MQTT_SITE": "kilns",
                "FURNACE_MQTT_API_KEY": "k",
                "FURNACE_MQTT_QUEUE_LIMIT": "50",
            }
        )
        self.assertEqual(config.host, "broker.internal")
        self.assertEqual(config.port, 8883)
        self.assertTrue(config.tls)
        self.assertEqual(config.site, "kilns")
        self.assertEqual(config.api_key, "k")
        self.assertEqual(config.queue_limit, 50)

    def test_file_config(self):
        data = {
            "broker": {"host": "mqtt", "site": "lab", "username": "bridge", "password": "pw"},
            "api": {"url": "http://api/api/ingest", "key": "secret"},
            "bridge": {"queue_limit": 100, "status_interval": 5},
        }
        config = Config.from_mapping(data)
        self.assertEqual(config.host, "mqtt")
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.queue_limit, 100)
        self.assertEqual(config.status_interval, 5)


if __name__ == "__main__":
    unittest.main()
