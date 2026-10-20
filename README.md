# Furnace Monitor

Four-channel furnace temperature monitoring with a Raspberry Pi Pico, four
MAX31855 thermocouple amplifiers, a PHP + SQLite backend and a browser
dashboard.

![Furnace dashboard](artifacts/dashboard-desktop.png)

## What it does

- Samples four K-type thermocouples every **0.1 s** (10 Hz) and uploads them
  to the API in one-second batches.
- Stores every sample in SQLite through a small JSON API. No framework, no
  external database server.
- Shows the four temperatures as colour-coded cards and as four live lines on
  one chart.
- Shows an **eight-digit run timer** `HH:MM:SS:T` above the chart (tenths of a
  second on the right) plus a human-readable equivalent below it
  ("1 day, 2 hours, 0 minutes, 18.7 seconds").
- The timer is derived from the run start time, so it **never resets at 24
  hours** and keeps counting past 99 hours.
- Long windows are averaged inside SQLite into fixed time buckets, so the chart
  stays responsive even after days of 10 Hz data.

## Architecture

```
┌────────────────────────┐    POST /api/ingest (JSON, 1 s batch)    ┌─────────────────────┐
│ Pico W + 4x MAX31855   │ ───────────────────────────────────────▶ │ PHP API (backend/)  │
│ 10 samples/s per ch.   │ ◀─────────────────────────────────────── │ SQLite, WAL mode    │
└────────────────────────┘              200 OK                       └──────────┬──────────┘
                                                                                 │ JSON
                                                                      ┌──────────▼──────────┐
                                                                      │ Web app (ui/)       │
                                                                      │ Chart.js, no build  │
                                                                      └─────────────────────┘
```

| Folder      | Contents                                                        |
| ----------- | --------------------------------------------------------------- |
| `backend/`  | PHP front controller, JSON API, SQLite schema, CLI tools, tests  |
| `module/`   | Pico firmware (WiFi + USB), serial bridge, simulator             |
| `ui/`       | Static web application: HTML, design tokens, CSS, ES modules     |
| `tests/`    | Node unit tests for the timer and formatting helpers             |
| `artifacts/`| Dashboard screenshots                                           |

The previous prototype kept a Python web server inside `ui/`. That server has
been removed; the backend now lives entirely in `backend/` and is written in
PHP.

## Quick start (no hardware)

Requirements:

- PHP **8.1 or newer** with `pdo_sqlite` (and `json`, which is built in).
- Python 3.9+ for the simulator (standard library only).

```bash
# 1. create the database
php backend/bin/install.php

# 2. serve the API and the web app on the same origin
php -S 0.0.0.0:8080 -t backend backend/index.php

# 3. in a second terminal, send fake furnace data at 10 Hz
python3 module/simulator.py --url http://127.0.0.1:8080
```

Open <http://localhost:8080>.

### Demo history (optional)

To preview multi-hour windows without waiting, seed a 26-hour firing run:

```bash
FURNACE_DB=backend/var/demo.sqlite php backend/bin/seed_demo.php --hours=26
FURNACE_DB=backend/var/demo.sqlite php -S 0.0.0.0:8080 -t backend backend/index.php
```

The seeded run ends at the moment of seeding, so the timer starts at roughly
`26:00:00:0` and the "All" window shows the whole firing curve.

## Hardware

- Raspberry Pi Pico W (WiFi firmware) or any Pico (USB firmware + bridge).
- 4x MAX31855 breakout boards with K-type thermocouples.
- Shared SPI bus, one chip-select pin per amplifier.

Wiring, firmware upload and calibration are documented in
[`module/README.md`](module/README.md).

## Timer behaviour

![Run timer](artifacts/dashboard-mobile.png)

- `HH:MM:SS:T` — hours, minutes, seconds and tenths of a second.
- The tenths group renders a dim leading zero plus the live digit, so the
  display always has eight digit positions, exactly like `00:00:00:00`.
- Hours are not masked: the field grows from `99` to `100` and beyond.
- The value comes from `runs.started_at_ms` in the database, not from a browser
  counter. Refreshing the page, rebooting the Pico, or crossing midnight does
  not reset it.
- "New run" closes the current run and starts a new one at the click time.
- Below the digits, the same duration is spelled out with days, hours, minutes
  and seconds.

## Tests

```bash
# API + database + static UI smoke test (temporary database)
PHP_BIN=php bash backend/tests/smoke.sh

# timer, axis and number formatting
node --test tests/format.test.mjs
```

## Configuration

The backend reads environment variables; see
[backend/README.md](backend/README.md#configuration) for the full table.
The most important ones:

| Variable               | Default                     | Purpose                              |
| ---------------------- | --------------------------- | ------------------------------------ |
| `FURNACE_DB`           | `backend/var/furnace.sqlite`| SQLite file                          |
| `FURNACE_INGEST_KEY`   | empty                       | Shared key the Pico must send        |
| `FURNACE_UI_DIR`       | `<repo>/ui`                 | Directory served as the web app      |

## Safety

This project is a monitoring prototype, **not a safety-rated furnace
controller**. Do not use it as the only over-temperature protection. Use an
independent hardware limit controller or thermal cut-out, correctly grounded
and isolated thermocouples, and wiring rated for the furnace environment.
