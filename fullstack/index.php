<?php

declare(strict_types=1);

/**
 * Single-origin Furnace Monitor.
 *
 * Document root: this directory.
 *
 *   /api/*  -> ../backend/index.php (the canonical API)
 *   /*      -> ../ui (the canonical static dashboard)
 *
 * The UI and the API normally live on two hosts, which means two certificates
 * and a CORS allow-list. Serving both from this one document root removes both
 * requirements: the browser and the firmware can use one origin.
 *
 * The UI and API sources are intentionally not copied here. They stay in their
 * canonical folders so a change to the dashboard cannot silently leave a
 * second, stale copy behind in this deployment wrapper.
 */

$uiRoot = realpath(__DIR__ . '/../ui');
$backendEntry = realpath(__DIR__ . '/../backend/index.php');

if ($uiRoot === false || $backendEntry === false) {
    http_response_code(500);
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    echo "Fullstack layout is incomplete: expected ../ui and ../backend.\n";
    exit;
}

$method = strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$uri = (string) ($_SERVER['REQUEST_URI'] ?? '/');
$parsedPath = parse_url($uri, PHP_URL_PATH);
$path = is_string($parsedPath) ? rawurldecode($parsedPath) : '/';

if ($path === '' || $path === '/') {
    $path = '/index.html';
}

/*
 * The API owns its whole namespace, including error responses. require (not
 * include) is deliberate: a missing or broken backend must be a hard failure,
 * never a blank page that looks like a dashboard without data.
 */
if ($path === '/api' || str_starts_with($path, '/api/')) {
    require $backendEntry;
    exit;
}

if ($method !== 'GET' && $method !== 'HEAD') {
    header('Allow: GET, HEAD');
    notFound(405, 'Method not allowed');
}

/*
 * The combined deployment supplies its own same-origin config. Serving it from
 * here, instead of copying a modified ui/config.js, keeps the canonical UI
 * ready for its normal two-host deployment.
 */
if ($path === '/config.js') {
    serveStatic(__DIR__ . '/config.js');
}

if (str_contains($path, "\0") || preg_match('#(?:^|/)\.#', $path) === 1) {
    notFound();
}

$file = realpath($uiRoot . $path);

if (
    $file === false
    || !is_file($file)
    || !str_starts_with($file, $uiRoot . DIRECTORY_SEPARATOR)
) {
    notFound();
}

serveStatic($file);

/**
 * Serve one file from disk with a conservative MIME type and cache policy.
 */
function serveStatic(string $file): never
{
    if (!is_file($file) || !is_readable($file)) {
        notFound();
    }

    $extension = strtolower(pathinfo($file, PATHINFO_EXTENSION));
    $types = [
        'css' => 'text/css; charset=utf-8',
        'html' => 'text/html; charset=utf-8',
        'ico' => 'image/x-icon',
        'js' => 'text/javascript; charset=utf-8',
        'json' => 'application/json; charset=utf-8',
        'map' => 'application/json; charset=utf-8',
        'png' => 'image/png',
        'svg' => 'image/svg+xml',
        'txt' => 'text/plain; charset=utf-8',
        'webmanifest' => 'application/manifest+json; charset=utf-8',
        'woff2' => 'font/woff2',
    ];
    $type = $types[$extension] ?? 'application/octet-stream';
    $size = filesize($file);
    $modified = filemtime($file);
    $size = $size === false ? 0 : $size;
    $modified = $modified === false ? time() : $modified;
    $etag = sprintf('"%x-%x"', $modified, $size);

    header('Content-Type: ' . $type);
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: strict-origin-when-cross-origin');
    header('X-Frame-Options: DENY');
    header("Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; worker-src 'self'; manifest-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
    header('ETag: ' . $etag);
    header('Last-Modified: ' . gmdate('D, d M Y H:i:s', $modified) . ' GMT');

    $cacheKey = basename($file);

    if (
        $extension === 'html'
        || $cacheKey === 'sw.js'
        || $cacheKey === 'config.js'
    ) {
        header('Cache-Control: no-cache, no-store, must-revalidate');
        header('Pragma: no-cache');
    } elseif ($extension === 'webmanifest') {
        header('Cache-Control: public, max-age=3600');
    } else {
        header('Cache-Control: public, max-age=300');
    }

    $ifNoneMatch = trim((string) ($_SERVER['HTTP_IF_NONE_MATCH'] ?? ''));

    if ($ifNoneMatch !== '' && $ifNoneMatch === $etag) {
        http_response_code(304);
        exit;
    }

    header('Content-Length: ' . $size);

    if (strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET')) === 'HEAD') {
        exit;
    }

    if (readfile($file) === false) {
        notFound();
    }

    exit;
}

/**
 * Emit a minimal text response and stop. API 404s are handled by the backend
 * before this function is reached.
 */
function notFound(int $status = 404, string $message = 'Not found'): never
{
    http_response_code($status);
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo $message . "\n";
    exit;
}
