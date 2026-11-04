# Database schema — current and target

Two schemas are documented here:

1. **As-built** — what `backend/schema.sql` creates today (SQLite).
2. **Target** — the shape that supports several devices, alerting and
   long-history analytics (PostgreSQL + TimescaleDB).

The migration path between them is in §5 and is intentionally mechanical.

---

## 1. As-built schema (SQLite)

```
runs ──1:N── readings        raw samples, 0.1 s cadence, short retention
  └───1:N── readings_1s      1 s averages written by the cron job, long retention
meta                        schema_version, last_rollup_ms
```

### `runs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | run / firing identifier |
| `label` | TEXT | free text |
| `started_at_ms` | INTEGER | absolute Unix ms; `t_ms` in `readings` is relative to this |
| `ended_at_ms` | INTEGER NULL | `NULL` = open run |
| `sample_count` | INTEGER | accepted samples |
| `active_ms` | INTEGER | measured working time (gaps longer than `max_sample_gap_ms` excluded) |

A partial unique index (`idx_runs_open WHERE ended_at_ms IS NULL`) guarantees at
most one open run, because SQLite treats `NULL`s as distinct in normal unique
indexes.

### `readings`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | monotonic; the dashboard polls `since=<id>` |
| `run_id` | INTEGER FK | `ON DELETE CASCADE` |
| `t_ms` | INTEGER | **relative** to `runs.started_at_ms` |
| `received_at_ms` | INTEGER | server clock at ingest — kept separately on purpose |
| `ch1..ch4` | REAL NULL | `NULL` means the channel faulted; never a fake value |
| `faults` | TEXT | comma-separated bitmasks, e.g. `0,0,1,0` |

Unique index on `(run_id, t_ms)`: a retried batch cannot double-count.

### `readings_1s`

`(run_id, t_ms)` primary key, `ch1..ch4` averages, `WITHOUT ROWID`. Written by
`backend/bin/maintenance.php`; raw rows are deleted after
`FURNACE_RAW_RETENTION_HOURS` (default 48 h) and the rollup is kept for
`FURNACE_AGGREGATE_RETENTION_DAYS` (default 180).

### Why relative timestamps

All times inside a run are relative to the run start, so a device clock jump
cannot reorder history, and a run can be moved without rewriting rows. The
absolute time of a row is always `runs.started_at_ms + readings.t_ms` — the
export code depends on this.

## 2. What the current schema cannot express

| Gap | Consequence |
| --- | --- |
| No `devices` / `sensors` | a second board merges into the same run; "which furnace is channel 2" is hard-coded |
| No per-channel identity | channel count, probe serial, calibration offset and name are not data |
| No alert storage | thresholds and events live only in the operator's head |
| No device health | `last_seen`, `boot_id`, `rssi`, firmware version have nowhere to go |
| No gap record | a lost batch is invisible; the chart simply has fewer points |
| Rotation by cron | retention/compression are scripts, not policies, and can silently stop |

## 3. Target schema (PostgreSQL 16 + TimescaleDB)

```
sites ──1:N── devices ──1:N── sensors
                  │                │
                  │                ├──1:N── readings        (hypertable)
                  │                └──1:N── alert_rules ──1:N── alerts
                  ├──1:N── device_events
                  └──1:N── runs ──1:N── readings
```

### `sites`

```sql
CREATE TABLE sites (
    id          text PRIMARY KEY,              -- 'razi'
    name        text NOT NULL,
    timezone    text NOT NULL DEFAULT 'Asia/Tehran'
);
```

### `devices`

```sql
CREATE TABLE devices (
    id               text PRIMARY KEY,          -- 'esp32_001', matches the MQTT segment
    site_id          text NOT NULL REFERENCES sites (id),
    name             text NOT NULL,
    location         text,
    board            text,                      -- 'ESP32-S3'
    firmware         text,
    sensor_type      text,                      -- 'MAX31855' | 'MAX31865'
    channels         smallint NOT NULL DEFAULT 4 CHECK (channels BETWEEN 1 AND 4),
    sample_interval_ms integer NOT NULL DEFAULT 100,
    boot_id          text,
    last_seq         bigint,
    last_seen        timestamptz,
    state            text NOT NULL DEFAULT 'offline'
                     CHECK (state IN ('boot','online','degraded','offline')),
    rssi             smallint,
    dropped_samples  bigint NOT NULL DEFAULT 0,
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now()
);
```

`channels` is the column that lets the brief's 3-furnace board and this repo's
4-channel board share one schema without code branches.

### `sensors`

```sql
CREATE TABLE sensors (
    id             serial PRIMARY KEY,
    device_id      text NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    channel        smallint NOT NULL CHECK (channel BETWEEN 0 AND 3),
    name           text NOT NULL,               -- 'Furnace 1'
    probe_type     text,                        -- 'K', 'PT100'
    calibration_offset_c real NOT NULL DEFAULT 0,
    min_allowed_c  real,                        -- band low, for alerting
    max_allowed_c  real,                        -- band high, for alerting
    is_enabled     boolean NOT NULL DEFAULT true,
    UNIQUE (device_id, channel)
);
```

### `readings` (hypertable)

```sql
CREATE TABLE readings (
    time         timestamptz NOT NULL,          -- absolute, UTC
    run_id       bigint      NOT NULL,
    device_id    text        NOT NULL REFERENCES devices (id),
    ch1          real,
    ch2          real,
    ch3          real,
    ch4          real,
    faults       smallint[]  NOT NULL DEFAULT '{0,0,0,0}',
    received_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device_id, time)
);

SELECT create_hypertable('readings', 'time',
       chunk_time_interval => interval '1 day');
CREATE INDEX ON readings (device_id, time DESC);
CREATE INDEX ON readings (run_id, time DESC);
```

Kept **wide** rather than one row per sensor on purpose: a frame is 1 row, not
4. At 10 Hz × 4 channels that is 864 k rows/day instead of 3.5 M, and every
query the dashboard makes wants all channels of one instant together. A device
with three sensors leaves `ch4` `NULL`.

### Continuous aggregates

```sql
CREATE MATERIALIZED VIEW readings_1s
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 second', time) AS bucket,
       device_id, run_id,
       avg(ch1) AS ch1, avg(ch2) AS ch2, avg(ch3) AS ch3, avg(ch4) AS ch4,
       count(*) AS samples
FROM readings
GROUP BY bucket, device_id, run_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('readings_1s',
       start_offset => interval '2 hours',
       end_offset   => interval '1 minute',
       schedule_interval => interval '1 minute');
```

A `readings_1min` aggregate over `readings_1s` covers months of charts without
touching raw rows.

### Policies instead of cron

```sql
ALTER TABLE readings SET (timescaledb.compress,
       timescaledb.compress_segmentby = 'device_id',
       timescaledb.compress_orderby   = 'time DESC');
SELECT add_compression_policy('readings', interval '2 days');

SELECT add_retention_policy('readings',       interval '48 hours');
SELECT add_retention_policy('readings_1s',    interval '180 days');
SELECT add_retention_policy('readings_1min',  interval '2 years');
```

This replaces `max_sample_gap_ms`-style scripted maintenance with declarative
policies; a stopped cron job can no longer quietly grow the database.

### `device_events`

```sql
CREATE TABLE device_events (
    id        bigserial PRIMARY KEY,
    device_id text NOT NULL REFERENCES devices (id),
    at        timestamptz NOT NULL,
    kind      text NOT NULL,        -- boot, connect, disconnect, gap, buffer_overflow,
                                    -- sensor_fault, sensor_recovered, ntp_sync, config_applied
    channel   smallint,
    detail    text,
    payload   jsonb
);
CREATE INDEX ON device_events (device_id, at DESC);
```

Sequence gaps become rows here (`kind = 'gap'`, `detail = 'seq 12580..12591'`),
so "the chart has a hole" always has an explanation attached.

### `alert_rules` and `alerts`

```sql
CREATE TABLE alert_rules (
    id             serial PRIMARY KEY,
    sensor_id      integer REFERENCES sensors (id) ON DELETE CASCADE,
    name           text NOT NULL,
    metric         text NOT NULL
                   CHECK (metric IN ('value','rate_of_change','stability','offline')),
    comparator     text NOT NULL CHECK (comparator IN ('gt','lt','outside_band')),
    threshold      real,
    threshold_high real,
    hysteresis     real  NOT NULL DEFAULT 2,     -- °C, prevents chatter
    for_seconds    integer NOT NULL DEFAULT 5,   -- must hold this long
    cooldown_s     integer NOT NULL DEFAULT 300, -- between two alerts
    severity       text NOT NULL DEFAULT 'warning'
                   CHECK (severity IN ('info','warning','critical')),
    is_enabled     boolean NOT NULL DEFAULT true
);

CREATE TABLE alerts (
    id          bigserial PRIMARY KEY,
    rule_id     integer NOT NULL REFERENCES alert_rules (id),
    sensor_id   integer REFERENCES sensors (id),
    device_id   text NOT NULL,
    severity    text NOT NULL,
    state       text NOT NULL CHECK (state IN ('firing','acknowledged','resolved')),
    message     text NOT NULL,
    value       real,
    opened_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    acked_by    text
);
CREATE INDEX ON alerts (state, opened_at DESC);
CREATE INDEX ON alerts (device_id, opened_at DESC);
```

One row per *episode*, not per sample: `hysteresis`, `for_seconds` and
`cooldown_s` are what stop an alarm storm when a temperature sits exactly on
the threshold. The resolved row is kept, so "how long was furnace 2 out of band
last night" is a query, not a memory.

## 4. Query shapes the schema must serve

| Screen | Query | Index used |
| --- | --- | --- |
| Live cards | latest row per device | `(device_id, time DESC)` |
| 10 min chart | `readings` bucketed to ~600 points | `(device_id, time DESC)` + raw |
| 24 h / 30 d chart | `readings_1s` / `readings_1min` | aggregate |
| Range analysis | min/max/avg per channel + time of extremes | aggregate scan |
| CSV export | streamed range, channel subset | `(device_id, time)` |
| Alert board | open alerts, newest first | `(state, opened_at DESC)` |
| Device health | `devices.last_seen` vs now | primary key |

Every one of these has a bounded result size; no endpoint may return an unbounded
row count (the current API enforces this with `max_points` and `export_max_rows`).

## 5. Migration from SQLite to PostgreSQL

Ordered, and reversible until step 6:

1. **Add columns first.** On the target DB, create `sites`, `devices`, `sensors`.
   Insert one device row (`esp32_001`, 4 channels) and four `sensors` rows.
2. **Backfill identity.** `readings` gains `device_id` and absolute `time`
   (`runs.started_at_ms + t_ms`). On SQLite, write the equivalent columns in a
   copy table and rebuild.
3. **Dual-write** from ingest into both stores for a few days; compare row
   counts per hour and the min/max per channel. This is the step that catches a
   seasonal-timezone or ms/µs error before the old store is touched.
4. **Flip reads** (dashboard, analysis, export) to the new store.
5. **Stop writing** to SQLite; keep the file read-only for one retention period.
6. **Retire** the SQLite path and the cron maintenance script.

Because every time value can be recomputed from `runs.started_at_ms + t_ms`,
and `received_at_ms` is stored alongside, the migration needs no trust in any
device clock — the one thing that cannot be reconstructed later.

## 6. Data-volume sanity check

| Volume | Raw/day | 1 s rollup/day | 1 min/day |
| --- | --- | --- | --- |
| 1 device, 4 ch, 10 Hz | 864 k rows | 86 k rows | 1.4 k rows |
| 10 devices | 8.6 M rows | 864 k rows | 14 k rows |

With TimescaleDB compression, raw rows at 10 Hz are roughly 2–4 bytes/column
after compression, so 10 devices for 48 h raw plus 180 d of 1 s data stays well
inside a single small VPS. A Raspberry Pi 5 with a USB SSD handles one device
comfortably; that is the recommended host (see
[architecture.md](architecture.md) §11).

## 7. Invariants

Rules the schema enforces, and that any future migration must preserve:

1. A faulted channel stores `NULL` plus a fault bit — never a stand-in value.
2. `(device_id, time)` is unique; retries and duplicate MQTT delivery are
   idempotent.
3. At most one open run per device.
4. `received_at` is always stored next to the device's own time; the difference
   is auditable.
5. Retention removes old rows, never silently rescales or alters stored values.
6. An alert episode keeps its `opened_at` and `resolved_at` for the life of the
   retention window; alerts are never deleted on resolve.
