<?php

declare(strict_types=1);

use App\Api;
use App\ApiError;
use App\DashboardSettings;
use App\Database;
use App\DeviceRepository;
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
                'GET /api/firmware/manifest?channel=<name>',
                'GET /api/firmware/file?channel=<name>&name=<file>',
                'GET /api/dashboard',
                'POST /api/dashboard',
                'GET /api/devices',
                'GET /api/devices/events?limit=<n>',
                'POST /api/devices/<id>/commands',
                'POST /api/devices/<id>/channels',
                'POST /api/devices/<id>/settings',
                'POST /api/devices/<id>/profile',
                'POST /api/firmware/upload',
                'POST /api/device/heartbeat',
                'POST /api/device/ack',
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
    $devices = new DeviceRepository($pdo);
    $dashboard = new DashboardSettings($pdo);
    $now = nowMs();

    // Public dashboard settings. Reads are open like the rest of the display
    // API; the write requires the admin key and a trusted origin.
    if ($route === '/dashboard') {
        if ($method === 'GET') {
            Api::json(['settings' => $dashboard->get()]);
        }

        if ($method !== 'POST') {
            throw new ApiError(405, 'Use GET or POST for dashboard settings');
        }

        Api::requireAdminKey($config);
        Api::requireTrustedOrigin($config);
        $body = Api::readJsonBody((int) $config['max_request_bytes']);

        Api::json(['ok' => true, 'settings' => $dashboard->save($body)]);
    }

    // Source upload is intentionally separate from OTA delivery: the operator
    // uploads a complete .py bundle here, then queues the normal OTA command.
    if ($route === '/firmware/upload') {
        if ($method !== 'POST') {
            throw new ApiError(405, 'Use POST for firmware upload');
        }

        $now = nowMs();
        Api::json(handleFirmwareUpload($config, $now));
    }

    // Device management: /devices/<id>/commands, channels, settings and profile.
    if (preg_match('#^/devices/([A-Za-z0-9_-]{1,32})/(commands|channels|settings|profile)$#', $route, $deviceRoute) === 1) {
        if ($method !== 'POST') {
            throw new ApiError(405, 'Use POST for device management');
        }

        Api::requireAdminKey($config);
        Api::requireTrustedOrigin($config);

        if ($devices->find($deviceRoute[1]) === null) {
            throw new ApiError(404, 'Unknown device');
        }

        $body = Api::readJsonBody((int) $config['max_request_bytes']);

        if ($deviceRoute[2] === 'commands') {
            $kind = isset($body['kind']) && is_string($body['kind']) ? $body['kind'] : '';
            $payload = [];

            if ($kind === 'wifi') {
                $ssid = isset($body['ssid']) && is_string($body['ssid']) ? trim($body['ssid']) : '';
                $password = isset($body['password']) && is_string($body['password']) ? $body['password'] : '';

                if ($ssid === '' || strlen($ssid) > 32) {
                    throw new ApiError(422, 'ssid must be 1 to 32 characters');
                }

                if (strlen($password) > 64) {
                    throw new ApiError(422, 'password is too long');
                }

                $payload = ['ssid' => $ssid, 'password' => $password];
            } elseif ($kind === 'ota') {
                $channel = isset($body['channel']) && is_string($body['channel']) ? $body['channel'] : '';

                if ($channel !== '' && !preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $channel)) {
                    throw new ApiError(422, 'channel must be a bundle name');
                }

                $payload = $channel === '' ? [] : ['channel' => $channel];
            } elseif ($kind !== 'restart') {
                throw new ApiError(422, 'kind must be wifi, ota or restart; use /settings for configuration');
            }

            Api::json([
                'ok' => true,
                'command' => $devices->queue($deviceRoute[1], $kind, $payload, $now),
            ]);
        }

        if ($deviceRoute[2] === 'settings') {
            $settings = validateDeviceSettings($body);
            $command = $devices->saveSettings($deviceRoute[1], $settings, $now);

            Api::json([
                'ok' => true,
                'command' => $command,
                'settings' => $devices->settings($deviceRoute[1]),
            ]);
        }

        if ($deviceRoute[2] === 'profile') {
            foreach (['display_name', 'location', 'notes'] as $field) {
                if (isset($body[$field]) && !is_string($body[$field])) {
                    throw new ApiError(422, sprintf('%s must be text', $field));
                }
            }

            $name = cutString(trim((string) ($body['display_name'] ?? '')), 60);
            $location = cutString(trim((string) ($body['location'] ?? '')), 80);
            $notes = cutString(trim((string) ($body['notes'] ?? '')), 500);

            Api::json([
                'ok' => true,
                'profile' => $devices->saveProfile($deviceRoute[1], $name, $location, $notes, $now),
            ]);
        }

        $channels = $body['channels'] ?? null;

        if (!is_array($channels) || $channels === [] || count($channels) > 4) {
            throw new ApiError(422, 'channels must list 1 to 4 entries');
        }

        $existing = [];

        foreach ($devices->channels($deviceRoute[1]) as $channel) {
            $existing[(int) $channel['channel']] = $channel;
        }

        $clean = [];
        $seen = [];

        foreach ($channels as $entry) {
            if (!is_array($entry)) {
                throw new ApiError(422, 'each channel must be an object');
            }

            $number = isset($entry['channel']) ? (int) $entry['channel'] : 0;

            if ($number < 1 || $number > 4 || isset($seen[$number])) {
                throw new ApiError(422, 'channel must be 1 to 4 and appear once');
            }

            $seen[$number] = true;
            $label = isset($entry['label']) && is_string($entry['label']) ? trim($entry['label']) : '';
            $offset = isset($existing[$number]) ? (float) $existing[$number]['offset'] : 0.0;

            if (array_key_exists('offset', $entry)) {
                if (!is_int($entry['offset']) && !is_float($entry['offset'])) {
                    throw new ApiError(422, 'channel offset must be a number');
                }

                $offset = (float) $entry['offset'];

                if (!is_finite($offset) || $offset < -200.0 || $offset > 200.0) {
                    throw new ApiError(422, 'channel offset must be between -200 and 200 °C');
                }

                $offset = round($offset, 2);
            }

            $clean[] = [
                'channel' => $number,
                'label' => cutString($label === '' ? sprintf('Furnace %d', $number) : $label, 40),
                'enabled' => !empty($entry['enabled']),
                'offset' => $offset,
            ];
        }

        $devices->setChannels($deviceRoute[1], $clean);
        $command = $devices->saveSettings($deviceRoute[1], $devices->settings($deviceRoute[1]) ?? [], $now);

        Api::json([
            'ok' => true,
            'channels' => $devices->channels($deviceRoute[1]),
            'settings' => $devices->settings($deviceRoute[1]),
            'command' => $command,
        ]);
    }

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

        case 'GET /firmware/manifest':
            Api::requireIngestKey($config);
            $firmwareChannel = firmwareChannel();
            $manifest = firmwareManifest($config, $firmwareChannel);

            header('Content-Type: application/json; charset=utf-8');
            header('Cache-Control: no-store');
            header('X-Content-Type-Options: nosniff');

            echo json_encode(
                $manifest,
                JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
            );

            exit;

        case 'GET /firmware/file':
            Api::requireIngestKey($config);
            $firmwareChannel = firmwareChannel();
            $manifest = firmwareManifest($config, $firmwareChannel);
            $requested = isset($_GET['name']) && is_string($_GET['name']) ? $_GET['name'] : '';
            $entry = null;

            foreach ($manifest['files'] as $file) {
                if (
                    is_array($file)
                    && isset($file['name'], $file['sha256'])
                    && firmwareFileName((string) $file['name']) === $requested
                ) {
                    $entry = $file;
                    break;
                }
            }

            if ($entry === null) {
                throw new ApiError(404, 'No such file in this firmware bundle');
            }

            $filePath = firmwareDirectory($config) . '/' . $firmwareChannel . '/' . $entry['name'];

            if (!is_file($filePath)) {
                throw new ApiError(404, 'The firmware file is missing on the server');
            }

            header('Content-Type: text/plain; charset=utf-8');
            header('Content-Length: ' . (string) filesize($filePath));
            header('X-Firmware-Version: ' . (string) $manifest['version']);
            header('X-Firmware-Sha256: ' . (string) $entry['sha256']);
            header('Cache-Control: no-store');
            header('X-Content-Type-Options: nosniff');

            readfile($filePath);
            exit;

        case 'POST /device/heartbeat':
            Api::requireIngestKey($config);
            $body = Api::readJsonBody((int) $config['max_request_bytes']);
            $deviceId = isset($body['id']) && is_string($body['id']) ? $body['id'] : '';

            if (!preg_match('/^[A-Za-z0-9_-]{1,32}$/', $deviceId)) {
                throw new ApiError(422, 'id must be a device id');
            }

            $present = [];

            foreach ((array) ($body['channels'] ?? []) as $channel) {
                if (is_int($channel) && $channel >= 1 && $channel <= 4) {
                    $present[$channel] = $channel;
                }
            }

            $state = $devices->heartbeat([
                'id' => $deviceId,
                'name' => cutString(trim((string) ($body['name'] ?? 'ESP32-S3')), 60),
                'source' => cutString((string) ($body['source'] ?? ''), 20),
                'firmware' => cutString((string) ($body['firmware'] ?? ''), 40),
                'ip' => cutString((string) ($body['ip'] ?? ''), 45),
                'rssi' => isset($body['rssi']) && is_int($body['rssi']) ? max(-127, min(0, $body['rssi'])) : null,
                'uptime_ms' => max(0, (int) ($body['uptime_ms'] ?? 0)),
                'sent' => max(0, (int) ($body['sent'] ?? 0)),
                'pending' => max(0, (int) ($body['pending'] ?? 0)),
                'dropped' => max(0, (int) ($body['dropped'] ?? 0)),
                'channels' => array_values($present),
            ], $now);

            Api::json(array_merge(['ok' => true, 'server_time_ms' => $now], $state));

        case 'POST /device/ack':
            Api::requireIngestKey($config);
            $body = Api::readJsonBody((int) $config['max_request_bytes']);
            $deviceId = isset($body['id']) && is_string($body['id']) ? $body['id'] : '';
            $commandId = isset($body['command_id']) ? (int) $body['command_id'] : 0;
            $status = isset($body['status']) && is_string($body['status']) ? $body['status'] : '';

            if (!preg_match('/^[A-Za-z0-9_-]{1,32}$/', $deviceId) || $commandId < 1) {
                throw new ApiError(422, 'id and command_id are required');
            }

            if (!in_array($status, ['done', 'failed'], true)) {
                throw new ApiError(422, 'status must be done or failed');
            }

            $result = cutString(trim((string) ($body['result'] ?? '')), 200);

            if (!$devices->ack($deviceId, $commandId, $status, $result, $now)) {
                throw new ApiError(404, 'Unknown command for this device');
            }

            Api::json(['ok' => true, 'server_time_ms' => $now]);

        case 'GET /devices':
            Api::json([
                'ok' => true,
                'server_time_ms' => $now,
                'online_after_ms' => (int) $config['device_online_s'] * 1000,
                'devices' => $devices->all($now),
                'bundles' => firmwareBundles($config),
            ]);

        case 'GET /devices/events':
            $limit = Api::intParam('limit', 50, 1, 200) ?? 50;
            Api::json(['ok' => true, 'events' => $devices->events($limit)]);

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
                // A silence longer than maxGapMs is not counted, so idle time
                // grows when the furnace is off; the run timer itself follows
                // the wall clock.
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
                'GET /api/firmware/manifest?channel=<name>',
                'GET /api/firmware/file?channel=<name>&name=<file>',
                'GET /api/dashboard',
                'POST /api/dashboard',
                'GET /api/devices',
                'GET /api/devices/events?limit=<n>',
                'POST /api/devices/<id>/commands',
                'POST /api/devices/<id>/channels',
                'POST /api/devices/<id>/settings',
                'POST /api/devices/<id>/profile',
                'POST /api/firmware/upload',
                'POST /api/device/heartbeat',
                'POST /api/device/ack',
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
 * Directory holding the published OTA bundles (one folder per channel).
 *
 * @param array<string, mixed> $config
 */
function firmwareDirectory(array $config): string
{
    return rtrim((string) $config['firmware_dir'], '/');
}

/**
 * The requested bundle name, validated before it touches the filesystem.
 */
function firmwareChannel(): string
{
    $channel = $_GET['channel'] ?? '';

    if (!is_string($channel) || !preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $channel)) {
        throw new ApiError(422, 'channel must be a firmware bundle name such as max31855');
    }

    return $channel;
}

/**
 * A file name that can only be a file in the bundle folder, never a path.
 */
function firmwareFileName(string $name): string
{
    if (!preg_match('/^[A-Za-z0-9_.-]{1,64}$/', $name) || strpos($name, '..') !== false) {
        return '';
    }

    return $name;
}

/**
 * Read and validate a channel's manifest.
 *
 * @param array<string, mixed> $config
 * @return array{channel: string, version: string, created_at: string, uploaded_at: string, notes: string, files: list<array<string, mixed>>}
 */
function firmwareManifest(array $config, string $channel): array
{
    $path = firmwareDirectory($config) . '/' . $channel . '/manifest.json';

    if (!is_file($path)) {
        throw new ApiError(404, 'No firmware bundle is published for this channel');
    }

    $raw = file_get_contents($path);

    if ($raw === false) {
        throw new ApiError(500, 'Could not read the firmware manifest');
    }

    try {
        $manifest = json_decode($raw, true, 8, JSON_THROW_ON_ERROR);
    } catch (JsonException) {
        throw new ApiError(500, 'The firmware manifest is not valid JSON');
    }

    if (
        !is_array($manifest)
        || !isset($manifest['version'], $manifest['files'])
        || !is_string($manifest['version'])
        || !is_array($manifest['files'])
        || !array_is_list($manifest['files'])
    ) {
        throw new ApiError(500, 'The firmware manifest is malformed');
    }

    $files = [];

    foreach ($manifest['files'] as $file) {
        if (!is_array($file) || !isset($file['name'], $file['sha256'])) {
            continue;
        }

        $name = firmwareFileName((string) $file['name']);
        $digest = strtolower((string) $file['sha256']);

        if ($name === '' || !preg_match('/^[0-9a-f]{64}$/', $digest)) {
            continue;
        }

        $files[] = [
            'name' => $name,
            'size' => is_int($file['size'] ?? null) ? $file['size'] : null,
            'sha256' => $digest,
        ];
    }

    if ($files === []) {
        throw new ApiError(500, 'The firmware manifest lists no valid files');
    }

    return [
        'channel' => $channel,
        'version' => $manifest['version'],
        'created_at' => is_string($manifest['created_at'] ?? null) ? $manifest['created_at'] : '',
        'uploaded_at' => is_string($manifest['uploaded_at'] ?? null) ? $manifest['uploaded_at'] : '',
        'notes' => is_string($manifest['notes'] ?? null) ? $manifest['notes'] : '',
        'files' => $files,
    ];
}

/**
 * Validate and publish a complete source bundle uploaded from the admin panel.
 *
 * The browser never writes into the live bundle folder directly. Files are
 * checked in a temporary directory, the manifest is generated there, and only
 * then is the channel folder swapped. A failed upload therefore leaves the
 * previous firmware servable.
 *
 * @param array<string, mixed> $config
 * @return array<string, mixed>
 */
function handleFirmwareUpload(array $config, int $now): array
{
    if (trim((string) ($config['admin_key'] ?? '')) === '') {
        throw new ApiError(503, 'Set FURNACE_ADMIN_KEY before uploading source bundles');
    }

    Api::requireAdminKey($config);
    Api::requireTrustedOrigin($config);

    $channel = isset($_POST['channel']) && is_string($_POST['channel']) ? trim($_POST['channel']) : '';

    if (!preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $channel)) {
        throw new ApiError(422, 'channel must be a bundle name such as max31855');
    }

    $version = isset($_POST['version']) && is_string($_POST['version']) ? trim($_POST['version']) : '';

    if ($version !== '' && !preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/', $version)) {
        throw new ApiError(422, 'version must start with a letter or digit and contain only letters, digits, dot, dash or underscore');
    }

    $notes = isset($_POST['notes']) && is_string($_POST['notes']) ? cutString(trim($_POST['notes']), 200) : '';
    $upload = $_FILES['files'] ?? null;

    if (!is_array($upload) || !isset($upload['name']) || !is_array($upload['name']) || $upload['name'] === []) {
        throw new ApiError(422, 'Select the .py files for the bundle');
    }

    $names = array_values($upload['name']);
    $count = count($names);
    $maxFiles = (int) ($config['max_firmware_files'] ?? 16);

    if ($count < 1 || $count > $maxFiles) {
        throw new ApiError(422, sprintf('A bundle must contain 1 to %d files', $maxFiles));
    }

    $required = [
        'boot.py',
        'wifi.py',
        'ingest.py',
        'supervisor.py',
        'ota.py',
        'device.py',
        'settings.py',
        'main.py',
    ];

    if ($channel === 'max31865') {
        $required[] = 'max31865.py';
    }

    $maxFileBytes = (int) ($config['max_firmware_file_bytes'] ?? 524288);
    $maxTotalBytes = (int) ($config['max_firmware_bytes'] ?? 2097152);
    $accepted = [];
    $total = 0;

    foreach ($names as $index => $rawName) {
        $name = firmwareFileName(is_string($rawName) ? $rawName : '');

        if ($name === '' || str_starts_with($name, '.') || !str_ends_with($name, '.py')) {
            throw new ApiError(422, 'Bundle files must have a plain .py file name');
        }

        if ($name === 'config.py' || $name === 'manifest.json') {
            throw new ApiError(422, 'config.py and manifest.json cannot be uploaded');
        }

        if (isset($accepted[$name])) {
            throw new ApiError(422, sprintf('%s was uploaded twice', $name));
        }

        $error = $upload['error'][$index] ?? UPLOAD_ERR_NO_FILE;

        if ($error !== UPLOAD_ERR_OK) {
            throw new ApiError(422, sprintf('%s did not upload cleanly (error %d)', $name, $error));
        }

        $temporary = (string) ($upload['tmp_name'][$index] ?? '');
        $size = $temporary !== '' ? filesize($temporary) : false;

        if ($size === false || $size < 1 || $size > $maxFileBytes) {
            throw new ApiError(422, sprintf('%s must be between 1 byte and %d bytes', $name, $maxFileBytes));
        }

        $total += $size;

        if ($total > $maxTotalBytes) {
            throw new ApiError(413, 'The source bundle is too large', ['max_bytes' => $maxTotalBytes]);
        }

        $accepted[$name] = ['tmp' => $temporary, 'size' => $size];
    }

    $missing = array_values(array_diff($required, array_keys($accepted)));

    if ($missing !== []) {
        throw new ApiError(422, 'The bundle is missing required files', ['missing' => $missing]);
    }

    $root = firmwareDirectory($config);

    if (!is_dir($root) && !mkdir($root, 0o775, true) && !is_dir($root)) {
        throw new ApiError(500, 'Cannot create the firmware directory');
    }

    if (!is_writable($root)) {
        throw new ApiError(500, 'The firmware directory is not writable');
    }

    $temporaryDirectory = $root . '/.upload-' . bin2hex(random_bytes(6));

    if (!mkdir($temporaryDirectory, 0o775) && !is_dir($temporaryDirectory)) {
        throw new ApiError(500, 'Cannot create a temporary firmware directory');
    }

    try {
        $entries = [];

        foreach ($accepted as $name => $file) {
            $destination = $temporaryDirectory . '/' . $name;

            if (!move_uploaded_file($file['tmp'], $destination)) {
                throw new ApiError(500, sprintf('Could not store %s', $name));
            }

            $digest = hash_file('sha256', $destination);

            if ($digest === false) {
                throw new ApiError(500, sprintf('Could not hash %s', $name));
            }

            $entries[] = [
                'name' => $name,
                'size' => $file['size'],
                'sha256' => strtolower($digest),
            ];
        }

        usort($entries, static fn (array $left, array $right): int => strcmp($left['name'], $right['name']));
        $seconds = intdiv($now, 1000);
        $version = $version !== ''
            ? $version
            : gmdate('Ymd-His', $seconds) . '-' . strtoupper(bin2hex(random_bytes(2)));
        $manifest = [
            'channel' => $channel,
            'version' => $version,
            'created_at' => gmdate('Y-m-d\TH:i:s\Z', $seconds),
            'uploaded_at' => gmdate('Y-m-d\TH:i:s\Z', $seconds),
            'notes' => $notes,
            'files' => $entries,
        ];
        $encoded = json_encode(
            $manifest,
            JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );

        if (file_put_contents($temporaryDirectory . '/manifest.json', $encoded . "\n") === false) {
            throw new ApiError(500, 'Could not write the firmware manifest');
        }

        $target = $root . '/' . $channel;
        $backup = $root . '/.' . $channel . '.previous';
        removeTree($backup);

        if (is_dir($target) && !rename($target, $backup)) {
            throw new ApiError(500, 'Could not replace the previous bundle');
        }

        if (!rename($temporaryDirectory, $target)) {
            if (is_dir($backup)) {
                rename($backup, $target);
            }

            throw new ApiError(500, 'Could not publish the uploaded bundle');
        }

        removeTree($backup);

        return [
            'ok' => true,
            'bundle' => [
                'channel' => $channel,
                'version' => $version,
                'files' => count($entries),
                'names' => array_map(static fn (array $entry): string => $entry['name'], $entries),
                'created_at' => $manifest['created_at'],
                'notes' => $notes,
            ],
            'bundles' => firmwareBundles($config),
        ];
    } catch (Throwable $exception) {
        removeTree($temporaryDirectory);
        throw $exception;
    }
}

/** Remove a temporary or previous bundle without following symlinks. */
function removeTree(string $path): void
{
    if (!is_dir($path)) {
        if (is_file($path)) {
            unlink($path);
        }

        return;
    }

    foreach (scandir($path) ?: [] as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }

        $child = $path . '/' . $entry;

        if (is_dir($child) && !is_link($child)) {
            removeTree($child);
        } else {
            unlink($child);
        }
    }

    rmdir($path);
}

/**
 * Validate the live settings endpoint and return only the fields supplied.
 *
 * @return array<string, int|float>
 */
function validateDeviceSettings(array $body): array
{
    $raw = $body['settings'] ?? $body;

    if (!is_array($raw) || (array_is_list($raw) && $raw !== [])) {
        throw new ApiError(422, 'settings must be an object');
    }

    $limits = [
        'sample_ms' => [50, 60000],
        'noise_samples' => [1, 9],
        'noise_gap_us' => [0, 50000],
        'heartbeat_s' => [5, 3600],
    ];
    $clean = [];

    foreach ($limits as $key => [$minimum, $maximum]) {
        if (!array_key_exists($key, $raw)) {
            continue;
        }

        $value = $raw[$key];

        if (!is_int($value) && !(is_float($value) && floor($value) === $value)) {
            throw new ApiError(422, sprintf('%s must be a whole number', $key));
        }

        $value = (int) $value;

        if ($value < $minimum || $value > $maximum) {
            throw new ApiError(422, sprintf('%s must be between %d and %d', $key, $minimum, $maximum));
        }

        $clean[$key] = $value;
    }

    if (array_key_exists('ema_alpha', $raw)) {
        if (!is_int($raw['ema_alpha']) && !is_float($raw['ema_alpha'])) {
            throw new ApiError(422, 'ema_alpha must be a number');
        }

        $alpha = (float) $raw['ema_alpha'];

        if (!is_finite($alpha) || $alpha < 0.05 || $alpha > 1.0) {
            throw new ApiError(422, 'ema_alpha must be between 0.05 and 1.0');
        }

        $clean['ema_alpha'] = round($alpha, 3);
    }

    if ($clean === []) {
        throw new ApiError(422, 'settings must contain sample_ms, noise_samples, noise_gap_us, ema_alpha or heartbeat_s');
    }

    return $clean;
}

/**
 * Published source bundles with their version and file names.
 *
 * @return array<string, array<string, mixed>>
 */
function firmwareBundles(array $config): array
{
    $root = firmwareDirectory($config);
    $bundles = [];

    foreach (glob($root . '/*/manifest.json') ?: [] as $path) {
        $channel = basename(dirname($path));

        try {
            $manifest = firmwareManifest($config, $channel);
        } catch (ApiError) {
            continue;
        }

        $bundles[$channel] = [
            'version' => $manifest['version'],
            'files' => count($manifest['files']),
            'names' => array_map(
                static fn (array $file): string => (string) $file['name'],
                $manifest['files'],
            ),
            'created_at' => (string) ($manifest['created_at'] ?? ''),
            'uploaded_at' => (string) ($manifest['uploaded_at'] ?? ''),
            'notes' => (string) ($manifest['notes'] ?? ''),
        ];
    }

    ksort($bundles);

    return $bundles;
}

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
