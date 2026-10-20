<?php

declare(strict_types=1);

use App\Api;
use App\ApiError;
use App\Database;
use App\ReadingRepository;
use App\RunRepository;

ini_set('display_errors', '0');
ini_set('log_errors', '1');

require __DIR__ . '/src/autoload.php';

/** @var array<string, mixed> $config */
$config = require __DIR__ . '/config.php';

date_default_timezone_set('UTC');

$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$path = rawurldecode((string) (parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/'));
$path = rtrim($path, '/') ?: '/';

try {
    if ($path === '/') {
        Api::json([
            'name' => 'furnace-monitor-api',
            'status' => 'ok',
            'endpoints' => [
                'GET /api/health',
                'GET /api/live?since=<id>&run=<id>',
                'GET /api/history?window=<seconds>&max_points=<n>',
                'POST /api/ingest',
                'POST /api/runs/reset',
            ],
        ]);
    }

    if ($path === '/api' || str_starts_with($path, '/api/')) {
        handleApi($method, substr($path, 4) ?: '/', $config);
    }

    throw new ApiError(404, 'Not found');
} catch (ApiError $exception) {
    Api::json(
        array_filter([
            'error' => $exception->getMessage(),
            'details' => $exception->details,
        ], static fn (mixed $value): bool => $value !== null),
        $exception->status,
    );
} catch (Throwable $exception) {
    error_log('[furnace] ' . $exception::class . ': ' . $exception->getMessage() . ' @ ' . $exception->getFile() . ':' . $exception->getLine());

    Api::json(['error' => 'Internal server error'], 500);
}

/**
 * Dispatch a request under /api.
 *
 * @param array<string, mixed> $config
 */
function handleApi(string $method, string $route, array $config): never
{
    // The dashboard is typically hosted on a different server, so the API
    // answers CORS preflights and echoes allow-listed UI origins.
    Api::applyCors($config);

    if ($method === 'OPTIONS') {
        if (!Api::isOriginAllowed($config)) {
            throw new ApiError(403, 'Origin is not allowed');
        }

        http_response_code(204);
        exit;
    }

    $pdo = Database::connect($config);
    $runs = new RunRepository($pdo);
    $readings = new ReadingRepository($pdo);
    $now = nowMs();

    switch ($method . ' ' . $route) {
        case 'GET /health':
            $count = (int) $pdo->query('SELECT COUNT(*) FROM readings')->fetchColumn();

            Api::json([
                'ok' => true,
                'server_time_ms' => $now,
                'schema_version' => (int) (Database::meta($pdo, 'schema_version') ?? 0),
                'raw_rows' => $count,
                'ingest_key_required' => trim((string) $config['ingest_key']) !== '',
            ]);

        case 'GET /live':
            $since = Api::intParam('since', 0, 0) ?? 0;
            $requestedRunId = Api::intParam('run', null, 1);

            $active = $runs->active();
            $run = $requestedRunId !== null ? ($runs->find($requestedRunId) ?? $active) : $active;

            $runApi = RunRepository::toApi($run, $now);
            $latest = null;
            $samples = [];
            $lastId = 0;
            $limit = 600;

            if ($run !== null) {
                $runId = (int) $run['id'];
                $latest = $readings->latest($runId);
                $lastId = $readings->lastId($runId);

                if ($since > 0) {
                    $samples = $readings->since($runId, $since, $limit);
                }
            }

            Api::json([
                'server_time_ms' => $now,
                'run' => $runApi,
                'active_run_id' => $active === null ? null : (int) $active['id'],
                'latest' => $latest,
                'samples' => $samples,
                'last_id' => $lastId,
                'resync' => count($samples) >= $limit,
                'ingest' => [
                    'last_received_at_ms' => $readings->lastReceivedAt(),
                ],
            ]);

        case 'GET /history':
            $windowSeconds = Api::intParam('window', 600, 0, 31536000) ?? 600;
            $maxPoints = Api::intParam('max_points', (int) $config['default_max_points'], 50, 2000)
                ?? (int) $config['default_max_points'];
            $requestedRunId = Api::intParam('run', null, 1);

            $run = $requestedRunId !== null ? $runs->find($requestedRunId) : $runs->active();
            $run ??= $runs->active();
            $runApi = RunRepository::toApi($run, $now);

            if ($run === null) {
                Api::json([
                    'run' => null,
                    'window_s' => $windowSeconds,
                    'bucket_ms' => 0,
                    'from_ms' => 0,
                    'to_ms' => 0,
                    'points' => [],
                ]);
            }

            // Anchor the window to the newest recorded sample rather than to
            // the wall clock: when the Pico stops, "last 10 minutes" should
            // still show the last data instead of an empty chart.
            $latest = $readings->latest((int) $run['id']);
            $recordedToMs = $latest === null
                ? (int) $runApi['elapsed_ms']
                : (int) $latest['t_ms'];

            $windowMs = $windowSeconds > 0
                ? min($windowSeconds * 1000, $recordedToMs)
                : $recordedToMs;
            $toMs = $recordedToMs;
            $fromMs = max(0, $recordedToMs - $windowMs);
            $target = max(1, (int) ceil($windowMs / $maxPoints));

            $useRollup = $windowMs > 6 * 3600 * 1000;
            $bucketMs = $useRollup
                ? max(1000, (int) ceil($target / 1000) * 1000)
                : max(100, (int) ceil($target / 100) * 100);

            $points = $readings->history((int) $run['id'], $fromMs, $toMs, $bucketMs, $useRollup);

            // The rollup is filled by the maintenance job. Until it has run for
            // this range, fall back to the raw table so the chart still works.
            if ($useRollup && $points === []) {
                $useRollup = false;
                $bucketMs = max(100, (int) ceil($target / 100) * 100);
                $points = $readings->history((int) $run['id'], $fromMs, $toMs, $bucketMs, false);
            }

            Api::json([
                'run' => $runApi,
                'window_s' => $windowSeconds,
                'bucket_ms' => $bucketMs,
                'source' => $useRollup ? 'rollup' : 'raw',
                'from_ms' => $fromMs,
                'to_ms' => $toMs,
                'points' => $points,
            ]);

        case 'POST /ingest':
            Api::requireIngestKey($config);

            $body = Api::readJsonBody((int) $config['max_request_bytes']);
            $samples = Api::validateSamples(
                $body['samples'] ?? null,
                (int) $config['max_batch_samples'],
            );

            $result = Database::transaction($pdo, static function (PDO $pdo) use ($runs, $readings, $samples, $now): array {
                $lastUptime = $samples[array_key_last($samples)]['u'];
                $firstAbsolute = null;

                foreach ($samples as $sample) {
                    if ($sample['at'] !== null) {
                        $firstAbsolute = $sample['at'];
                        break;
                    }
                }

                $run = $runs->active();

                if ($run === null) {
                    // Anchor the new run to the first batch instead of losing
                    // the sub-second samples that were captured before it.
                    // A collector that replays a stored log supplies absolute
                    // times, so the run starts when the log started.
                    if ($firstAbsolute !== null) {
                        $run = $runs->start($firstAbsolute);
                    } else {
                        $span = max(0, $lastUptime - $samples[0]['u']);
                        $run = $runs->start($now - $span);
                    }
                }

                $anchor = max(0, $now - (int) $run['started_at_ms']);
                $startedAt = (int) $run['started_at_ms'];

                $rows = [];
                $previousT = -1;

                foreach ($samples as $sample) {
                    // Absolute time wins when the sender knows it; uptime is
                    // only relative to a boot and cannot survive an upload lag.
                    $t = $sample['at'] !== null
                        ? $sample['at'] - $startedAt
                        : $anchor - ($lastUptime - $sample['u']);
                    $t = max(0, (int) round($t));

                    // Keep rows strictly ordered even if the device clock jitters.
                    if ($t <= $previousT) {
                        $t = $previousT + 1;
                    }

                    $previousT = $t;

                    $rows[] = [
                        't_ms' => $t,
                        'channels' => $sample['t'],
                        'faults' => implode(',', $sample['f']),
                    ];
                }

                $accepted = $readings->insertBatch((int) $run['id'], $rows, $now);
                $runs->addSamples((int) $run['id'], $accepted);

                return ['run_id' => (int) $run['id'], 'accepted' => $accepted];
            });

            Api::json([
                'ok' => true,
                'run_id' => $result['run_id'],
                'accepted' => $result['accepted'],
                'server_time_ms' => $now,
            ]);

        case 'POST /runs/reset':
            Api::requireTrustedOrigin($config);
            $body = Api::readJsonBody((int) $config['max_request_bytes']);
            $label = isset($body['label']) && is_string($body['label'])
                ? cutString(trim($body['label']), 80)
                : '';

            /** @var array<string, mixed> $run */
            $run = Database::transaction($pdo, static function (PDO $pdo) use ($runs, $now, $label): array {
                $current = $runs->active();

                if ($current !== null) {
                    $runs->end((int) $current['id'], $now);
                }

                return $runs->start($now, $label === '' ? null : $label);
            });

            Api::json([
                'ok' => true,
                'run' => RunRepository::toApi($run, $now),
            ]);

        case 'GET /':
            Api::json([
                'name' => 'furnace-monitor-api',
                'endpoints' => [
                    'GET /api/health',
                    'GET /api/live?since=<id>&run=<id>',
                    'GET /api/history?window=<seconds>&max_points=<n>',
                    'POST /api/ingest',
                    'POST /api/runs/reset',
                ],
            ]);

        default:
            throw new ApiError(404, sprintf('Unknown API route: %s %s', $method, $route));
    }
}

function nowMs(): int
{
    return (int) floor(microtime(true) * 1000);
}

/** Truncate a UTF-8 string without splitting a character. */
function cutString(string $value, int $length): string
{
    if (function_exists('mb_substr')) {
        return mb_substr($value, 0, $length);
    }

    return substr($value, 0, $length);
}
