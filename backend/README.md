# Backend — PHP API + SQLite

A dependency-free PHP 8.1+ service that stores 10 Hz furnace samples and serves
the dashboard. One front controller (`index.php`) routes the JSON API and the
static files from `../ui`.

## Requirements

- PHP 8.1 or newer
- `pdo_sqlite` and `json` extensions (`mbstring` is optional)
- Write permission for `backend/var/`

## Install and run

```bash
php bin/install.php
php -S 0.0.0.0:8080 -t . index.php
```

The second command serves the API **and** the dashboard on
<http://localhost:8080>. Point the document root at `backend/` when using
Apache or Nginx.

## Configuration

| Environment variable              | Default                      | Description                                     |
| --------------------------------- | ---------------------------- | ----------------------------------------------- |
| `FURNACE_DB`                      | `backend/var/furnace.sqlite` | SQLite database file                            |
| `FURNACE_UI_DIR`                  | `<repo>/ui`                  | Directory with `index.html` and assets          |
| `FURNACE_INGEST_KEY`              | empty (open)                 | Value the device must send as `X-Api-Key`       |
| `FURNACE_RAW_RETENTION_HOURS`     | `48`                         | Raw 10 Hz rows kept before pruning              |
| `FURNACE_AGGREGATE_RETENTION_DAYS`| `180`                        | One-second rollup rows kept                     |

Configuration lives in `config.php` and only reads environment variables, so
the same files work on a laptop and on a plant server.

## API

All responses are JSON. Errors use `{"error": "..."}` with a 4xx/5xx status.

### `POST /api/ingest`

Accepts 1–600 samples per request. Send `X-Api-Key` when
`FURNACE_INGEST_KEY` is configured.

```json
{
  "samples": [
    { "u": 1200, "t": [781.25, 660.5, 940.0, 545.75], "f": [0, 0, 0, 1] }
  ]
}
```

- `u` — device uptime in milliseconds (only differences within a batch matter,
  so a Pico reboot cannot corrupt the timeline).
- `t` — four temperatures in °C, `null` for a faulted channel. Valid range is
  `-270..1800`.
- `f` — four fault bit masks: `1` open thermocouple, `2` short to GND,
  `4` short to VCC, `8` read error.

Response:

```json
{ "ok": true, "run_id": 3, "accepted": 10, "server_time_ms": 1792523131653 }
```

The server anchors the batch on the newest sample and stores the others at
their uptime offsets, so network latency does not distort the 0.1 s grid.
Retries are at-least-once: a lost HTTP response can store a duplicate frame.

### `GET /api/live?since=<id>&run=<id>`

Used by the dashboard every 250 ms.

```json
{
  "server_time_ms": 1792523131653,
  "run": {
    "id": 3,
    "started_at_ms": 1792429503836,
    "ended_at_ms": null,
    "elapsed_ms": 93627817,
    "running": true,
    "sample_count": 93600
  },
  "active_run_id": 3,
  "latest": { "id": 93600, "t_ms": 93599000, "temps": [748.25, 633.28, 892.46, 514.45], "faults": [0, 0, 0, 0] },
  "samples": [],
  "last_id": 93600,
  "resync": false
}
```

`samples` contains only frames newer than `since` (up to 600). `resync: true`
tells the client to reload history instead of appending.

### `GET /api/history?window=<seconds>&max_points=<n>&run=<id>`

Returns averaged, bucketed points for the chart. `window=0` means the whole
run.

```json
{
  "run": { "id": 3, "elapsed_ms": 93627817, "running": true },
  "window_s": 600,
  "bucket_ms": 500,
  "source": "raw",
  "from_ms": 93000000,
  "to_ms": 93599000,
  "points": [{ "t": 93000000, "v": [748.2, 633.3, 892.5, 514.4] }]
}
```

- Windows up to six hours read the raw table; longer windows read the
  one-second rollup, with an automatic fallback before the first maintenance run.
- The window is anchored to the newest **recorded** sample, so a stopped Pico
  still shows its last data instead of an empty chart.
- `max_points` is capped at 2000 and defaults to 1200.

### `POST /api/runs/reset`

Starts a new run and freezes the previous one. Optional body:
`{"label": "Firing 42"}`. Cross-origin requests are rejected with `403`.

### `GET /api/health`

Database path is never exposed; returns schema version, row count and whether
an ingest key is required.

## Storage model

```
runs        one row per firing; started_at_ms drives the dashboard timer
readings    one row per 0.1 s frame: run_id, t_ms, received_at_ms, ch1..ch4, faults
readings_1s per-second averages written by the maintenance job (long retention)
meta        schema version and rollup checkpoint
```

At 10 Hz a run produces **864,000 rows per day**. Raw rows are kept for
`FURNACE_RAW_RETENTION_HOURS` (48 h by default, about 1.7 million rows) and
then replaced by the one-second rollup, which costs 86,400 rows per day.
Indexes are limited to `(run_id, t_ms)` and `received_at_ms`, and history
queries aggregate inside SQLite instead of transferring raw rows.

## Maintenance (rollup + retention)

Run once a minute:

```cron
* * * * * php /srv/furnace/backend/bin/maintenance.php --all
```

Windows Task Scheduler equivalent:

```
Program:   php.exe
Arguments: C:\furnace\backend\bin\maintenance.php --all
```

Options: `--all` (default), `--rollup`, `--prune`, `--vacuum`, `--help`.
The rollup keeps a 10 s overlap and a checkpoint in `meta`, so late batches are
still aggregated. `--vacuum` reclaims disk space after a large prune.

## Demo data

```bash
FURNACE_DB=var/demo.sqlite php bin/seed_demo.php --hours=26 --interval=1
```

The script ends the active run, creates a 26-hour demo run with realistic heat
up, soak drift and a periodic thermocouple fault, and prints the row count.
Never point it at production data.

## Deployment

### Apache

Point `DocumentRoot` at `backend/` and allow the bundled `.htaccess`:

```apache
<Directory /srv/furnace/backend>
    AllowOverride All
    Require all granted
</Directory>
```

`mod_rewrite` sends every request to `index.php`; `backend/var/` also has its
own deny rule. The dashboard files live outside the document root and are
streamed by the front controller with an allow-list of extensions.

### Nginx + PHP-FPM

```nginx
server {
    listen 80;
    server_name furnace.local;
    root /srv/furnace/backend;

    location / {
        try_files $uri /index.php?$query_string;
    }

    location = /index.php {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root/index.php;
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
    }

    location ^~ /var/ { deny all; }
}
```

### Security notes

- Set `FURNACE_INGEST_KEY` and the matching `API_KEY` in `module/config.py` on
  any network that is not fully trusted.
- The dashboard itself has no login. Put it behind a reverse-proxy login or a
  firewall rule if the plant network is shared.
- Writes are same-origin checked, responses are `no-store`, and the HTML is
  served with a strict Content-Security-Policy.
- Keep the SQLite file out of the web root when possible; the default location
  is protected by `var/.htaccess` but not by every server configuration.

## Tests

```bash
PHP_BIN=php bash tests/smoke.sh
```

The script creates a temporary database, starts two `php -S` servers
(unkeyed and keyed), and checks ingestion, incremental live data, history,
validation errors, reset + CSRF protection, static assets, traversal
protection, the maintenance job and API-key enforcement.
