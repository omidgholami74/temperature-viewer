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
                'GET /api/bounds',
                'GET /api/summary?from_ms=<ms>&to_ms=<ms>',
                'GET /api/export?from_ms=<ms>&to_ms=<ms>&resolution=auto|raw|1s|1min&channels=1,2,3,4',
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

        case 'GET /bounds':
            Api::json($readings->bounds());

        case 'GET /summary':
            $bounds = $readings->bounds();
            [$fromMs, $toMs] = resolveRange($bounds);

            if ($fromMs <= 0) {
                Api::json([
                    'from_ms' => 0,
                    'to_ms' => 0,
                    'bounds' => $bounds,
                    'resolution' => 'raw',
                    'samples' => 0,
                    'runs' => 0,
                    'channels' => [],
                    'coldest' => null,
                    'hottest' => null,
                    'average' => null,
                ]);
            }

            $useRollup = ($toMs - $fromMs) > (int) $config['analysis_rollup_hours'] * 3600 * 1000;
            $stats = $readings->stats($fromMs, $toMs, $useRollup);

            // The rollup only exists after bin/maintenance.php has run; fall
            // back to raw rows so analysis works on a fresh install too.
            if ($useRollup && $stats['samples'] === 0) {
                $useRollup = false;
                $stats = $readings->stats($fromMs, $toMs, false);
            }

            $channels = [];
            $coldest = null;
            $hottest = null;
            $sum = 0.0;
            $weight = 0;

            foreach ($stats['channels'] as $index => $channel) {
                $channels[] = [
                    'channel' => $index,
                    'samples' => $channel['samples'],
                    'min' => $channel['min'],
                    'max' => $channel['max'],
                    'avg' => $channel['avg'],
                    'min_at_ms' => $channel['min_at_ms'],
                    'max_at_ms' => $channel['max_at_ms'],
                ];

                if ($channel['samples'] === 0 || $channel['min'] === null || $channel['max'] === null) {
                    continue;
                }

                if ($coldest === null || $channel['min'] < $coldest['value']) {
                    $coldest = [
                        'channel' => $index,
                        'value' => $channel['min'],
                        'at_ms' => $channel['min_at_ms'],
                    ];
                }

                if ($hottest === null || $channel['max'] > $hottest['value']) {
                    $hottest = [
                        'channel' => $index,
                        'value' => $channel['max'],
                        'at_ms' => $channel['max_at_ms'],
                    ];
                }

                $sum += (float) $channel['avg'] * $channel['samples'];
                $weight += $channel['samples'];
            }

            Api::json([
                'from_ms' => $fromMs,
                'to_ms' => $toMs,
                'bounds' => $bounds,
                'resolution' => $useRollup ? '1s' : 'raw',
                'samples' => $stats['samples'],
                'runs' => $stats['runs'],
                'channels' => $channels,
                'coldest' => $coldest,
                'hottest' => $hottest,
                'average' => $weight > 0 ? round($sum / $weight, 2) : null,
            ]);

        case 'GET /export':
            $bounds = $readings->bounds();
            [$fromMs, $toMs] = resolveRange($bounds);
            $requested = strtolower(trim((string) ($_GET['resolution'] ?? 'auto')));
            $selectedChannels = parseChannels($_GET['channels'] ?? null);

            if (!in_array($requested, ['auto', 'raw', '1s', '1min'], true)) {
                throw new ApiError(422, 'resolution must be auto, raw, 1s or 1min');
            }

            if ($fromMs <= 0) {
                throw new ApiError(404, 'No samples stored yet');
            }

            $rangeMs = $toMs - $fromMs;
            $rollupHours = (int) $config['analysis_rollup_hours'];

            if ($requested === 'auto') {
                $resolution = $rangeMs <= $rollupHours * 3600 * 1000
                    ? 'raw'
                    : ($rangeMs <= 36 * 3600 * 1000 ? '1s' : '1min');
            } else {
                $resolution = $requested;
            }

            $bucketMs = match ($resolution) {
                'raw' => 0,
                '1s' => 1000,
                default => 60000,
            };
            $useRollup = $resolution !== 'raw' && $rangeMs > $rollupHours * 3600 * 1000;
            $maxRows = (int) $config['export_max_rows'];
            $rows = $readings->countRange($fromMs, $toMs, $useRollup, $bucketMs);

            if ($rows > $maxRows) {
                throw new ApiError(413, 'This range is too large to export in one file', [
                    'resolution' => $resolution,
                    'rows' => $rows,
                    'max_rows' => $maxRows,
                    'hint' => 'Narrow the date range or choose 1 min resolution',
                ]);
            }

            $samples = $readings->streamRange($fromMs, $toMs, $useRollup, $bucketMs);
            $samples->rewind();

            // An empty rollup (maintenance has not run) must not produce an
            // empty file while raw rows exist.
            if (!$samples->valid() && $useRollup) {
                $useRollup = false;
                $rows = $readings->countRange($fromMs, $toMs, false, $bucketMs);

                if ($rows > $maxRows) {
                    throw new ApiError(413, 'This range is too large to export in one file', [
                        'resolution' => $resolution,
                        'rows' => $rows,
                        'max_rows' => $maxRows,
                        'hint' => 'Narrow the date range or choose 1 min resolution',
                    ]);
                }

                $samples = $readings->streamRange($fromMs, $toMs, false, $bucketMs);
                $samples->rewind();
            }

            // A readable file for Excel: one Tehran local-time column and one
            // temperature column per selected furnace. The raw export keeps a
            // tenth of a second so the ten rows of one second stay distinct.
            $filename = sprintf(
                'furnace-%s_to_%s.csv',
                iranStamp($fromMs, 'Ymd-Hi'),
                iranStamp($toMs, 'Ymd-Hi'),
            );
            Api::beginCsv($filename);

            $out = fopen('php://output', 'w');

            if ($out === false) {
                throw new ApiError(500, 'Could not open the response stream');
            }

            fwrite($out, "\xEF\xBB\xBF");

            $header = ['Time (Iran)'];

            foreach ($selectedChannels as $furnace) {
                $header[] = sprintf('Furnace %d (°C)', $furnace);
            }

            fputcsv($out, $header);

            // The generator was primed with rewind()/valid() above; continue
            // from its current row instead of iterating it a second time.
            while ($samples->valid()) {
                $row = $samples->current();
                $samples->next();
                $abs = (int) $row['abs_ms'];
                $channels = $row['channels'];
                $line = [iranTime($abs, $resolution === 'raw')];

                foreach ($selectedChannels as $furnace) {
                    $value = $channels[$furnace - 1] ?? null;
                    $line[] = $value === null ? '' : round((float) $value, 2);
                }

                fputcsv($out, $line);
            }

            fclose($out);
            exit;

        case 'POST /ingest':
            Api::requireIngestKey($config);

            $body = Api::readJsonBody((int) $config['max_request_bytes']);
            $samples = Api::validateSamples(
                $body['samples'] ?? null,
                (int) $config['max_batch_samples'],
            );
            $maxGapMs = (int) $config['max_sample_gap_ms'];

            $result = Database::transaction($pdo, static function (PDO $pdo) use ($runs, $readings, $samples, $now, $maxGapMs): array {
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

                // Working time is the sum of the gaps between stored samples.
                // A silence longer than maxGapMs is not counted, so the timer
                // pauses when the furnace is off instead of following the clock.
                $previousT = $readings->lastT((int) $run['id']);
                $activeMs = 0;

                foreach ($rows as $row) {
                    if ($previousT !== null && $row['t_ms'] > $previousT) {
                        $activeMs += min($row['t_ms'] - $previousT, $maxGapMs);
                    }

                    $previousT = $row['t_ms'];
                }

                $accepted = $readings->insertBatch((int) $run['id'], $rows, $now);

                // A retried batch is mostly duplicates; skipping the increment
                // keeps the timer from counting the same interval twice.
                if ($accepted !== count($rows)) {
                    $activeMs = 0;
                }

                $runs->addSamples((int) $run['id'], $accepted, $activeMs);

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
                'GET /api/bounds',
                'GET /api/summary?from_ms=<ms>&to_ms=<ms>',
                'GET /api/export?from_ms=<ms>&to_ms=<ms>&resolution=auto|raw|1s|1min&channels=1,2,3,4',
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

/**
 * Furnace numbers (1..4) selected for an export. Absent means all four.
 *
 * @return list<int>
 */
function parseChannels(mixed $raw): array
{
    if ($raw === null) {
        return [1, 2, 3, 4];
    }

    if (!is_string($raw) || trim($raw) === '') {
        throw new ApiError(422, 'channels must list at least one furnace (1 to 4)');
    }

    $selected = [];

    foreach (explode(',', $raw) as $part) {
        $part = trim($part);

        if ($part === '') {
            continue;
        }

        if (!preg_match('/^[1-4]$/', $part)) {
            throw new ApiError(422, 'channels must list furnace numbers between 1 and 4');
        }

        $selected[(int) $part] = (int) $part;
    }

    if ($selected === []) {
        throw new ApiError(422, 'channels must list at least one furnace (1 to 4)');
    }

    ksort($selected);

    return array_values($selected);
}

/**
 * Absolute milliseconds rendered in Iran local time for the CSV.
 *
 * Asia/Tehran instead of UTC keeps the sheet readable for the operator. A raw
 * export also keeps one decimal, because ten rows share each second.
 */
function iranTime(int $absoluteMs, bool $tenths): string
{
    static $zone = null;
    $zone ??= new DateTimeZone('Asia/Tehran');

    $moment = (new DateTimeImmutable('@' . intdiv($absoluteMs, 1000)))->setTimezone($zone);
    $text = $moment->format('Y-m-d H:i:s');

    return $tenths ? $text . sprintf('.%d', intdiv($absoluteMs % 1000, 100)) : $text;
}

/** Iran local time with a custom pattern, e.g. the export filename. */
function iranStamp(int $absoluteMs, string $format): string
{
    static $zone = null;
    $zone ??= new DateTimeZone('Asia/Tehran');

    return (new DateTimeImmutable('@' . intdiv($absoluteMs, 1000)))
        ->setTimezone($zone)
        ->format($format);
}

/**
 * Turn optional from_ms/to_ms query parameters into a concrete range.
 * Defaults cover every stored sample, so "All" never needs the browser to
 * guess the oldest timestamp.
 *
 * @param array{first_ms: int, last_ms: int, runs: int} $bounds
 * @return array{0: int, 1: int}
 */
function resolveRange(array $bounds): array
{
    $fromMs = Api::intParam('from_ms', null, 0);
    $toMs = Api::intParam('to_ms', null, 0);
    $fromMs ??= (int) $bounds['first_ms'];
    $toMs ??= (int) $bounds['last_ms'];

    if ($fromMs > 0 && $toMs > 0 && $toMs < $fromMs) {
        throw new ApiError(422, 'to_ms must be greater than or equal to from_ms');
    }

    return [$fromMs, $toMs];
}

/** Truncate a UTF-8 string without splitting a character. */
function cutString(string $value, int $length): string
{
    if (function_exists('mb_substr')) {
        return mb_substr($value, 0, $length);
    }

    return substr($value, 0, $length);
}
