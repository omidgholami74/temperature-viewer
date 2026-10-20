#!/usr/bin/env python3
"""Console collector: store Pico samples in a file on this computer and upload them.

The plain Pico has no network stack, so the firmware prints one JSON sample per
line on USB serial. This program either:

* ``SOURCE = "serial"`` — captures that stream into ``data/samples.jsonl`` on
  this computer and uploads the file to the PHP API while it grows, or
* ``SOURCE = "file"`` — uploads a log that was already copied from the Pico
  (for example ``samples.jsonl`` downloaded with Thonny).

Uploading is incremental: the byte offset that the API has accepted is stored
in ``data/checkpoint.json``, so stopping and restarting never re-sends old data
and a network outage only delays the next batch (the file keeps the backlog).

There are no command-line arguments; edit the settings below.
Stop it with Ctrl+C.
"""

import datetime
import json
import os
import threading
import time
import urllib.error
import urllib.request

# --- Settings (edit here, no command-line arguments) ----------------------
SOURCE = "serial"  # "serial" (live Pico over USB) or "file" (a copied log)

# serial mode
SERIAL_PORT = ""  # e.g. "COM5" or "/dev/ttyACM0"; empty auto-detects a Pico
BAUD_RATE = 115200

# file mode
SOURCE_FILE = ""  # e.g. "samples.jsonl" copied from the Pico into collector/
# When the log ended, as ISO 8601 ("2026-10-20T14:05:00Z"). Empty uses the
# file's modification time, which is the copy time if the log was copied.
LOG_ENDED_AT = ""

# both modes
SPOOL_FILE = "data/samples.jsonl"  # where serial samples are stored
CHECKPOINT_FILE = "data/checkpoint.json"  # how far the API has accepted

API_URL = "http://127.0.0.1:8080"  # backend base URL, no trailing slash
API_KEY = ""  # must match FURNACE_INGEST_KEY when the API requires a key

BATCH_SIZE = 100  # samples per request
POLL_INTERVAL_S = 1.0  # pause when there is nothing new to upload
RETRY_DELAYS_S = (1, 2, 5, 10, 30)  # backoff between failed uploads

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve(path):
    """Make relative paths relative to this folder, not the shell's cwd."""
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def load_offset(source):
    try:
        with open(resolve(CHECKPOINT_FILE), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return 0

    if state.get("source") != source:
        return 0  # a different file: start from its beginning

    return int(state.get("offset", 0))


def save_offset(source, offset):
    path = resolve(CHECKPOINT_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"source": source, "offset": offset}, handle)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp, path)


def parse_sample(raw):
    """Return a validated sample dict, or None for banners and broken lines."""
    try:
        sample = json.loads(raw)
    except ValueError:
        return None

    if not isinstance(sample, dict) or "u" not in sample or "t" not in sample:
        return None

    return sample


def parse_iso8601(text):
    """ISO 8601 string to Unix milliseconds, or None when it is not valid."""
    value = text.strip()

    if not value:
        return None

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        print(f"LOG_ENDED_AT is not ISO 8601: {text!r}", flush=True)
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)

    return int(moment.timestamp() * 1000)


def clock_offset(path):
    """Milliseconds to add to each ``u`` so it becomes an absolute time.

    The newest sample in a copied log is placed at LOG_ENDED_AT, or at the
    file's modification time when that is not configured.
    """
    newest = None

    try:
        with open(path, "rb") as handle:
            for raw in handle:
                sample = parse_sample(raw)

                if sample is not None:
                    newest = sample
    except OSError as error:
        print(f"Cannot read {path}: {error}", flush=True)
        return None

    if newest is None:
        return None

    anchor = parse_iso8601(LOG_ENDED_AT) if LOG_ENDED_AT else None

    if anchor is None:
        try:
            anchor = int(os.path.getmtime(path) * 1000)
        except OSError:
            return None

    return int(anchor) - int(newest["u"])


def stamp(samples, offset):
    """Attach absolute times so the API keeps the real 10 Hz spacing."""
    if offset is None:
        return samples

    for sample in samples:
        if "at" not in sample:
            sample["at"] = int(round(sample["u"] + offset))

    return samples


def read_batch(path, offset):
    """Read complete lines from offset. Never consumes a half-written line."""
    samples = []
    skipped = 0
    position = offset

    try:
        handle = open(path, "rb")
    except OSError:
        return samples, position, skipped

    with handle:
        handle.seek(offset)

        for _ in range(BATCH_SIZE):
            raw = handle.readline()

            if not raw or not raw.endswith(b"\n"):
                break  # end of file, or a line the writer has not finished

            position = handle.tell()
            sample = parse_sample(raw.decode("utf-8", "ignore"))

            if sample is None:
                skipped += 1
            else:
                samples.append(sample)

    return samples, position, skipped


def post_batch(samples):
    """Return True, "rejected" (HTTP 4xx) or False (retry later)."""
    headers = {"Content-Type": "application/json"}

    if API_KEY:
        headers["X-Api-Key"] = API_KEY

    request = urllib.request.Request(
        API_URL.rstrip("/") + "/api/ingest",
        data=json.dumps({"samples": samples}).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        body = error.read(200).decode("utf-8", "ignore")
        print(f"API {error.code}: {body}", flush=True)

        # A rejected payload does not become valid by retrying it.
        return "rejected" if 400 <= error.code < 500 else False
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"API unreachable: {error}", flush=True)
        return False


def upload(path, keep_waiting, clock=None):
    """Upload everything new in path. With keep_waiting=False, exit when done."""
    offset = load_offset(path)
    sent = 0
    failures = 0

    while True:
        samples, position, skipped = read_batch(path, offset)

        if position == offset:
            if not keep_waiting:
                print(f"All data uploaded ({sent} samples this run).", flush=True)
                return 0

            time.sleep(POLL_INTERVAL_S)
            continue

        if skipped:
            print(f"Skipped {skipped} unreadable line(s).", flush=True)

        if not samples:
            offset = position  # only banners/blank lines; nothing to send
            save_offset(path, offset)
            continue

        result = post_batch(stamp(samples, clock))

        if result is True:
            offset = position
            save_offset(path, offset)
            sent += len(samples)
            failures = 0
            print(
                f"Sent {len(samples):3d} samples (total {sent}) | temperatures: "
                + "  ".join(
                    "--" if value is None else f"{value:6.1f}" for value in samples[-1]["t"]
                ),
                flush=True,
            )
        elif result == "rejected":
            offset = position
            save_offset(path, offset)
            print(f"Dropped {len(samples)} rejected sample(s).", flush=True)
        else:
            delay = RETRY_DELAYS_S[min(failures, len(RETRY_DELAYS_S) - 1)]
            failures += 1
            print(f"Retrying in {delay} s (data stays on disk).", flush=True)
            time.sleep(delay)


def pick_port():
    """Use the configured port or auto-detect a Pico style USB serial device."""
    if SERIAL_PORT:
        return SERIAL_PORT

    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    ports = list(list_ports.comports())

    for port in ports:
        description = f"{port.description or ''} {port.manufacturer or ''}".lower()

        if any(key in description for key in ("pico", "micropython", "usb serial", "usb-serial")):
            return port.device

    return ports[0].device if ports else None


def capture_serial(spool_path):
    """Append every JSON line from the Pico to spool_path, forever."""
    try:
        import serial
    except ImportError:
        print("pyserial is missing: python -m pip install -r collector/requirements.txt", flush=True)
        return 2

    port = pick_port()

    if not port:
        print("No serial port found. Connect the Pico or set SERIAL_PORT.", flush=True)
        return 2

    print(f"Reading {port} at {BAUD_RATE} baud into {spool_path}", flush=True)

    while True:
        # Each connection gets a fresh clock mapping; a reboot resets ``u``.
        offset = None
        last_uptime = None

        try:
            with serial.Serial(port, BAUD_RATE, timeout=0.5) as connection:
                with open(spool_path, "ab", buffering=0) as spool:
                    while True:
                        raw = connection.readline().strip()

                        if not raw.startswith(b"{"):
                            continue

                        sample = parse_sample(raw)

                        if sample is None:
                            continue

                        uptime = int(sample["u"])

                        if offset is None or (last_uptime is not None and uptime < last_uptime):
                            # First line of this connection, or the Pico rebooted.
                            offset = int(time.time() * 1000) - uptime
                            print(
                                "Clock mapped: sample uptime "
                                f"{uptime} ms is now (offset {offset} ms)",
                                flush=True,
                            )

                        last_uptime = uptime
                        sample["at"] = uptime + offset

                        # Unbuffered: the uploader sees the line immediately.
                        spool.write(json.dumps(sample).encode("utf-8") + b"\n")
        except (serial.SerialException, OSError) as error:
            print(f"Serial error: {error}. Reconnecting in 3 s.", flush=True)
            time.sleep(3)
            port = pick_port() or port


def main():
    if SOURCE == "serial":
        spool_path = resolve(SPOOL_FILE)
        os.makedirs(os.path.dirname(spool_path), exist_ok=True)
        print(f"Collecting from serial and uploading to {API_URL}", flush=True)

        threading.Thread(target=upload, args=(spool_path, True), daemon=True).start()

        try:
            return capture_serial(spool_path)
        except KeyboardInterrupt:
            print("\nStopped; unsent data stays in", spool_path, flush=True)
            return 0

    if SOURCE == "file":
        if not SOURCE_FILE:
            print("SOURCE_FILE is empty; point it at a log copied from the Pico.", flush=True)
            return 2

        source_path = resolve(SOURCE_FILE)

        if not os.path.exists(source_path):
            print(f"{source_path} does not exist.", flush=True)
            return 2

        clock = clock_offset(source_path)

        if clock is None:
            print("No usable timestamp anchor; samples will be timed on arrival.", flush=True)
        else:
            print(
                "Anchoring the newest sample to "
                + (LOG_ENDED_AT or "the file's modification time"),
                flush=True,
            )

        print(f"Uploading {source_path} to {API_URL}", flush=True)

        try:
            return upload(source_path, keep_waiting=False, clock=clock)
        except KeyboardInterrupt:
            print("\nStopped; run again to continue from the same offset.", flush=True)
            return 0

    print('SOURCE must be "serial" or "file".', flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
