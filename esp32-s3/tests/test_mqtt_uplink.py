"""Checks for the MQTT publisher that run without a board or a broker.

    python3 -m unittest discover -s esp32-s3/tests -v

``umqtt.simple`` is replaced by a fake client that records what the firmware
would publish, so the message shapes from ``docs/mqtt-protocol.md`` are
checked here instead of on the furnace.
"""

import importlib.util
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Records publishes; ``fail_publish`` simulates a dead socket."""

    instances = []

    def __init__(self, client_id, server, port, user, password, keepalive, ssl):
        self.client_id = client_id
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.keepalive = keepalive
        self.ssl = ssl
        self.published = []
        self.subscribed = []
        self.will = None
        self.callback = None
        self.queued = []
        self.disconnected = False
        self.fail_publish = False
        FakeClient.instances.append(self)

    def set_last_will(self, topic, message, retain=False, qos=0):
        self.will = {"topic": topic, "payload": json.loads(message), "retain": retain}

    def set_callback(self, callback):
        self.callback = callback

    def connect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def publish(self, topic, message, retain=False, qos=0):
        if self.fail_publish:
            raise OSError("connection lost")

        self.published.append(
            {"topic": topic, "payload": json.loads(message), "retain": retain, "qos": qos}
        )

    def check_msg(self):
        if self.queued:
            topic, payload = self.queued.pop(0)
            self.callback(topic, payload)

    def disconnect(self):
        self.disconnected = True


def install_umqtt_stub():
    package = types.ModuleType("umqtt")
    simple = types.ModuleType("umqtt.simple")
    simple.MQTTClient = FakeClient
    package.simple = simple
    sys.modules["umqtt"] = package
    sys.modules["umqtt.simple"] = simple


install_umqtt_stub()
uplink_module = load("esp32s3_mqtt_uplink", os.path.join(ROOT, "common", "mqtt_uplink.py"))


class Config:
    MQTT_DEVICE_ID = "esp32_001"
    MQTT_HOST = "192.168.1.10"
    MQTT_PORT = 1883
    MQTT_SITE = "razi"
    MQTT_PASSWORD = "secret"
    BATCH_SIZE = 10
    BUFFER_LIMIT = 600
    MQTT_STATUS_INTERVAL_S = 60


def frame(index, temperature=700.0):
    return (1_200_000 + index * 100, [temperature, 700.0, None, 700.0], [0, 0, 1, 0])


class UplinkTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        self.config = Config()
        self.logs = []
        self.uplink = uplink_module.MqttUplink(self.config, log=self.logs.append)
        self.uplink.start()
        self.client = FakeClient.instances[-1]
        self.client.published.clear()

    def telemetry(self):
        return [item for item in self.client.published if item["topic"].endswith("/telemetry")]

    def status(self):
        return [item for item in self.client.published if item["topic"].endswith("/status")]

    def test_start_registers_offline_will_and_subscribes_commands(self):
        self.assertEqual(self.client.will["topic"], "factory/razi/esp32_001/status")
        self.assertEqual(self.client.will["payload"]["state"], "offline")
        self.assertTrue(self.client.will["retain"])
        self.assertEqual(
            self.client.subscribed, [("factory/razi/esp32_001/cmd", 1)]
        )

    def test_one_message_carries_one_second_of_frames(self):
        for index in range(10):
            self.uplink.add(*frame(index), at_ms=1792523131000 + index * 100)

        self.assertEqual(self.uplink.flush(), True)

        messages = self.telemetry()
        self.assertEqual(len(messages), 1)

        payload = messages[0]["payload"]
        self.assertEqual(payload["v"], 2)
        self.assertEqual(payload["device_id"], "esp32_001")
        self.assertEqual(payload["seq"], 0)
        self.assertEqual(payload["source"], "ntp")
        self.assertEqual(payload["at"], 1792523131000)
        self.assertEqual(len(payload["frames"]), 10)
        self.assertEqual(payload["frames"][0]["u"], 1_200_000)
        self.assertEqual(payload["frames"][0]["t"], [700.0, 700.0, None, 700.0])
        self.assertEqual(payload["frames"][0]["f"], [0, 0, 1, 0])
        # Frames carry u/t/f only: at lives once per message.
        self.assertNotIn("at", payload["frames"][0])
        self.assertEqual(self.uplink.stats().split()[0], "sent=10")

    def test_sequence_increments_per_message(self):
        for index in range(20):
            self.uplink.add(*frame(index))

        self.uplink.flush()

        self.assertEqual([m["payload"]["seq"] for m in self.telemetry()], [0, 1])
        self.assertEqual(self.uplink.seq, 2)

    def test_a_partial_batch_waits_unless_everything_is_requested(self):
        for index in range(5):
            self.uplink.add(*frame(index))

        self.uplink.flush()
        self.assertEqual(self.telemetry(), [])

        self.uplink.flush(everything=True)
        self.assertEqual(len(self.telemetry()[0]["payload"]["frames"]), 5)

    def test_without_a_clock_the_message_says_uptime(self):
        for index in range(10):
            self.uplink.add(*frame(index))

        self.uplink.flush()

        payload = self.telemetry()[0]["payload"]
        self.assertIsNone(payload["at"])
        self.assertEqual(payload["source"], "uptime")

    def test_the_buffer_drops_the_oldest_frames(self):
        # The limit is never smaller than one batch, otherwise nothing would
        # ever be published; with batch 2 the buffer can really be 4.
        self.config.BATCH_SIZE = 2
        self.config.BUFFER_LIMIT = 4
        uplink = uplink_module.MqttUplink(self.config, log=self.logs.append)

        for index in range(7):
            uplink.add(*frame(index))

        self.assertEqual(len(uplink.pending), 4)
        self.assertEqual(uplink.dropped, 3)
        self.assertEqual(uplink.pending[0]["u"], 1_200_000 + 3 * 100)

    def test_the_limit_cannot_be_smaller_than_one_batch(self):
        self.config.BATCH_SIZE = 10
        self.config.BUFFER_LIMIT = 3
        uplink = uplink_module.MqttUplink(self.config, log=self.logs.append)

        self.assertEqual(uplink.limit, 10)

    def test_a_failed_publish_keeps_the_frames(self):
        for index in range(10):
            self.uplink.add(*frame(index))

        self.client.fail_publish = True

        self.assertEqual(self.uplink.flush(), False)
        self.assertEqual(len(self.uplink.pending), 10)
        self.assertGreaterEqual(self.uplink.failures, 1)

        self.client.fail_publish = False
        self.uplink.connected = True
        self.uplink.client = self.client
        self.assertEqual(self.uplink.flush(), True)
        self.assertEqual(len(self.telemetry()[0]["payload"]["frames"]), 10)

    def test_status_is_retained(self):
        self.uplink.publish_status("online")

        messages = self.status()
        self.assertTrue(messages[-1]["retain"])
        self.assertEqual(messages[-1]["payload"]["state"], "online")
        self.assertEqual(messages[-1]["payload"]["device_id"], "esp32_001")

    def test_ping_is_acknowledged(self):
        self.client.queued.append(
            (
                "factory/razi/esp32_001/cmd",
                json.dumps({"v": 2, "cmd_id": "c-1", "op": "ping"}).encode(),
            )
        )
        self.uplink.poll()

        acks = [m for m in self.client.published if m["topic"].endswith("/cmd/ack")]
        self.assertEqual(acks[-1]["payload"], {"v": 2, "cmd_id": "c-1", "ok": True,
                                               "applied": {"ping": "pong"}})

    def test_an_unsupported_command_is_refused_not_faked(self):
        self.client.queued.append(
            (
                "factory/razi/esp32_001/cmd",
                json.dumps({"v": 2, "cmd_id": "c-2", "op": "set_interval"}).encode(),
            )
        )
        self.uplink.poll()

        ack = [m for m in self.client.published if m["topic"].endswith("/cmd/ack")][-1]
        self.assertFalse(ack["payload"]["ok"])
        self.assertIn("unsupported", ack["payload"]["error"])

    def test_missing_device_id_is_a_config_error(self):
        config = Config()
        config.MQTT_DEVICE_ID = ""

        with self.assertRaises(ValueError):
            uplink_module.MqttUplink(config, log=self.logs.append)


if __name__ == "__main__":
    unittest.main()
