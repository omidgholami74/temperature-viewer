<?php

declare(strict_types=1);

namespace App;

use PDO;

/**
 * Recording sessions. elapsed_ms is the wall-clock span derived from
 * started_at_ms and is what the dashboard counter shows; active_ms is the time
 * covered by samples and drives the idle time and the working rate. Neither has
 * a 24 hour ceiling.
 */
final class RunRepository
{
    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * @return array<string, mixed>|null
     */
    public function active(): ?array
    {
        $row = $this->pdo
            ->query('SELECT * FROM runs WHERE ended_at_ms IS NULL ORDER BY id DESC LIMIT 1')
            ->fetch();

        return $row === false ? null : $row;
    }

    /**
     * @return array<string, mixed>
     */
    public function start(int $startedAtMs, ?string $label = null): array
    {
        $statement = $this->pdo->prepare(
            'INSERT INTO runs (label, started_at_ms, ended_at_ms, sample_count) VALUES (?, ?, NULL, 0)',
        );
        $statement->execute([$label, $startedAtMs]);

        $run = $this->find((int) $this->pdo->lastInsertId());

        if ($run === null) {
            throw new \RuntimeException('Could not create a run');
        }

        return $run;
    }

    public function end(int $runId, int $endedAtMs): void
    {
        $statement = $this->pdo->prepare(
            'UPDATE runs SET ended_at_ms = ? WHERE id = ? AND ended_at_ms IS NULL',
        );
        $statement->execute([$endedAtMs, $runId]);
    }

    /**
     * @return array<string, mixed>|null
     */
    public function find(int $runId): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM runs WHERE id = ?');
        $statement->execute([$runId]);
        $row = $statement->fetch();

        return $row === false ? null : $row;
    }

    public function addSamples(int $runId, int $count, int $activeMs = 0): void
    {
        if ($count <= 0 && $activeMs <= 0) {
            return;
        }

        $statement = $this->pdo->prepare(
            'UPDATE runs SET sample_count = sample_count + ?, active_ms = active_ms + ? WHERE id = ?',
        );
        $statement->execute([max(0, $count), max(0, $activeMs), $runId]);
    }

    /**
     * Convert a database row to the JSON shape used by the UI.
     *
     * @param array<string, mixed>|null $row
     * @return array<string, mixed>|null
     */
    public static function toApi(?array $row, int $nowMs): ?array
    {
        if ($row === null) {
            return null;
        }

        $startedAt = (int) $row['started_at_ms'];
        $endedAt = $row['ended_at_ms'] === null ? null : (int) $row['ended_at_ms'];
        $reference = $endedAt ?? $nowMs;

        return [
            'id' => (int) $row['id'],
            'label' => $row['label'],
            'started_at_ms' => $startedAt,
            'ended_at_ms' => $endedAt,
            'elapsed_ms' => max(0, $reference - $startedAt),
            // Time covered by samples. The counter follows elapsed_ms instead,
            // so this is the idle time and the working rate, not the timer.
            'active_ms' => max(0, (int) ($row['active_ms'] ?? 0)),
            'running' => $endedAt === null,
            'sample_count' => (int) $row['sample_count'],
        ];
    }
}
