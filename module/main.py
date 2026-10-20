"""Furnace monitor firmware for Raspberry Pi Pico W + 4x MAX31855.

Reads all four thermocouples every 0.1 s and uploads batches to the PHP API.
Samples are buffered in RAM while the network is down, so a short WiFi drop
does not lose data.

Copy config.example.py to config.py, edit it, then upload this folder to the
Pico (Thonny: Save as -> Raspberry Pi Pico) and run main.py.
"""

import time

import network

try:
    import config
except ImportError:
    raise SystemExit("config.py is missing. Copy config.example.py to config.py and edit it.")

from api_client import ApiClient
from hardware import build_spi
from max31855 import MAX31855Array
from sampler import Sampler


def connect_wifi():
    """Join the configured network, blocking for at most 30 seconds."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to WiFi:", config.WIFI_SSID)
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        deadline = time.ticks_add(time.ticks_ms(), 30_000)

        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("WiFi connection timed out")

            time.sleep_ms(250)

    print("WiFi connected:", wlan.ifconfig()[0])
    return wlan


def main():
    wlan = connect_wifi()
    sensors = MAX31855Array(build_spi(), config.CS_PINS)
    sampler = Sampler(sensors, config.SAMPLE_INTERVAL_MS)
    client = ApiClient(config.API_BASE, config.API_KEY)

    buffer = []
    last_post = time.ticks_ms()
    last_wifi_check = last_post

    print("Sampling 4 channels every {} ms".format(config.SAMPLE_INTERVAL_MS))

    while True:
        sampler.wait()
        sample = sampler.sample()
        buffer.append(sample)

        now = time.ticks_ms()

        if time.ticks_diff(now, last_post) >= config.POST_INTERVAL_MS or len(buffer) >= 100:
            if client.post_samples(buffer):
                print("sent", len(buffer), "samples | temperatures:", sample["t"])
                buffer = []
            else:
                print("upload failed, buffering", len(buffer), "samples")

                if len(buffer) > config.MAX_BUFFERED_SAMPLES:
                    buffer = buffer[-config.MAX_BUFFERED_SAMPLES:]

            last_post = now

        if time.ticks_diff(now, last_wifi_check) >= 5_000:
            last_wifi_check = now

            if not wlan.isconnected():
                print("WiFi lost, reconnecting")

                try:
                    wlan = connect_wifi()
                except OSError as error:
                    print("Reconnect failed:", error)


try:
    main()
except KeyboardInterrupt:
    print("Stopped by user")
