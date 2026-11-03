# Testing

Every layer has a test that runs without a furnace, and most run without
hardware at all. The suites are deliberately small and fast: they test the
contracts that hurt when they break (time handling, fault bits, batching,
retention, formatting), not every getter.

## Suites

| Suite | Command | Proves |
| --- | --- | --- |
| Backend smoke | `PHP_BIN=php bash backend/tests/smoke.sh` | Ingest, incremental live data, history, gap-capped `active_ms`, bounds, analysis, CSV exports, validation errors, CORS preflight, origin-checked reset, ingest key, maintenance |
| Dashboard units | `node --test tests/` | Timer digits and axis/number formatting, API failure diagnostics, serial port parsing |
| ESP32-S3 MicroPython | `python3 -m unittest discover -s esp32-s3/tests -v` | HTTP ingest client against a local server, RTD maths against IEC 60751 values, OTA manifest/staging/rollback and the bundle publisher |
| Arduino/C++ | `g++ -std=c++17 …` via `esp32-s3/arduino/tests/run.sh` | MAX31855/MAX31865 decoding, CDC curve, JSON body |
| Collector | `python3 -m unittest discover -s collector/tests -v` | CSV/JSON line parsing, checkpoint resume including the Windows rename fallback |
| MQTT bridge | `python3 -m unittest discover -s MQTT/bridge/tests -v` | Topic grammar, payload validation, sentinel normalisation, dedupe, reboot detection, bounded queue, retry classification, acks, status |
| MQTT transport | `python3 -m unittest discover -s MQTT/firmware/tests -v` | Batch encoding, retained will, QoS 1 publish, ack trimming, buffer overflow, reconnect bookkeeping |

Counts at the time of writing: 33 bridge tests, 11 transport tests, 22
dashboard/serial tests, 50 ESP32-S3 firmware tests and the collector suite. The
backend smoke test needs PHP and `curl`; the Arduino suite needs `g++`.

## What each suite actually covers

### Backend (`backend/tests/smoke.sh`)

Starts two `php -S` servers against temporary SQLite files (one with an ingest
key) and checks:

* a 10-sample batch is accepted, `run_id` returned, `active_ms` grows by 3.9 s
  when a 4 s silence is capped at 3 s;
* `/api/live` returns only rows after `since`, with `resync` on overflow;
* history buckets, bounds, summary extremes and ties;
* CSV export: channel selection order, empty cells for faults, 413 before
  writing an oversized file, resolution validation;
* `400/413/422` paths, CORS headers for allowed origins, `403` for a foreign
  preflight, `POST /api/runs/reset` only from an allowed origin, `401` without
  the key.

### Dashboard (`tests/*.test.mjs`)

Pure functions, no DOM: `formatDigits` carries past 99 hours and hundredths,
`formatHuman` reads naturally, `formatAxisTick` adapts to the span,
`failureText` names CORS/timeout/unreachable, and the serial port parser
handles the shapes browsers return.

### ESP32-S3 (`esp32-s3/tests/test_firmware.py`)

Runs the real `ingest.py` against a local HTTP server: batching, retry after a
failure, dropping a `422`, `X-Api-Key`, and that a failed request keeps the
queue intact. The RTD conversion is compared with published IEC 60751 points.

### Collector (`collector/tests/`)

Line parsing (`-1000.00` → `null` + fault 8), uptime→wall-clock mapping across a
burst, checkpoint offsets, and the rename fallback that Windows needs when the
spool file is open.

### MQTT (`MQTT/*/tests/`)

The bridge suite fakes both the broker and the API, so it covers behaviour a
live stack cannot easily reproduce: a duplicate `seq`, a reboot from 5000 to 0,
a queue that overflows, an API that answers 422 versus 503, and the exact ack
shape. The transport suite fakes the MQTT client and checks that a frame is
never published twice before its ack and never dropped on a failed publish.

## Manual and hardware checks

These cannot be automated in this repository:

| Check | Why | How |
| --- | --- | --- |
| Sensor accuracy | Needs a reference thermometer | Compare at a soak temperature, fill `CHANNEL_OFFSETS` |
| Wi-Fi roaming and reconnect | Needs an AP | Pull the AP, watch the console and the buffer |
| Watchdog reboot | Needs a board | Hang the loop deliberately; `boot.py` prints `WDT_RESET` |
| Browser rendering | Needs a real browser | Open `ui/index.html` and the MQTT dashboard, check both themes and a phone width |
| PWA install | Needs HTTPS or localhost | Install, kill the tab, reopen offline |
| Broker TLS | Needs certificates | `mosquitto_sub --cafile … -p 8883` |

## Running everything

```bash
# Python
python3 -m unittest discover -s esp32-s3/tests
python3 -m unittest discover -s collector/tests
python3 -m unittest discover -s MQTT/bridge/tests
python3 -m unittest discover -s MQTT/firmware/tests

# JavaScript
node --test tests/

# Arduino maths
bash esp32-s3/arduino/tests/run.sh

# API (needs PHP)
PHP_BIN=php bash backend/tests/smoke.sh
```

## Gaps

* The dashboard has no DOM test harness: timer and chart logic is exercised
  through formatting tests and manual checks only.
* The PHP API has no unit tests beyond `smoke.sh`; there is no PHPUnit setup.
* No test starts Mosquitto, so the bridge is verified against fakes rather than
  a live broker.
* Firmware on real hardware (SPI timing, probe faults, watchdog) is checked by
  hand with the console output described in
  [`docs/firmware.md`](firmware.md#boot-and-supervision).

## Related

* [System architecture](system-architecture.md)
* [Firmware](firmware.md)
* [Data model](data-model.md)
