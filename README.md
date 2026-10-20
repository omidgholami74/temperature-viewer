# Furnace Monitor

Four-channel furnace temperature monitoring with a Raspberry Pi Pico, four
MAX31855 thermocouple amplifiers, a PHP + SQLite API and a browser dashboard.

The **UI and the API run on separate servers**:

| Service | Production URL |
| ------- | -------------- |
| Dashboard (static files) | <https://temp.futurefounderco.com> |
| API (PHP + SQLite) | <https://apitemp.futurefounderco.com> |

The browser calls the API cross-origin; the API allows only the origins listed
in `FURNACE_ALLOWED_ORIGINS` (defaults to the dashboard above).

![Furnace dashboard](artifacts/dashboard-desktop.png)

## What it does

- Samples four K-type thermocouples every **0.1 s** (10 Hz). The Pico prints
  each sample on USB serial; the PC collector stores them in a file and
  uploads that file to the API in batches.
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
┌──────────────────────────────┐       HTTPS + CORS        ┌──────────────────────────────┐
│ temp.futurefounderco.com     │  GET /api/live, /history  │ apitemp.futurefounderco.com  │
│ static dashboard (ui/)       │  POST /api/runs/reset     │ PHP API + SQLite (backend/)  │
└──────────────▲───────────────┘ ────────────────────────▶ └──────────────▲───────────────┘
               │                                                          │ POST /api/ingest
        ┌──────┴──────┐                                            ┌──────┴───────────────┐
        │ Operator    │                                            │ collector/ on a PC   │
        └─────────────┘                                            │ file → HTTP batches  │
                                                                   └──────▲───────────────┘
                                                                          │ USB serial
                                                                   ┌──────┴───────────────┐
                                                                   │ Plain Pico + 4x      │
                                                                   │ MAX31855, 10 Hz      │
                                                                   └──────────────────────┘
```

| Folder      | Contents                                                        |
| ----------- | --------------------------------------------------------------- |
| `backend/`  | PHP API only: JSON endpoints, SQLite schema, CLI tools, tests    |
| `ui/`       | Static dashboard: HTML, design tokens, CSS, ES modules, config   |
| `module/`   | Pico firmware: serial + flash log at 10 Hz, no network, simulator |
| `collector/`| PC console app: serial/file capture, checkpointed upload to API   |
| `serial-web/`| Browser dashboard reading the COM port directly (Web Serial)     |
| `tests/`    | Node unit tests for the timer and formatting helpers             |
| `artifacts/`| Dashboard screenshots                                           |

## Quick start (no hardware)

Requirements:

- PHP **8.1 or newer** with `pdo_sqlite` (and `json`, which is built in).
- Python 3.9+ for the static UI server, the simulator and the collector
  (`pyserial` is only needed for live serial capture).

```bash
# 1. create the database
php backend/bin/install.php

# 2. API server
php -S 0.0.0.0:8080 -t backend backend/index.php

# 3. UI server (second terminal), then open http://localhost:3000
python3 -m http.server 3000 --directory ui

# 4. fake furnace data (third terminal)
python3 module/simulator.py
```

Without any server at all, [`serial-web/`](serial-web/README.md) shows the four
temperatures in the same dashboard straight from the COM port.

With real hardware, step 4 is replaced by the collector, which reads the Pico
over USB serial and uploads from a file on the PC:

```bash
python -m pip install -r collector/requirements.txt
python collector/collector.py
```

Both scripts take no arguments; their settings are constants at the top of the
file. See [`collector/README.md`](collector/README.md).

The simulator takes no arguments: its `API_URL` (default
`http://127.0.0.1:8080`), `API_KEY` and intervals are constants at the top of
`module/simulator.py`.

The local UI automatically calls `http://localhost:8080` (see `ui/config.js`).
No CORS environment variable is needed locally: the API already allows
`http://localhost:3000` and `http://127.0.0.1:3000`.

### Demo history (optional)

```bash
FURNACE_DB=backend/var/demo.sqlite php backend/bin/seed_demo.php --hours=26
FURNACE_DB=backend/var/demo.sqlite php -S 0.0.0.0:8080 -t backend backend/index.php
```

The seeded run ends at the moment of seeding, so the timer starts near
`26:00:00:0` and the "All" window shows the whole firing curve.

## Hardware

- Raspberry Pi Pico — a plain board is enough, no WiFi needed.
- 4x MAX31855 breakout boards with K-type thermocouples.
- A PC connected to the Pico by USB, running `collector/`.
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

## Configuration

The backend reads environment variables; see
[backend/README.md](backend/README.md#configuration) for the full table.
The most important ones:

| Variable                | Default (production included) | Purpose                          |
| ----------------------- | ----------------------------- | -------------------------------- |
| `FURNACE_DB`            | `backend/var/furnace.sqlite`  | SQLite file                      |
| `FURNACE_INGEST_KEY`    | empty                         | Shared key the Pico must send    |
| `FURNACE_ALLOWED_ORIGINS` | dashboard + localhost origins | Browser origins allowed by CORS |

The UI reads its API origin from `ui/config.js`:

```js
window.FURNACE_CONFIG = {
  apiBaseUrl: 'https://apitemp.futurefounderco.com', // '' for same-origin
};
```

## Production deployment

1. **API** — point `apitemp.futurefounderco.com` at `backend/` with PHP-FPM and
   enable the bundled `.htaccess` (Apache) or the equivalent Nginx rewrite.
   The front controller answers only `/api/*`; `/` returns a small JSON banner.
2. **UI** — point `temp.futurefounderco.com` at `ui/` as a plain static site.
   Do not proxy API calls; the browser calls `apitemp` directly and needs CORS.
3. **CORS** — keep `FURNACE_ALLOWED_ORIGINS=https://temp.futurefounderco.com`
   on the API host (this is the built-in default). Add other origins only if
   you really serve other dashboards.
4. **TLS** — both hosts must use HTTPS. A page loaded over HTTPS cannot call an
   HTTP API (mixed content).
5. **Cron** — run `php backend/bin/maintenance.php --all` once a minute on the
   API host for rollups and retention.

Detailed Apache/Nginx examples and security notes are in
[backend/README.md](backend/README.md#deployment) and
[ui/README.md](ui/README.md#hosting).

## Tests

```bash
# API, CORS, database and maintenance smoke test (temporary database)
PHP_BIN=php bash backend/tests/smoke.sh

# timer, axis and number formatting
node --test tests/format.test.mjs
```

## Safety

This project is a monitoring prototype, **not a safety-rated furnace
controller**. Do not use it as the only over-temperature protection. Use an
independent hardware limit controller or thermal cut-out, correctly grounded
and isolated thermocouples, and wiring rated for the furnace environment.
