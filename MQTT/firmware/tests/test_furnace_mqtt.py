"""Tests for the MicroPython MQTT transport (run on CPython)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from furnace_mqtt import MqttTransport, encode_batch  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000

    def time(self):
        return self.now


class FakeClient:
    def __init__(self):
        self.calls = []
        self.callback = None
        self.will = None

    def set_last_will(self, topic, msg, qos=0, retain=False):
        self.will = (topic, msg, qos, retain)

    def set_callback(self, callback):
        self.callback = callback

    def connect(self):
        self.calls.append(("connect",))

    def subscribe(self, topic, qos=0):
        self.calls.append(("subscribe", topic, qos))

    def publish(self, topic, payload, qos=0, retain=False):
        self.calls.append(("publish", topic, payload, qos, retain))
        return True

    def check_msg(self):
        self.calls.append(("check",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def published(self, topic_leaf):
        return [
            call for call in self.calls
            if call[0] == "publish" and call[1].endswith("/" + topic_leaf)
        ]


def transport(client=None, **kwargs):
    fake = client or FakeClient()
    options = {
        "site": "lab",
        "device": "kiln-1",
        "host": "broker.local",
        "client_factory": lambda: fake,
        "clock": FakeClock(),
    }
    options.update(kwargs)
    return MqttTransport(**options), fake


class EncodeTests(unittest.TestCase):
    def test_relative_batch(self):
        body = json.loads(encode_batch("lab", "kiln-1", 4, [[0, [1, 2, 3, 4], [0, 0, 0, 0]]]))
        self.assertEqual(body["v"], 1)
        self.assertEqual(body["seq"], 4)
        self.assertNotIn("at", body)

    def test_absolute_batch(self):
        body = json.loads(
            encode_batch("lab", "kiln", 1, [[0, [1, 2, 3, 4], [0, 0, 0, 0]]], at=1730000000000)
        )
        self.assertEqual(body["at"], 1730000000000)


class TopicTests(unittest.TestCase):
    def test_topics(self):
        node, _ = transport()
        self.assertEqual(node.topic("samples"), "furnace/lab/kiln-1/samples")
        self.assertEqual(node.topic("ack"), "furnace/lab/kiln-1/ack")
        self.assertEqual(node.topic("state"), "furnace/lab/kiln-1/state")


class ConnectionTests(unittest.TestCase):
    def test_connect_subscribes_and_sets_will(self):
        node, client = transport()
        self.assertTrue(node.connect())

        topics = [call[1] for call in client.calls if call[0] == "subscribe"]
        self.assertIn("furnace/lab/kiln-1/ack", topics)

        self.assertEqual(client.will[0], "furnace/lab/kiln-1/state")
        self.assertTrue(client.will[3])
        state = json.loads(client.will[1])
        self.assertFalse(state["online"])

        online = client.published("state")
        self.assertTrue(json.loads(online[-1][2])["online"])
        self.assertTrue(online[-1][4])

    def test_publish_uses_qos1(self):
        node, client = transport(batch_size=3)
        node.start()
        for index in range(3):
            node.enqueue([index * 100, [700, 710, 720, 730], [0, 0, 0, 0]])

        sent = client.published("samples")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][3], 1)
        body = json.loads(sent[0][2])
        self.assertEqual(len(body["frames"]), 3)

    def test_publish_failure_keeps_frames(self):
        node, client = transport(batch_size=1)
        node.start()

        def explode(*args, **kwargs):
            raise OSError("network down")

        client.publish = explode
        node.enqueue([0, [1, 2, 3, 4], [0, 0, 0, 0]])

        self.assertEqual(len(node.buffer), 1)
        self.assertEqual(node.seq, 0)
        self.assertIsNone(node.client)


class BufferTests(unittest.TestCase):
    def test_ack_trims_only_accepted_frames(self):
        node, client = transport(batch_size=2)
        node.start()

        for index in range(4):
            node.enqueue([index * 100, [700, 710, 720, 730], [0, 0, 0, 0]])

        self.assertEqual(len(node.buffer), 4)
        self.assertEqual(len(client.published("samples")), 2)  # 2 frames per batch

        node._on_message(b"furnace/lab/kiln-1/ack", b'{"seq":1,"ok":true,"accepted":2}')
        self.assertEqual(len(node.buffer), 2)

        node._on_message(b"furnace/lab/kiln-1/ack", b'{"seq":1,"ok":true,"accepted":2}')
        self.assertEqual(len(node.buffer), 2)  # duplicated ack is ignored

    def test_failed_ack_keeps_frames(self):
        node, _ = transport(batch_size=2)
        node.start()
        for index in range(2):
            node.enqueue([index * 100, [700, 710, 720, 730], [0, 0, 0, 0]])

        node._on_message(b"furnace/lab/kiln-1/ack", b'{"seq":1,"ok":false,"accepted":0}')
        self.assertEqual(len(node.buffer), 2)

    def test_old_ack_is_ignored(self):
        node, _ = transport(batch_size=1)
        node.start()
        node.enqueue([0, [1, 2, 3, 4], [0, 0, 0, 0]])
        node._on_message(b"furnace/lab/kiln-1/ack", b'{"seq":1,"ok":true,"accepted":1}')
        node._on_message(b"furnace/lab/kiln-1/ack", b'{"seq":0,"ok":true,"accepted":1}')
        self.assertEqual(node.last_acked_seq, 1)

    def test_buffer_limit_drops_oldest(self):
        node, _ = transport(batch_size=2, buffer_limit=4)
        for index in range(10):
            node.enqueue([index, [1, 2, 3, 4], [0, 0, 0, 0]])

        # Six of the ten frames were pushed out of the capped buffer; the four
        # newest are still queued, starting at frame 6.
        self.assertEqual(len(node.buffer), 4)
        self.assertEqual(node.dropped, 6)
        self.assertEqual(node.buffer[0][0], 6)

    def test_malformed_ack_is_ignored(self):
        node, _ = transport()
        node._on_message(b"furnace/lab/kiln-1/ack", b"not json")
        self.assertEqual(node.last_acked_seq, -1)


if __name__ == "__main__":
    unittest.main()
