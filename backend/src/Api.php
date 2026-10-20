<?php

declare(strict_types=1);

namespace App;

use JsonException;

/**
 * Small helpers shared by every API endpoint.
 */
final class Api
{
    /**
     * Emit a JSON response and terminate the request.
     */
    public static function json(mixed $data, int $status = 200): never
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');

        echo json_encode(
            $data,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );

        exit;
    }

    /**
     * Read and decode a JSON request body.
     *
     * @return array<string, mixed>
     */
    public static function readJsonBody(int $maxBytes): array
    {
        $length = (int) ($_SERVER['CONTENT_LENGTH'] ?? 0);

        if ($length > $maxBytes) {
            throw new ApiError(413, 'Request body is too large', ['max_bytes' => $maxBytes]);
        }

        $raw = file_get_contents('php://input');

        if ($raw === false) {
            throw new ApiError(400, 'Could not read request body');
        }

        if (trim($raw) === '') {
            return [];
        }

        try {
            $decoded = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
        } catch (JsonException $exception) {
            throw new ApiError(400, 'Malformed JSON body', ['reason' => $exception->getMessage()]);
        }

        // An empty JSON object decodes to an empty array, which array_is_list()
        // also reports as a list, so allow that one case explicitly.
        if (!is_array($decoded) || (array_is_list($decoded) && $decoded !== [])) {
            throw new ApiError(400, 'JSON body must be an object');
        }

        return $decoded;
    }

    /**
     * CORS configuration as a list of full origins (scheme + host + port).
     * The special value "*" allows any origin.
     *
     * @param array<string, mixed> $config
     * @return list<string>
     */
    public static function allowedOrigins(array $config): array
    {
        $raw = trim((string) ($config['allowed_origins'] ?? ''));

        if ($raw === '') {
            return [];
        }

        $origins = [];

        foreach (explode(',', $raw) as $origin) {
            $origin = rtrim(trim($origin), '/');

            if ($origin !== '') {
                $origins[] = $origin;
            }
        }

        return array_values(array_unique($origins));
    }

    /**
     * Emit CORS headers when the browser origin is allowed.
     *
     * The dashboard is normally served by a different static server than this
     * API, so the UI origin must be listed in FURNACE_ALLOWED_ORIGINS.
     *
     * @param array<string, mixed> $config
     */
    public static function applyCors(array $config): void
    {
        $origin = (string) ($_SERVER['HTTP_ORIGIN'] ?? '');

        if ($origin === '') {
            return;
        }

        $allowed = self::allowedOrigins($config);

        if ($allowed === []) {
            return;
        }

        if (in_array('*', $allowed, true)) {
            header('Access-Control-Allow-Origin: *');
        } elseif (in_array($origin, $allowed, true)) {
            header('Access-Control-Allow-Origin: ' . $origin);
            header('Vary: Origin');
        } else {
            return;
        }

        header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
        header('Access-Control-Allow-Headers: Content-Type, X-Api-Key');
        header('Access-Control-Max-Age: 600');
    }

    /**
     * True when the current request may read or write.
     *
     * Requests without an Origin header (Pico, curl, cron) are always allowed.
     * Browser requests must either come from an allow-listed UI origin or from
     * the same host and port as this API.
     *
     * @param array<string, mixed> $config
     */
    public static function isOriginAllowed(array $config): bool
    {
        $origin = (string) ($_SERVER['HTTP_ORIGIN'] ?? '');

        if ($origin === '') {
            return true;
        }

        $allowed = self::allowedOrigins($config);

        if (in_array('*', $allowed, true) || in_array($origin, $allowed, true)) {
            return true;
        }

        return self::isSameServerOrigin($origin);
    }

    /**
     * Reject writes from origins that are not trusted.
     *
     * @param array<string, mixed> $config
     */
    public static function requireTrustedOrigin(array $config): void
    {
        if (!self::isOriginAllowed($config)) {
            throw new ApiError(403, 'Origin is not allowed to write');
        }
    }

    /**
     * Compare host and port with the API's own Host header, ignoring the
     * scheme so a reverse proxy can terminate TLS.
     */
    private static function isSameServerOrigin(string $origin): bool
    {
        $requestHost = (string) ($_SERVER['HTTP_HOST'] ?? '');

        if ($requestHost === '') {
            return false;
        }

        $requestPort = null;

        if (preg_match('/^(.+):(\d+)$/', $requestHost, $matches)) {
            $requestHost = $matches[1];
            $requestPort = (int) $matches[2];
        }

        $originHost = parse_url($origin, PHP_URL_HOST);
        $originPort = parse_url($origin, PHP_URL_PORT);

        if (!is_string($originHost) || $originHost === '' || strcasecmp($originHost, $requestHost) !== 0) {
            return false;
        }

        // Treat an omitted port as the scheme default.
        if ($originPort === null) {
            $scheme = parse_url($origin, PHP_URL_SCHEME);
            $originPort = $scheme === 'https' ? 443 : 80;
        }

        if ($requestPort === null) {
            $forwarded = $_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '';
            $scheme = $forwarded === 'https' || (isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
                ? 'https'
                : 'http';
            $requestPort = $scheme === 'https' ? 443 : 80;
        }

        return $originPort === $requestPort;
    }

    /**
     * Validate the shared ingest key when one is configured.
     *
     * @param array<string, mixed> $config
     */
    public static function requireIngestKey(array $config): void
    {
        $expected = trim((string) $config['ingest_key']);

        if ($expected === '') {
            return;
        }

        $provided = $_SERVER['HTTP_X_API_KEY'] ?? $_GET['key'] ?? '';

        if (!is_string($provided) || $provided === '' || !hash_equals($expected, $provided)) {
            throw new ApiError(401, 'Invalid or missing API key');
        }
    }

    /**
     * Read an integer query parameter with bounds.
     */
    public static function intParam(string $name, ?int $default = null, ?int $min = null, ?int $max = null): ?int
    {
        $value = $_GET[$name] ?? null;

        if ($value === null || $value === '') {
            return $default;
        }

        if (!is_string($value) || !preg_match('/^-?\d{1,12}$/', $value)) {
            throw new ApiError(422, sprintf('Query parameter "%s" must be an integer', $name));
        }

        $int = (int) $value;

        if (($min !== null && $int < $min) || ($max !== null && $int > $max)) {
            throw new ApiError(422, sprintf('Query parameter "%s" is out of range', $name), [
                'min' => $min,
                'max' => $max,
            ]);
        }

        return $int;
    }

    /**
     * Trim, normalise and validate an ingest payload.
     *
     * @return list<array{u: int, t: array{0: ?float, 1: ?float, 2: ?float, 3: ?float}, f: array{0: int, 1: int, 2: int, 3: int}}>
     */
    public static function validateSamples(mixed $raw, int $maxSamples): array
    {
        if (!is_array($raw) || $raw === [] || !array_is_list($raw)) {
            throw new ApiError(422, 'Field "samples" must be a non-empty list');
        }

        if (count($raw) > $maxSamples) {
            throw new ApiError(422, 'Too many samples in one request', ['max_samples' => $maxSamples]);
        }

        $samples = [];

        foreach ($raw as $index => $sample) {
            if (!is_array($sample)) {
                throw new ApiError(422, sprintf('samples[%d] must be an object', $index));
            }

            $uptime = $sample['u'] ?? null;

            if (!is_int($uptime) && !(is_float($uptime) && floor($uptime) === $uptime)) {
                throw new ApiError(422, sprintf('samples[%d].u must be an integer uptime in milliseconds', $index));
            }

            $temps = $sample['t'] ?? null;

            if (!is_array($temps) || count($temps) !== 4) {
                throw new ApiError(422, sprintf('samples[%d].t must contain exactly 4 values', $index));
            }

            $channels = [];

            foreach (array_values($temps) as $channel => $value) {
                if ($value === null) {
                    $channels[$channel] = null;
                    continue;
                }

                if (!is_int($value) && !is_float($value)) {
                    throw new ApiError(422, sprintf('samples[%d].t[%d] must be a number or null', $index, $channel));
                }

                $value = (float) $value;

                if (!is_finite($value) || $value < -270.0 || $value > 1800.0) {
                    throw new ApiError(422, sprintf('samples[%d].t[%d] is outside -270..1800 °C', $index, $channel));
                }

                $channels[$channel] = round($value, 2);
            }

            $faults = $sample['f'] ?? [0, 0, 0, 0];

            if (!is_array($faults) || count($faults) !== 4) {
                throw new ApiError(422, sprintf('samples[%d].f must contain exactly 4 fault flags', $index));
            }

            $flags = [];

            foreach (array_values($faults) as $channel => $flag) {
                if (!is_int($flag) || $flag < 0 || $flag > 15) {
                    throw new ApiError(422, sprintf('samples[%d].f[%d] must be an integer between 0 and 15', $index, $channel));
                }

                $flags[$channel] = $flag;
            }

            $samples[] = [
                'u' => (int) $uptime,
                't' => $channels,
                'f' => $flags,
            ];
        }

        return $samples;
    }
}
