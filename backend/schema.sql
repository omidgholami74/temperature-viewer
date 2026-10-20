-- Furnace monitor storage schema (SQLite).
--
-- readings      : raw samples, one row per 0.1 s frame, short retention.
-- readings_1s   : per-second averages written by bin/maintenance.php, long retention.
-- runs          : one firing / recording session. The UI timer is derived from
--                 started_at_ms, so it never wraps at 24 hours.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    sample_count  INTEGER NOT NULL DEFAULT 0
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

INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO meta (key, value) VALUES ('last_rollup_ms', '0');
