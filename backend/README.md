# Backend — PHP API + SQLite

A dependency-free PHP 8.1+ service that stores 10 Hz furnace samples. This
folder is **API only**: the dashboard is a separate static site (`ui/`) served
by its own web server and calls this API cross-origin.

Production API: <https://apitemp.futurefounderco.com>

Production dashboard: <https://temp.futurefounderco.com>

## Requirements

- PHP 8.1 or newer
- `pdo_sqlite` and `json` extensions (`mbstring` is optional)
- Write permission for `backend/var/`
- HTTPS in production (the dashboard is served over HTTPS)

## Install and run

```bash
php bin/install.php
php -S 0.0.0.0:8080 -t . index.php
```

- API base: <http://localhost:8080/api>
- `GET /` returns a small JSON description, not HTML.
- The UI is **not** served here; use `python3 -m http.server 3000 --directory ui`
  locally or point a static host at `ui/`.

## Configuration

| Environment variable               | Default                                                              | Description                                    |
| ---------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------- |
| `FURNACE_DB`                       | `backend/var/furnace.sqlite`                                          | SQLite database file                           |
| `FURNACE_ALLOWED_ORIGINS`          | `https://temp.futurefounderco.com,http://localhost:3000,http://127.0.0.1:3000` | Comma-separated browser origins allowed by CORS |
| `FURNACE_INGEST_KEY`               | empty (open)                                                          | Value the device must send as `X-Api-Key`      |
| `FURNACE_RAW_RETENTION_HOURS`      | `48`                                                                  | Raw 10 Hz rows kept before pruning             |
| `FURNACE_AGGREGATE_RETENTION_DAYS` | `180`                                                                 | One-second rollup rows kept                    |

Configuration lives in `config.php` and only reads environment variables, so
the same files work locally and on the production host. On the API server,
override the allow-list explicitly:

```apache
SetEnv FURNACE_ALLOWED_ORIGINS "https://temp.futurefounderco.com"
SetEnv FURNACE_INGEST_KEY "change-me"
```

For PHP-FPM, set the variables in the pool (`env[...]`) or the unit
environment, not with `SetEnv` (which is Apache-only).

## CORS and write protection

The dashboard origin is different from the API origin, so the browser sends
CORS preflights:

- `GET /api/live` and `GET /api/history` are simple requests; the API echoes
  the allow-listed origin in `Access-Control-Allow-Origin`.
- `POST /api/ingest` and `POST /api/runs/reset` use JSON, so the browser first
  sends `OPTIONS`; the API answers `204` with `Access-Control-Allow-Methods`
  and `Access-Control-Allow-Headers: Content-Type, X-Api-Key`.
- Requests without an `Origin` header (Pico, curl, cron) are unaffected.
- Writes (`/api/runs/reset`) require an allow-listed origin or the API's own
  origin; any other `Origin` gets `403`. This is the CSRF boundary.
- `FURNACE_ALLOWED_ORIGINS=*` is supported for fully trusted networks, but it
  also removes the reset CSRF protection. Do not use it on the public internet.

The API does not use cookies, so `Access-Control-Allow-Credentials` is not sent.

## API

All responses are JSON. Errors use `{"error": "..."}` with a 4xx/5xx status.

### `POST /api/ingest`

Accepts 1–600 samples per request. Send `X-Api-Key` when
`FURNACE_INGEST_KEY` is configured.

```json
{
  "samples": [
    { "u": 1200, "at": 1792523131000, "t": [781.25, 660.5, 940.0, 545.75], "f": [0, 0, 0, 1] }
  ]
}
```

- `u` — device uptime in milliseconds (only differences within a batch matter,
  so a Pico reboot cannot corrupt the timeline).
- `at` — optional absolute Unix time in milliseconds. A collector that replays
  a log stored on the PC sends it so old samples keep their real times; the
  firmware does not have a clock and omits it.
- `t` — four temperatures in °C, `null` for a faulted channel. Valid range is
  `-270..1800`.
- `f` — four fault bit masks: `1` open thermocouple, `2` short to GND,
  `4` short to VCC, `8` read error.

```json
{ "ok": true, "run_id": 3, "accepted": 10, "server_time_ms": 1792523131653 }
```

Without `at`, the server anchors the batch on the newest sample's arrival and
stores the others at their uptime offsets, so network latency does not distort
the 0.1 s grid. That assumption only holds for live streaming: a backlog sent
this way is compressed into the upload time. With `at`, each sample is stored
at its own time and the first batch of a run sets `started_at_ms` to the first
sample's `at`, so an uploaded log keeps its real history.
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
`{"label": "Firing 42"}`. Requires an allow-listed `Origin`.

### `GET /api/health`

Returns schema version, raw row count and whether an ingest key is required.
The database path is never exposed.

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

Run once a minute on the API host:

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

### Apache (API host)

Point `apitemp.futurefounderco.com` at this directory, enable HTTPS, and allow
the bundled `.htaccess`:

```apache
<VirtualHost *:443>
    ServerName apitemp.futurefounderco.com
    DocumentRoot /srv/furnace/backend

    SetEnv FURNACE_ALLOWED_ORIGINS "https://temp.futurefounderco.com"
    SetEnv FURNACE_INGEST_KEY "change-me"

    <Directory /srv/furnace/backend>
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
```

`mod_rewrite` sends every request to `index.php`; `backend/var/` has its own
deny rule so the SQLite file is never downloadable.

### Nginx + PHP-FPM

```nginx
server {
    listen 443 ssl;
    server_name apitemp.futurefounderco.com;
    root /srv/furnace/backend;

    location / {
        try_files $uri /index.php?$query_string;
    }

    location = /index.php {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root/index.php;
        fastcgi_param FURNACE_ALLOWED_ORIGINS "https://temp.futurefounderco.com";
        fastcgi_param FURNACE_INGEST_KEY "change-me";
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
    }

    location ^~ /var/ { deny all; }
}
```

### Dashboard

Serve `ui/` as static files on `temp.futurefounderco.com`; do not proxy it
through this API. See [../ui/README.md](../ui/README.md#hosting).

### Security notes

- Set `FURNACE_INGEST_KEY` and the matching `API_KEY` in `module/config.py` on
  any network that is not fully trusted.
- The dashboard itself has no login. The API only allows the configured UI
  origins to write; reads from other origins are blocked by the browser but the
  data is not secret. Add a reverse-proxy login if the data is sensitive.
- Keep the SQLite file out of the web root when possible; the default location
  is protected by `var/.htaccess`, but not by every server configuration.
- Apply a Content-Security-Policy on the **UI** server, not here.

## Tests

```bash
PHP_BIN=php bash tests/smoke.sh
```

The script creates a temporary database and starts two `php -S` servers
(unkeyed and keyed). It checks ingestion, incremental live data, history,
validation errors, CORS headers and preflights, origin-checked reset, the
API-only root, maintenance and API-key enforcement.
