<?php

declare(strict_types=1);

namespace App;

use PDO;

/**
 * Sample storage and read queries.
 *
 * Raw rows are written one batch at a time. History is aggregated inside
 * SQLite into time buckets so the chart never receives more than a few
 * thousand points, no matter how long the run has been going.
 */
final class ReadingRepository
{
    private const RAW_TABLE = 'readings';
    private const ROLLUP_TABLE = 'readings_1s';

    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * @param list<array{t_ms: int, channels: array<int, ?float>, faults: string}> $rows
     * @param int $receivedAtMs server receive time, used for retention
     * @return int number of rows actually stored (duplicates are ignored)
     */
    public function insertBatch(int $runId, array $rows, int $receivedAtMs): int
    {
        if ($rows === []) {
            return 0;
        }

        $statement = $this->pdo->prepare(
            'INSERT OR IGNORE INTO readings (run_id, t_ms, received_at_ms, ch1, ch2, ch3, ch4, faults)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        );

        $accepted = 0;

        foreach ($rows as $row) {
            $channels = $row['channels'];

            $statement->execute([
                $runId,
                $row['t_ms'],
                $receivedAtMs,
                $channels[0] ?? null,
                $channels[1] ?? null,
                $channels[2] ?? null,
                $channels[3] ?? null,
                $row['faults'],
            ]);

            $accepted += $statement->rowCount();
        }

        return $accepted;
    }

    /**
     * @return array<string, mixed>|null
     */
    public function latest(int $runId): ?array
    {
        $statement = $this->pdo->prepare(
            'SELECT id, t_ms, received_at_ms, ch1, ch2, ch3, ch4, faults
             FROM readings WHERE run_id = ? ORDER BY id DESC LIMIT 1',
        );
        $statement->execute([$runId]);
        $row = $statement->fetch();

        return $row === false ? null : self::toApi($row);
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function since(int $runId, int $afterId, int $limit): array
    {
        $statement = $this->pdo->prepare(
            'SELECT id, t_ms, received_at_ms, ch1, ch2, ch3, ch4, faults
             FROM readings WHERE run_id = ? AND id > ? ORDER BY id ASC LIMIT ?',
        );
        $statement->bindValue(1, $runId, PDO::PARAM_INT);
        $statement->bindValue(2, $afterId, PDO::PARAM_INT);
        $statement->bindValue(3, $limit, PDO::PARAM_INT);
        $statement->execute();

        $samples = [];

        foreach ($statement->fetchAll() as $row) {
            $samples[] = self::toApi($row);
        }

        return $samples;
    }

    public function lastId(int $runId): int
    {
        $statement = $this->pdo->prepare('SELECT MAX(id) FROM readings WHERE run_id = ?');
        $statement->execute([$runId]);
        $value = $statement->fetchColumn();

        return $value === false || $value === null ? 0 : (int) $value;
    }

    public function lastReceivedAt(): ?int
    {
        $value = $this->pdo->query('SELECT MAX(received_at_ms) FROM readings')->fetchColumn();

        return $value === false || $value === null ? null : (int) $value;
    }

    /** Timestamp of the newest sample in a run, relative to the run start. */
    public function lastT(int $runId): ?int
    {
        $statement = $this->pdo->prepare('SELECT MAX(t_ms) FROM readings WHERE run_id = ?');
        $statement->execute([$runId]);
        $value = $statement->fetchColumn();

        return $value === false || $value === null ? null : (int) $value;
    }

    /**
     * Absolute wall-clock bounds of the stored samples.
     *
     * The UI uses them to default the export pickers and to offer sensible
     * "last 24 hours" ranges even when the furnace stopped days ago.
     *
     * @return array{first_ms: int, last_ms: int, runs: int}
     */
    public function bounds(): array
    {
        $runs = $this->pdo->query('SELECT id, started_at_ms FROM runs ORDER BY started_at_ms ASC')
            ->fetchAll();
        $lastStmt = $this->pdo->prepare('SELECT MAX(t_ms) FROM readings WHERE run_id = ?');

        $first = null;
        $last = null;
        $counted = 0;

        foreach ($runs as $run) {
            $lastStmt->execute([(int) $run['id']]);
            $maxT = $lastStmt->fetchColumn();

            if ($maxT === false || $maxT === null) {
                continue;
            }

            $startedAt = (int) $run['started_at_ms'];
            $end = $startedAt + (int) $maxT;
            $counted += 1;
            $first = $first === null ? $startedAt : min($first, $startedAt);
            $last = $last === null ? $end : max($last, $end);
        }

        return [
            'first_ms' => $first ?? 0,
            'last_ms' => $last ?? 0,
            'runs' => $counted,
        ];
    }

    /**
     * Per-channel min/max/average over an absolute time range.
     *
     * Runs are queried one at a time so the (run_id, t_ms) index is used and
     * long histories do not turn into a full table scan. Each extreme also
     * carries the absolute time it first occurred, because "the furnace hit
     * 1180 °C" is only useful together with *when* it did.
     *
     * @return array{samples: int, runs: int, channels: list<array{samples: int, min: ?float, max: ?float, avg: ?float, min_at_ms: ?int, max_at_ms: ?int}>}
     */
    public function stats(int $fromMs, int $toMs, bool $rollup = false): array
    {
        $table = $rollup ? self::ROLLUP_TABLE : self::RAW_TABLE;
        $statement = $this->pdo->prepare(sprintf(
            'SELECT COUNT(*) AS total,
                    COUNT(ch1) AS n1, MIN(ch1) AS min1, MAX(ch1) AS max1, AVG(ch1) AS avg1,
                    COUNT(ch2) AS n2, MIN(ch2) AS min2, MAX(ch2) AS max2, AVG(ch2) AS avg2,
                    COUNT(ch3) AS n3, MIN(ch3) AS min3, MAX(ch3) AS max3, AVG(ch3) AS avg3,
                    COUNT(ch4) AS n4, MIN(ch4) AS min4, MAX(ch4) AS max4, AVG(ch4) AS avg4
             FROM %s WHERE run_id = ? AND t_ms >= ? AND t_ms <= ?',
            $table,
        ));

        $channels = array_fill(0, 4, [
            'samples' => 0,
            'min' => null,
            'max' => null,
            'avg' => null,
            'min_at_ms' => null,
            'max_at_ms' => null,
        ]);
        $sums = [0.0, 0.0, 0.0, 0.0];
        $totals = 0;
        $runs = 0;

        foreach ($this->runs() as $run) {
            $startedAt = (int) $run['started_at_ms'];
            $fromT = max(0, $fromMs - $startedAt);
            $toT = $toMs - $startedAt;

            if ($toT < $fromT) {
                continue;
            }

            $statement->execute([(int) $run['id'], $fromT, $toT]);
            $row = $statement->fetch();

            if ($row === false || (int) $row['total'] === 0) {
                continue;
            }

            $runs += 1;
            $totals += (int) $row['total'];

            for ($index = 0; $index < 4; $index += 1) {
                $column = $index + 1;
                $n = (int) $row['n' . $column];

                if ($n <= 0) {
                    continue;
                }

                $min = (float) $row['min' . $column];
                $max = (float) $row['max' . $column];
                $channel = &$channels[$index];
                $channel['samples'] += $n;

                // Only look up the time when this run actually holds the new
                // extreme; the per-value lookup is cheap but not free.
                if ($channel['min'] === null || $min < $channel['min']) {
                    $channel['min'] = $min;
                    $minT = $this->firstTimeOfValue(
                        $table,
                        (int) $run['id'],
                        $fromT,
                        $toT,
                        $column,
                        $min,
                    );
                    $channel['min_at_ms'] = $minT === null ? null : $startedAt + $minT;
                }

                if ($channel['max'] === null || $max > $channel['max']) {
                    $channel['max'] = $max;
                    $maxT = $this->firstTimeOfValue(
                        $table,
                        (int) $run['id'],
                        $fromT,
                        $toT,
                        $column,
                        $max,
                    );
                    $channel['max_at_ms'] = $maxT === null ? null : $startedAt + $maxT;
                }

                $sums[$index] += (float) $row['avg' . $column] * $n;
                unset($channel);
            }
        }

        foreach ($channels as $index => &$channel) {
            $channel['avg'] = $channel['samples'] > 0
                ? round($sums[$index] / $channel['samples'], 2)
                : null;
            $channel['min'] = $channel['min'] === null ? null : round($channel['min'], 2);
            $channel['max'] = $channel['max'] === null ? null : round($channel['max'], 2);
        }
        unset($channel);

        return ['samples' => $totals, 'runs' => $runs, 'channels' => $channels];
    }

    /**
     * First time (relative to the run) that a channel held an exact value.
     *
     * The value always comes from the aggregate query above, so equality is a
     * bit-for-bit match on the stored REAL. Ties resolve to the earliest row,
     * which is the moment the extreme was first reached.
     */
    private function firstTimeOfValue(
        string $table,
        int $runId,
        int $fromT,
        int $toT,
        int $channel,
        float $value,
    ): ?int {
        $statement = $this->pdo->prepare(sprintf(
            'SELECT t_ms FROM %s WHERE run_id = ? AND t_ms >= ? AND t_ms <= ? AND ch%d = ?
             ORDER BY t_ms ASC LIMIT 1',
            $table,
            $channel,
        ));
        $statement->execute([$runId, $fromT, $toT, $value]);
        $found = $statement->fetchColumn();

        return $found === false || $found === null ? null : (int) $found;
    }

    /**
     * How many export rows a range would produce (used to refuse oversized
     * downloads before any bytes are sent).
     */
    public function countRange(int $fromMs, int $toMs, bool $rollup = false, int $bucketMs = 0): int
    {
        if ($bucketMs > 0) {
            // One bucket per slice is a safe upper bound; the caller only uses
            // it to enforce a row limit.
            return (int) ceil(max(0, $toMs - $fromMs) / $bucketMs) + 1;
        }

        $table = $rollup ? self::ROLLUP_TABLE : self::RAW_TABLE;
        $statement = $this->pdo->prepare(sprintf(
            'SELECT COUNT(*) FROM %s WHERE run_id = ? AND t_ms >= ? AND t_ms <= ?',
            $table,
        ));
        $total = 0;

        foreach ($this->runs() as $run) {
            $startedAt = (int) $run['started_at_ms'];
            $fromT = max(0, $fromMs - $startedAt);
            $toT = $toMs - $startedAt;

            if ($toT < $fromT) {
                continue;
            }

            $statement->execute([(int) $run['id'], $fromT, $toT]);
            $total += (int) $statement->fetchColumn();
        }

        return $total;
    }

    /**
     * Stream samples across every run that overlaps an absolute range.
     *
     * With $bucketMs = 0 the original rows are returned (fault flags included).
     * With a bucket the rows are averaged inside SQLite, which keeps a month of
     * data downloadable. Rows are yielded in chronological order, one run at a
     * time, so the caller can write them straight to the response.
     *
     * @return \Generator<int, array<string, mixed>>
     */
    public function streamRange(int $fromMs, int $toMs, bool $rollup = false, int $bucketMs = 0): \Generator
    {
        $table = $rollup ? self::ROLLUP_TABLE : self::RAW_TABLE;
        $hasFaults = !$rollup;
        $bucket = max(0, $bucketMs);

        if ($bucket > 0) {
            // Group relative to the first sample of the selected range, so the
            // CSV never starts with a bucket timestamp from before the range.
            $statement = $this->pdo->prepare(sprintf(
                'SELECT MIN(t_ms) AS first_t, COUNT(*) AS samples,
                        AVG(ch1) AS ch1, AVG(ch2) AS ch2, AVG(ch3) AS ch3, AVG(ch4) AS ch4
                 FROM %s WHERE run_id = ? AND t_ms >= ? AND t_ms <= ?
                 GROUP BY CAST((t_ms - ?) / ? AS INTEGER)
                 ORDER BY first_t ASC',
                $table,
            ));
        } else {
            $statement = $this->pdo->prepare(sprintf(
                'SELECT t_ms, ch1, ch2, ch3, ch4%s FROM %s
                 WHERE run_id = ? AND t_ms >= ? AND t_ms <= ? ORDER BY t_ms ASC',
                $hasFaults ? ', faults' : '',
                $table,
            ));
        }

        foreach ($this->runs() as $run) {
            $startedAt = (int) $run['started_at_ms'];
            $fromT = max(0, $fromMs - $startedAt);
            $toT = $toMs - $startedAt;

            if ($toT < $fromT) {
                continue;
            }

            $statement->bindValue(1, (int) $run['id'], PDO::PARAM_INT);
            $statement->bindValue(2, $fromT, PDO::PARAM_INT);
            $statement->bindValue(3, $toT, PDO::PARAM_INT);

            if ($bucket > 0) {
                $statement->bindValue(4, $fromT, PDO::PARAM_INT);
                $statement->bindValue(5, $bucket, PDO::PARAM_INT);
            }

            $statement->execute();

            // Fetch row by row: a day of raw samples must not be copied into
            // memory just to be written to the CSV response.
            while (($row = $statement->fetch()) !== false) {
                $t = $bucket > 0
                    ? $fromT + (int) (floor(((int) $row['first_t'] - $fromT) / $bucket) * $bucket)
                    : (int) $row['t_ms'];

                yield [
                    'run_id' => (int) $run['id'],
                    'run_label' => $run['label'],
                    'started_at_ms' => $startedAt,
                    't_ms' => $t,
                    'abs_ms' => $startedAt + $t,
                    'samples' => isset($row['samples']) ? (int) $row['samples'] : 1,
                    'channels' => [
                        $row['ch1'] === null ? null : (float) $row['ch1'],
                        $row['ch2'] === null ? null : (float) $row['ch2'],
                        $row['ch3'] === null ? null : (float) $row['ch3'],
                        $row['ch4'] === null ? null : (float) $row['ch4'],
                    ],
                    'faults' => $hasFaults ? (string) ($row['faults'] ?? '') : '',
                ];
            }
        }
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function runs(): array
    {
        return $this->pdo->query('SELECT id, label, started_at_ms FROM runs ORDER BY started_at_ms ASC')
            ->fetchAll();
    }

    /**
     * Averaged history for the chart.
     *
     * @return list<array{t: int, v: array{0: ?float, 1: ?float, 2: ?float, 3: ?float}}>
     */
    public function history(int $runId, int $fromMs, int $toMs, int $bucketMs, bool $rollup = false): array
    {
        $table = $rollup ? self::ROLLUP_TABLE : self::RAW_TABLE;
        $bucket = max(1, $bucketMs);

        $statement = $this->pdo->prepare(sprintf(
            'SELECT CAST(t_ms / ? AS INTEGER) * ? AS bucket,
                    AVG(ch1) AS ch1, AVG(ch2) AS ch2, AVG(ch3) AS ch3, AVG(ch4) AS ch4
             FROM %s
             WHERE run_id = ? AND t_ms >= ? AND t_ms <= ?
             GROUP BY bucket
             ORDER BY bucket ASC',
            $table,
        ));

        $statement->execute([$bucket, $bucket, $runId, $fromMs, $toMs]);

        $points = [];

        foreach ($statement->fetchAll() as $row) {
            $points[] = [
                't' => (int) $row['bucket'],
                'v' => [
                    $row['ch1'] === null ? null : round((float) $row['ch1'], 2),
                    $row['ch2'] === null ? null : round((float) $row['ch2'], 2),
                    $row['ch3'] === null ? null : round((float) $row['ch3'], 2),
                    $row['ch4'] === null ? null : round((float) $row['ch4'], 2),
                ],
            ];
        }

        return $points;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private static function toApi(array $row): array
    {
        $faults = trim((string) ($row['faults'] ?? ''));
        $faults = $faults === '' ? [] : array_map('intval', explode(',', $faults));
        $faults = array_pad($faults, 4, 0);

        return [
            'id' => (int) $row['id'],
            't_ms' => (int) $row['t_ms'],
            'received_at_ms' => (int) $row['received_at_ms'],
            'temps' => [
                $row['ch1'] === null ? null : (float) $row['ch1'],
                $row['ch2'] === null ? null : (float) $row['ch2'],
                $row['ch3'] === null ? null : (float) $row['ch3'],
                $row['ch4'] === null ? null : (float) $row['ch4'],
            ],
            'faults' => array_slice($faults, 0, 4),
        ];
    }
}
