<?php

declare(strict_types=1);

/**
 * Fill the database with a realistic firing run so the dashboard can be
 * previewed without a Pico. Never run this against production data.
 *
 *   FURNACE_DB=var/demo.sqlite php backend/bin/seed_demo.php --hours=1
 *
 * Options:
 *   --hours=1       length of the generated run
 *   --interval=0.1  seconds between generated samples, matching the 10 Hz
 *                   firmware so the working rate reads 10 /s
 *   --label=...     run label
 */

require __DIR__ . '/../src/autoload.php';

/** @var array<string, mixed> $config */
$config = require __DIR__ . '/../config.php';

$options = getopt('', ['hours::', 'interval::', 'label::', 'help']);

if (isset($options['help'])) {
    fwrite(STDOUT, "Usage: php seed_demo.php [--hours=1] [--interval=0.1] [--label=\"Demo firing\"]\n");
    exit(0);
}

$hours = max(0.05, (float) ($options['hours'] ?? 1));
$interval = max(0.1, (float) ($options['interval'] ?? 0.1));
$label = (string) ($options['label'] ?? 'Demo firing');
$intervalMs = (int) round($interval * 1000);
$durationMs = (int) round($hours * 3600 * 1000);
$totalSamples = (int) floor($durationMs / $intervalMs);

if ($totalSamples > 400000) {
    fwrite(STDERR, "Refusing to generate more than 400000 samples. Reduce --hours or raise --interval.\n");
    exit(1);
}

$pdo = App\Database::connect($config);
$now = (int) floor(microtime(true) * 1000);
$startedAt = $now - $durationMs;

$pdo->exec('BEGIN IMMEDIATE');

try {
    $active = $pdo->query('SELECT id FROM runs WHERE ended_at_ms IS NULL ORDER BY id DESC LIMIT 1')->fetchColumn();

    if ($active !== false && $active !== null) {
        $end = $pdo->prepare('UPDATE runs SET ended_at_ms = ? WHERE id = ?');
        $end->execute([$now, (int) $active]);
    }

    // The demo is generated at a fixed interval, so its recorded time is the
    // sum of those gaps. Leaving active_ms at 0 would show all of it as idle
    // time and no working rate.
    $activeMs = $totalSamples > 1 ? ($totalSamples - 1) * $intervalMs : 0;

    $insertRun = $pdo->prepare(
        'INSERT INTO runs (label, started_at_ms, ended_at_ms, sample_count, active_ms)
         VALUES (?, ?, NULL, ?, ?)',
    );
    $insertRun->execute([$label, $startedAt, $totalSamples, $activeMs]);
    $runId = (int) $pdo->lastInsertId();

    $insert = $pdo->prepare(
        'INSERT INTO readings (run_id, t_ms, received_at_ms, ch1, ch2, ch3, ch4, faults)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    );

    $targets = [780.0, 660.0, 940.0, 545.0];
    $ramps = [5400.0, 7200.0, 4200.0, 8100.0];

    for ($index = 0; $index < $totalSamples; $index++) {
        $t = $index * $intervalMs;
        $seconds = $t / 1000;
        $temps = [];
        $faults = [];

        for ($channel = 0; $channel < 4; $channel++) {
            // Demo fault: furnace 4 loses its thermocouple for 20 s every 6 h.
            if ($channel === 3 && fmod($seconds, 21600) < 20) {
                $temps[] = null;
                $faults[] = 1;
                continue;
            }

            $ramp = $ramps[$channel];
            $progress = min(1.0, $seconds / $ramp);
            $base = 25 + ($targets[$channel] - 25) * (1 - exp(-3 * $progress));
            $soak = 5.5 * sin($seconds / 2400 + $channel) + 1.6 * sin($seconds / 310 + $channel * 2);
            $noise = (mt_rand(-1000, 1000) / 1000) * 0.45;

            $temps[] = round($base + $soak + $noise, 2);
            $faults[] = 0;
        }

        $insert->execute([
            $runId,
            $t,
            $startedAt + $t,
            $temps[0],
            $temps[1],
            $temps[2],
            $temps[3],
            implode(',', $faults),
        ]);
    }

    $pdo->exec('COMMIT');
} catch (Throwable $exception) {
    $pdo->exec('ROLLBACK');
    fwrite(STDERR, 'Seeding failed: ' . $exception->getMessage() . PHP_EOL);
    exit(1);
}

printf("Demo run #%d: %.1f h, %d samples at %.1f s interval\n", $runId, $hours, $totalSamples, $interval);
printf("Database: %s\n", $config['db_path']);
echo "Run bin/maintenance.php to build the one-second rollup used by long windows.\n";
