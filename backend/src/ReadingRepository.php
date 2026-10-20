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
