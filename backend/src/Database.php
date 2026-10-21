<?php

declare(strict_types=1);

namespace App;

use PDO;

/**
 * SQLite connection factory with sane pragmas for a write-heavy ingest loop.
 */
final class Database
{
    private static ?PDO $connection = null;

    /**
     * @param array<string, mixed> $config
     */
    public static function connect(array $config): PDO
    {
        if (self::$connection instanceof PDO) {
            return self::$connection;
        }

        $path = (string) $config['db_path'];
        $directory = dirname($path);

        if (!is_dir($directory) && !mkdir($directory, 0o775, true) && !is_dir($directory)) {
            throw new \RuntimeException(sprintf('Cannot create database directory: %s', $directory));
        }

        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);

        // WAL allows the dashboard to read while the Pico is writing, NORMAL
        // keeps 10 Hz inserts cheap while remaining crash safe on power loss.
        $pdo->exec('PRAGMA journal_mode = WAL');
        $pdo->exec('PRAGMA synchronous = NORMAL');
        $pdo->exec('PRAGMA busy_timeout = 5000');
        $pdo->exec('PRAGMA foreign_keys = ON');
        $pdo->exec('PRAGMA temp_store = MEMORY');

        self::migrate($pdo);

        return self::$connection = $pdo;
    }

    /**
     * Apply the idempotent schema. Called on every connection, which costs
     * well under a millisecond thanks to "IF NOT EXISTS".
     *
     * Older databases predate the data-driven timer, so the run table gains
     * its active_ms column here and the existing runs are backfilled once.
     */
    public static function migrate(PDO $pdo): void
    {
        $schema = file_get_contents(dirname(__DIR__) . '/schema.sql');

        if ($schema === false) {
            throw new \RuntimeException('schema.sql is missing');
        }

        $pdo->exec($schema);

        $columns = $pdo->query('PRAGMA table_info(runs)')->fetchAll();
        $names = array_column($columns, 'name');

        if (!in_array('active_ms', $names, true)) {
            $pdo->exec('ALTER TABLE runs ADD COLUMN active_ms INTEGER NOT NULL DEFAULT 0');
            self::backfillActiveMs($pdo);
        }

        if ((int) (self::meta($pdo, 'schema_version') ?? 0) < 2) {
            self::setMeta($pdo, 'schema_version', '2');
        }
    }

    /**
     * Estimate a run's recorded time from its existing samples.
     *
     * Only gaps between consecutive samples count, capped just like ingest:
     * a long shutdown between two samples is not working time.
     */
    private static function backfillActiveMs(PDO $pdo, int $maxGapMs = 3000): void
    {
        $statement = $pdo->prepare(
            'UPDATE runs SET active_ms = (
                SELECT COALESCE(SUM(CASE WHEN d > 0 AND d <= ? THEN d ELSE 0 END), 0)
                FROM (
                    SELECT t_ms - LAG(t_ms, 1, t_ms) OVER (ORDER BY t_ms) AS d
                    FROM readings WHERE run_id = runs.id
                )
             )',
        );
        // The gap is compared with a computed column, which has no affinity:
        // binding it as text would make every integer gap compare as smaller.
        $statement->bindValue(1, $maxGapMs, PDO::PARAM_INT);
        $statement->execute();
    }

    /**
     * Execute a callback inside a write-locking transaction.
     *
     * @template T
     * @param callable(PDO): T $callback
     * @return T
     */
    public static function transaction(PDO $pdo, callable $callback): mixed
    {
        // BEGIN IMMEDIATE serialises writers, so two concurrent ingest requests
        // cannot both create an active run.
        $pdo->exec('BEGIN IMMEDIATE');

        try {
            $result = $callback($pdo);
            $pdo->exec('COMMIT');

            return $result;
        } catch (\Throwable $exception) {
            $pdo->exec('ROLLBACK');
            throw $exception;
        }
    }

    public static function meta(PDO $pdo, string $key): ?string
    {
        $statement = $pdo->prepare('SELECT value FROM meta WHERE key = ?');
        $statement->execute([$key]);
        $value = $statement->fetchColumn();

        return $value === false ? null : (string) $value;
    }

    public static function setMeta(PDO $pdo, string $key, string $value): void
    {
        $statement = $pdo->prepare(
            'INSERT INTO meta (key, value) VALUES (?, ?)
             ON CONFLICT (key) DO UPDATE SET value = excluded.value',
        );
        $statement->execute([$key, $value]);
    }
}
