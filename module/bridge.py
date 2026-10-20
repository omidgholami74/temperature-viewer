#!/usr/bin/env python3
"""Forward samples from a USB-connected Pico to the PHP API.

The Pico runs main_usb.py and prints one JSON sample per line. This script
runs on the PC that can reach the backend:

    python bridge.py --url http://192.168.1.50:8080 --key change-me

Requires pyserial and requests (see requirements.txt).
"""

import argparse
import json
import sys
import time

import requests
import serial
from serial.tools import list_ports

MAX_BUFFER = 600


def pick_port(requested):
    """Use the explicit port or auto-detect a Pico style USB serial device."""
    if requested:
        return requested

    ports = list(list_ports.comports())

    for port in ports:
        description = f"{port.description or ''} {port.manufacturer or ''}".lower()

        if any(key in description for key in ("pico", "micropython", "usb serial", "usb-serial")):
            return port.device

    return ports[0].device if ports else None


def post_batch(session, url, api_key, samples):
    headers = {"Content-Type": "application/json"}

    if api_key:
        headers["X-Api-Key"] = api_key

    try:
        response = session.post(url, json={"samples": samples}, headers=headers, timeout=8)
    except requests.RequestException as error:
        print(f"API unreachable: {error}", file=sys.stderr)
        return False

    if response.status_code == 200:
        return True

    print(f"API {response.status_code}: {response.text[:160]}", file=sys.stderr)
    return False


def read_stream(args, session, url):
    port = pick_port(args.port)

    if not port:
        print("No serial port found. Connect the Pico or pass --port COM3.", file=sys.stderr)
        return 1

    print(f"Forwarding {port} at {args.baud} baud to {url}")
    buffer = []
    last_post = time.monotonic()

    while True:
        try:
            with serial.Serial(port, args.baud, timeout=0.3) as connection:
                while True:
                    line = connection.readline().decode("utf-8", "ignore").strip()

                    if line.startswith("{"):
                        try:
                            sample = json.loads(line)
                        except ValueError:
                            sample = None

                        if isinstance(sample, dict) and "u" in sample and "t" in sample:
                            buffer.append(sample)

                            if len(buffer) > MAX_BUFFER:
                                buffer = buffer[-MAX_BUFFER:]

                    now = time.monotonic()

                    if buffer and (len(buffer) >= args.batch or now - last_post >= args.interval):
                        if post_batch(session, url, args.key, buffer):
                            buffer = []

                        last_post = now

                    time.sleep(0.001)
        except (serial.SerialException, OSError) as error:
            print(f"Serial error: {error}. Retrying in 3 s.", file=sys.stderr)
            time.sleep(3)
            port = pick_port(args.port) or port


def main():
    parser = argparse.ArgumentParser(description="Pico USB -> furnace PHP API bridge")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="backend base URL")
    parser.add_argument("--key", default="", help="ingest API key, if configured")
    parser.add_argument("--port", default="", help="serial port, e.g. COM5 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200, help="serial baud rate")
    parser.add_argument("--batch", type=int, default=10, help="samples per request")
    parser.add_argument("--interval", type=float, default=1.0, help="maximum seconds between requests")
    args = parser.parse_args()

    url = args.url.rstrip("/") + "/api/ingest"
    session = requests.Session()

    try:
        return read_stream(args, session, url)
    except KeyboardInterrupt:
        print("\nStopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
