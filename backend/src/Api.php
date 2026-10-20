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
     * Reject cross-site writes. Browsers send Origin, so a mismatched host
     * blocks CSRF while command line tools and the Pico keep working.
     */
    public static function requireSameOrigin(): void
    {
        $origin = $_SERVER['HTTP_ORIGIN'] ?? null;

        if ($origin === null || $origin === '') {
            return;
        }

        $requestHost = (string) ($_SERVER['HTTP_HOST'] ?? '');

        if ($requestHost === '') {
            throw new ApiError(400, 'Missing Host header');
        }

        // Compare hosts only: the dashboard may be served over http on a local
        // network and https behind a reverse proxy at the same address.
        $requestHost = preg_replace('/:\d+$/', '', $requestHost);
        $originHost = parse_url($origin, PHP_URL_HOST);

        if (!is_string($originHost) || $originHost === '' || strcasecmp($originHost, (string) $requestHost) !== 0) {
            throw new ApiError(403, 'Cross-origin writes are not allowed');
        }
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
