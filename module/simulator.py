#!/usr/bin/env python3
"""Send simulated four-channel furnace data to the PHP API.

Useful for testing the dashboard without a Pico:

    python simulator.py --url http://127.0.0.1:8080

It samples every 0.1 s and uploads one batch per second, exactly like the
firmware. Stop it with Ctrl+C.
"""

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request

BASES = [780.0, 660.0, 940.0, 545.0]
MAX_BUFFER = 600


def post_batch(url, api_key, samples):
    headers = {"Content-Type": "application/json"}

    if api_key:
        headers["X-Api-Key"] = api_key

    request = urllib.request.Request(
        url.rstrip("/") + "/api/ingest",
        data=json.dumps({"samples": samples}).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        print(f"API {error.code}: {error.read(160).decode('utf-8', 'ignore')}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"API unreachable: {error}", file=sys.stderr)

    return False


def main():
    parser = argparse.ArgumentParser(description="Furnace API simulator")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="backend base URL")
    parser.add_argument("--key", default="", help="ingest API key, if configured")
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between samples")
    parser.add_argument("--post-every", type=float, default=1.0, help="seconds between uploads")
    args = parser.parse_args()

    interval = max(0.02, args.interval)
    print(f"Sending simulated samples to {args.url} every {interval:.2f} s. Ctrl+C to stop.")

    started = time.monotonic()
    next_sample = started
    last_post = started
    buffer = []

    try:
        while True:
            now = time.monotonic()

            if now < next_sample:
                time.sleep(min(0.01, next_sample - now))
                continue

            if now - next_sample > interval * 2:
                next_sample = now

            elapsed = now - started

            temperatures = [
                round(
                    base
                    + 55 * math.sin(elapsed / 240 + index * 1.6)
                    + 6 * math.sin(elapsed / 23 + index * 2)
                    + random.uniform(-0.7, 0.7),
                    2,
                )
                for index, base in enumerate(BASES)
            ]
            faults = [0, 0, 0, 0]

            # A short open-thermocouple event on channel 4 every five minutes.
            if int(elapsed) % 300 > 290:
                temperatures[3] = None
                faults[3] = 1

            buffer.append({"u": int(elapsed * 1000), "t": temperatures, "f": faults})
            next_sample += interval

            if now - last_post >= args.post_every or len(buffer) >= 100:
                if post_batch(args.url, args.key, buffer):
                    summary = "  ".join(
                        "--" if value is None else f"{value:6.1f}" for value in temperatures
                    )
                    print(f"{elapsed:7.1f} s  sent {len(buffer):2d}  {summary}")
                    buffer = []
                elif len(buffer) > MAX_BUFFER:
                    buffer = buffer[-MAX_BUFFER:]

                last_post = now
    except KeyboardInterrupt:
        print("\nStopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
