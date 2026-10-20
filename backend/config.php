<?php

declare(strict_types=1);

/**
 * Runtime configuration.
 *
 * Every value can be overridden with an environment variable, which keeps the
 * same code usable on a laptop, a Raspberry Pi and shared PHP hosting.
 */
return [
    // SQLite file. Keep it outside the web root when possible.
    'db_path' => getenv('FURNACE_DB') ?: __DIR__ . '/var/furnace.sqlite',

    // Browser origins allowed to call this API. The production dashboard is
    // https://temp.futurefounderco.com; the localhost entries keep local
    // development working with `python3 -m http.server 3000 --directory ui`.
    // Override with FURNACE_ALLOWED_ORIGINS on the server.
    // Use "*" only on a fully trusted network: it also allows cross-site reset.
    'allowed_origins' => (string) (getenv('FURNACE_ALLOWED_ORIGINS') ?: 'https://temp.futurefounderco.com,http://localhost:3000,http://127.0.0.1:3000'),

    // Shared secret the Pico must send with ingest requests.
    // Leave empty to disable authentication (trusted local network only).
    'ingest_key' => (string) (getenv('FURNACE_INGEST_KEY') ?: ''),

    // High resolution samples are kept for this many hours, then only the
    // one-second rollup is retained. Run bin/maintenance.php from cron.
    'raw_retention_hours' => max(1, (int) (getenv('FURNACE_RAW_RETENTION_HOURS') ?: 48)),
    'aggregate_retention_days' => max(1, (int) (getenv('FURNACE_AGGREGATE_RETENTION_DAYS') ?: 180)),

    // Limits that protect the API from oversized or malformed requests.
    'max_batch_samples' => 600,
    'max_request_bytes' => 131072,
    'default_max_points' => 1200,
];
