<?php

declare(strict_types=1);

/**
 * Roll raw samples up to one-second averages and enforce retention.
 *
 * Run it every minute from cron (or Windows Task Scheduler):
 *
 *   * * * * * php /path/to/backend/bin/maintenance.php --all
 *
 * Options:
 *   --all      rollup + prune (default when no option is given)
 *   --rollup   only aggregate raw rows into readings_1s
 *   --prune    only delete rows older than the retention settings
 *   --vacuum   checkpoint the WAL and compact the database file
 */

require __DIR__ . '/../src/autoload.php';

/** @var array<string, mixed> $config */
$config = require __DIR__ . '/../config.php';

$options = getopt('', ['all', 'rollup', 'prune', 'vacuum', 'help']);

if (isset($options['help'])) {
    fwrite(STDOUT, "Usage: php maintenance.php [--all|--rollup|--prune|--vacuum]\n");
    exit(0);
}

$runAll = isset($options['all']) || (!isset($options['rollup']) && !isset($options['prune']) && !isset($options['vacuum']));

$pdo = App\Database::connect($config);
$now = (int) floor(microtime(true) * 1000);

if ($runAll || isset($options['rollup'])) {
    $rolled = rollup($pdo, $now);
    printf("Rollup: %d one-second rows written\n", $rolled);
}

if ($runAll || isset($options['prune'])) {
    [$raw, $aggregated] = prune($pdo, $now, $config);
    printf("Pruned: %d raw rows, %d rollup rows\n", $raw, $aggregated);
}

if (isset($options['vacuum'])) {
    $pdo->exec('PRAGMA wal_checkpoint(TRUNCATE)');
    $pdo->exec('VACUUM');
    echo "Database compacted\n";
}

/**
 * Aggregate raw samples into one-second buckets. A small overlap is kept
 * between runs so late batches are still rolled up.
 */
function rollup(PDO $pdo, int $nowMs): int
{
    $checkpoint = (int) (App\Database::meta($pdo, 'last_rollup_ms') ?? 0);
    $from = max(0, $checkpoint - 10000);
    $to = $nowMs - 3000;

    if ($to <= $from) {
        return 0;
    }

    $statement = $pdo->prepare(
        "INSERT OR REPLACE INTO readings_1s (run_id, t_ms, ch1, ch2, ch3, ch4)
         SELECT run_id,
                CAST(t_ms / 1000 AS INTEGER) * 1000 AS bucket,
                AVG(ch1), AVG(ch2), AVG(ch3), AVG(ch4)
         FROM readings
         WHERE received_at_ms > ? AND received_at_ms <= ?
         GROUP BY run_id, bucket",
    );

    $statement->execute([$from, $to]);
    $written = $statement->rowCount();

    App\Database::setMeta($pdo, 'last_rollup_ms', (string) $to);

    return $written;
}

/**
 * Delete data beyond the retention window.
 *
 * @param array<string, mixed> $config
 * @return array{0: int, 1: int}
 */
function prune(PDO $pdo, int $nowMs, array $config): array
{
    $rawCutoff = $nowMs - (int) $config['raw_retention_hours'] * 3600 * 1000;
    $deleteRaw = $pdo->prepare('DELETE FROM readings WHERE received_at_ms < ?');
    $deleteRaw->execute([$rawCutoff]);
    $rawDeleted = $deleteRaw->rowCount();

    $retentionMs = (int) $config['aggregate_retention_days'] * 86400 * 1000;
    $deleteRollup = $pdo->prepare('DELETE FROM readings_1s WHERE run_id = ? AND t_ms < ?');
    $aggregatedDeleted = 0;

    foreach ($pdo->query('SELECT id, started_at_ms FROM runs') as $run) {
        $cutoff = $nowMs - $retentionMs - (int) $run['started_at_ms'];

        if ($cutoff <= 0) {
            continue;
        }

        $deleteRollup->execute([(int) $run['id'], $cutoff]);
        $aggregatedDeleted += $deleteRollup->rowCount();
    }

    return [$rawDeleted, $aggregatedDeleted];
}
