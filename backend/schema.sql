-- Furnace monitor storage schema (SQLite).
--
-- readings      : raw samples, one row per 0.1 s frame, short retention.
-- readings_1s   : per-second averages written by bin/maintenance.php, long retention.
-- runs          : one firing / recording session. active_ms is the time the
--                 furnace actually produced data: gaps without samples are not
--                 counted, so the counter cannot drift while the device is off.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    active_ms     INTEGER NOT NULL DEFAULT 0
);

-- SQLite treats NULLs as distinct in unique indexes, so index a constant
-- expression to guarantee that at most one run is open at a time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_open
    ON runs ((ended_at_ms IS NULL)) WHERE ended_at_ms IS NULL;

CREATE TABLE IF NOT EXISTS readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    t_ms            INTEGER NOT NULL,
    received_at_ms  INTEGER NOT NULL,
    ch1             REAL,
    ch2             REAL,
    ch3             REAL,
    ch4             REAL,
    faults          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_run_t
    ON readings (run_id, t_ms);

CREATE INDEX IF NOT EXISTS idx_readings_received
    ON readings (received_at_ms);

CREATE TABLE IF NOT EXISTS readings_1s (
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    t_ms   INTEGER NOT NULL,
    ch1    REAL,
    ch2    REAL,
    ch3    REAL,
    ch4    REAL,
    PRIMARY KEY (run_id, t_ms)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Boards that report in, the labels of their channels and the commands the
-- admin panel queues for them. Presence comes from the heartbeat; label and
-- enabled are operator settings and are never overwritten by a heartbeat.
CREATE TABLE IF NOT EXISTS devices (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    firmware     TEXT NOT NULL DEFAULT '',
    ip           TEXT NOT NULL DEFAULT '',
    rssi         INTEGER,
    uptime_ms    INTEGER NOT NULL DEFAULT 0,
    sent         INTEGER NOT NULL DEFAULT 0,
    pending      INTEGER NOT NULL DEFAULT 0,
    dropped      INTEGER NOT NULL DEFAULT 0,
    created_at_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_seen ON devices (last_seen_ms);

CREATE TABLE IF NOT EXISTS device_channels (
    device_id TEXT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    channel   INTEGER NOT NULL,
    label     TEXT NOT NULL,
    present   INTEGER NOT NULL DEFAULT 0,
    enabled   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (device_id, channel)
);

CREATE TABLE IF NOT EXISTS device_commands (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id      TEXT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    payload        TEXT NOT NULL,
    created_at_ms  INTEGER NOT NULL,
    delivered_at_ms INTEGER,
    ack_at_ms      INTEGER,
    status         TEXT NOT NULL DEFAULT 'queued',
    result         TEXT
);

CREATE INDEX IF NOT EXISTS idx_commands_device
    ON device_commands (device_id, status);

CREATE TABLE IF NOT EXISTS device_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    detail    TEXT,
    at_ms     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_time ON device_events (at_ms);

INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '3');
INSERT OR IGNORE INTO meta (key, value) VALUES ('last_rollup_ms', '0');
