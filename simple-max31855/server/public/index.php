<?php

declare(strict_types=1);

/**
 * Minimal live receiver and dashboard for simple-max31855.
 *
 * Deploy this directory as the document root of https://temp.futurefounderco.com.
 * The collector posts the same JSON batches as the main API to /api/ingest:
 *
 *   {"samples":[{"u":1200,"at":1792523131000,"t":[...],"f":[...]}]}
 *
 * There is no database and no framework. The newest 1800 samples live in
 * ../data/recent.json and are replaced atomically on each batch. That is three
 * minutes at 10 Hz, enough for the 60-second live chart and a reload.
 */

const API_CHANNELS = 4;
const MAX_BATCH = 600;
const MAX_RECENT = 1800;
const MAX_BODY_BYTES = 1048576;

$dataDir = getenv('FURNACE_DATA_DIR') ?: dirname(__DIR__) . '/data';
$recentPath = $dataDir . '/recent.json';
$lockPath = $dataDir . '/recent.lock';
$apiKey = getenv('FURNACE_INGEST_KEY') ?: '';

header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: strict-origin-when-cross-origin');

$method = strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$uri = (string) ($_SERVER['REQUEST_URI'] ?? '/');
$path = parse_url($uri, PHP_URL_PATH);
$path = is_string($path) && $path !== '' ? $path : '/';

try {
    if ($path === '/api/ingest') {
        if ($method !== 'POST') {
            method_not_allowed('POST');
        }

        require_key($apiKey);
        receive_samples($dataDir, $recentPath, $lockPath);
    }

    if ($path === '/api/recent') {
        if ($method !== 'GET') {
            method_not_allowed('GET');
        }

        send_recent($recentPath);
    }

    if ($path === '/api/health') {
        if ($method !== 'GET') {
            method_not_allowed('GET');
        }

        $state = read_state($recentPath);
        json_response([
            'ok' => true,
            'latest_id' => last_id($state),
            'samples' => count($state['samples']),
            'total_samples' => total_samples($state),
            'server_time_ms' => now_ms(),
        ]);
    }

    if ($path === '/' || $path === '/index.html') {
        serve_html();
    }

    serve_static($path);
} catch (Throwable $error) {
    error_log('simple-max31855: ' . $error->getMessage());
    json_response(['error' => 'Server error'], 500);
}

function now_ms(): int
{
    return (int) floor(microtime(true) * 1000);
}

function method_not_allowed(string $allowed): void
{
    header('Allow: ' . $allowed);
    json_response(['error' => 'Method not allowed'], 405);
}

function json_response(array $payload, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

function fail(int $status, string $message): void
{
    json_response(['error' => $message], $status);
}

function require_key(string $apiKey): void
{
    if ($apiKey === '') {
        return;
    }

    $provided = (string) ($_SERVER['HTTP_X_API_KEY'] ?? '');

    if (!hash_equals($apiKey, $provided)) {
        fail(401, 'Invalid API key');
    }
}

function read_state(string $path): array
{
    if (!is_file($path)) {
        return ['next_id' => 1, 'samples' => []];
    }

    $raw = file_get_contents($path);

    if ($raw === false) {
        return ['next_id' => 1, 'samples' => []];
    }

    $state = json_decode($raw, true);

    if (!is_array($state) || !isset($state['samples']) || !is_array($state['samples'])) {
        return ['next_id' => 1, 'samples' => []];
    }

    $state['next_id'] = max(1, (int) ($state['next_id'] ?? 1));
    $state['samples'] = array_values(array_filter($state['samples'], 'is_array'));

    return $state;
}

function last_id(array $state): int
{
    if ($state['samples'] === []) {
        return 0;
    }

    return (int) $state['samples'][count($state['samples']) - 1]['id'];
}

function total_samples(array $state): int
{
    return max(0, (int) $state['next_id'] - 1);
}

function write_state(string $path, array $state): bool
{
    $json = json_encode($state, JSON_UNESCAPED_SLASHES);

    if ($json === false) {
        return false;
    }

    $tmp = $path . '.tmp.' . getmypid();
    $handle = @fopen($tmp, 'wb');

    if ($handle === false) {
        return false;
    }

    $written = fwrite($handle, $json);

    if ($written === false || $written !== strlen($json)) {
        fclose($handle);
        @unlink($tmp);
        return false;
    }

    fflush($handle);
    fclose($handle);

    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        return false;
    }

    return true;
}

function normalise_sample(mixed $raw, int $now): ?array
{
    if (!is_array($raw)) {
        return null;
    }

    $temperatures = $raw['t'] ?? null;
    $faults = $raw['f'] ?? [];

    if (!is_array($temperatures) || count($temperatures) < 1 || count($temperatures) > API_CHANNELS) {
        return null;
    }

    if (!is_array($faults)) {
        return null;
    }

    $cleanTemperatures = [];

    foreach (array_values($temperatures) as $value) {
        if ($value === null) {
            $cleanTemperatures[] = null;
            continue;
        }

        if (!is_numeric($value)) {
            return null;
        }

        $number = (float) $value;

        if ($number < -270.0 || $number > 1800.0) {
            return null;
        }

        $cleanTemperatures[] = $number;
    }

    $cleanFaults = [];

    for ($index = 0; $index < API_CHANNELS; $index++) {
        $bits = $faults[$index] ?? 0;

        if (!is_numeric($bits) || (int) $bits < 0 || (int) $bits > 15) {
            return null;
        }

        $cleanFaults[] = (int) $bits;
    }

    $cleanTemperatures = array_pad($cleanTemperatures, API_CHANNELS, null);

    $at = $raw['at'] ?? null;

    if (!is_numeric($at) || (int) $at <= 0) {
        $at = $now;
    }

    return [
        'at' => (int) $at,
        't' => $cleanTemperatures,
        'f' => $cleanFaults,
    ];
}

function receive_samples(string $dataDir, string $recentPath, string $lockPath): void
{
    $contentType = (string) ($_SERVER['CONTENT_TYPE'] ?? '');

    if (stripos($contentType, 'application/json') !== 0) {
        fail(415, 'Content-Type must be application/json');
    }

    $body = file_get_contents('php://input');

    if ($body === false) {
        fail(400, 'Cannot read request body');
    }

    if (strlen($body) > MAX_BODY_BYTES) {
        fail(413, 'Request body is too large');
    }

    $payload = json_decode($body, true);

    if (!is_array($payload) || !isset($payload['samples']) || !is_array($payload['samples'])) {
        fail(422, 'Field "samples" must be a list');
    }

    $samples = array_values($payload['samples']);
    $count = count($samples);

    if ($count < 1 || $count > MAX_BATCH) {
        fail(422, 'Send between 1 and ' . MAX_BATCH . ' samples');
    }

    $now = now_ms();
    $normalised = [];

    foreach ($samples as $sample) {
        $clean = normalise_sample($sample, $now);

        if ($clean === null) {
            fail(422, 'Invalid sample');
        }

        $normalised[] = $clean;
    }

    if (!is_dir($dataDir) && !mkdir($dataDir, 0775, true) && !is_dir($dataDir)) {
        fail(500, 'Cannot create data directory');
    }

    $lock = fopen($lockPath, 'c');

    if ($lock === false || !flock($lock, LOCK_EX)) {
        if (is_resource($lock)) {
            fclose($lock);
        }

        fail(500, 'Cannot lock data file');
    }

    try {
        $state = read_state($recentPath);
        $nextId = max(1, (int) $state['next_id']);

        foreach ($normalised as $sample) {
            $sample['id'] = $nextId++;
            $state['samples'][] = $sample;
        }

        $state['next_id'] = $nextId;
        $state['samples'] = array_slice($state['samples'], -MAX_RECENT);

        if (!write_state($recentPath, $state)) {
            fail(500, 'Cannot write data file');
        }
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }

    json_response([
        'ok' => true,
        'accepted' => $count,
        'latest_id' => $nextId - 1,
        'total_samples' => max(0, $nextId - 1),
        'server_time_ms' => now_ms(),
    ]);
}

function send_recent(string $path): void
{
    $limit = (int) ($_GET['limit'] ?? 600);
    $limit = max(1, min(1200, $limit));
    $since = max(0, (int) ($_GET['since'] ?? 0));
    $state = read_state($path);
    $samples = $state['samples'];
    $latestId = last_id($state);

    if ($since > 0) {
        $samples = array_values(array_filter(
            $samples,
            static fn (array $sample): bool => (int) $sample['id'] > $since
        ));
    } else {
        $samples = array_slice($samples, -$limit);
    }

    if (count($samples) > $limit) {
        $samples = array_slice($samples, -$limit);
    }

    json_response([
        'samples' => $samples,
        'last_id' => $latestId,
        'total_samples' => total_samples($state),
        'server_time_ms' => now_ms(),
    ]);
}

function serve_html(): void
{
    $file = __DIR__ . '/index.html';
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-cache');
    readfile($file);
    exit;
}

function serve_static(string $path): void
{
    $types = [
        '/app.js' => 'text/javascript; charset=utf-8',
        '/style.css' => 'text/css; charset=utf-8',
        '/logo.svg' => 'image/svg+xml',
        '/favicon.svg' => 'image/svg+xml',
    ];

    if (!isset($types[$path])) {
        json_response(['error' => 'Not found'], 404);
    }

    $file = __DIR__ . $path;

    if (!is_file($file)) {
        json_response(['error' => 'Not found'], 404);
    }

    header('Content-Type: ' . $types[$path]);
    header('Cache-Control: public, max-age=300');
    readfile($file);
    exit;
}
