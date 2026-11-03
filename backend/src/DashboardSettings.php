<?php

declare(strict_types=1);

namespace App;

use PDO;

/**
 * Settings that shape the public dashboard.
 *
 * These are deliberately server-side: the threshold used to live in each
 * visitor's localStorage, which meant two phones could show different values
 * and the operator could not change it without opening the public page. The
 * admin panel now owns them, the public page only reads them.
 */
final class DashboardSettings
{
    private const KEY = 'dashboard_settings';

    public const DEFAULTS = [
        'noise_enabled' => true,
        'noise_threshold' => 60,
        'default_window_s' => 600,
        'notice' => '',
    ];

    private const WINDOWS = [60, 600, 3600, 21600, 86400, 0];

    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * @return array{noise_enabled: bool, noise_threshold: int, default_window_s: int, notice: string}
     */
    public function get(): array
    {
        $raw = Database::meta($this->pdo, self::KEY);

        if ($raw === null || $raw === '') {
            return self::DEFAULTS;
        }

        $stored = json_decode($raw, true);

        if (!is_array($stored)) {
            return self::DEFAULTS;
        }

        try {
            return $this->validate($stored, self::DEFAULTS);
        } catch (\Throwable) {
            return self::DEFAULTS;
        }
    }

    /**
     * Save a partial update and return the complete settings object.
     *
     * @param array<string, mixed> $input
     * @return array{noise_enabled: bool, noise_threshold: int, default_window_s: int, notice: string}
     */
    public function save(array $input): array
    {
        $values = $this->validate($input, $this->get());
        Database::setMeta($this->pdo, self::KEY, json_encode($values, JSON_THROW_ON_ERROR));

        return $values;
    }

    /**
     * @param array<string, mixed> $input
     * @param array<string, mixed> $current
     * @return array{noise_enabled: bool, noise_threshold: int, default_window_s: int, notice: string}
     */
    private function validate(array $input, array $current): array
    {
        $values = [
            'noise_enabled' => (bool) ($current['noise_enabled'] ?? true),
            'noise_threshold' => (int) ($current['noise_threshold'] ?? 60),
            'default_window_s' => (int) ($current['default_window_s'] ?? 600),
            'notice' => (string) ($current['notice'] ?? ''),
        ];

        if (array_key_exists('noise_enabled', $input)) {
            if (!is_bool($input['noise_enabled'])) {
                throw new ApiError(422, 'noise_enabled must be true or false');
            }

            $values['noise_enabled'] = $input['noise_enabled'];
        }

        if (array_key_exists('noise_threshold', $input)) {
            if (!is_int($input['noise_threshold'])) {
                throw new ApiError(422, 'noise_threshold must be a whole number');
            }

            if ($input['noise_threshold'] < 5 || $input['noise_threshold'] > 95) {
                throw new ApiError(422, 'noise_threshold must be between 5 and 95 percent');
            }

            $values['noise_threshold'] = $input['noise_threshold'];
        }

        if (array_key_exists('default_window_s', $input)) {
            if (!is_int($input['default_window_s']) || !in_array($input['default_window_s'], self::WINDOWS, true)) {
                throw new ApiError(422, 'default_window_s must be one of 60, 600, 3600, 21600, 86400 or 0');
            }

            $values['default_window_s'] = $input['default_window_s'];
        }

        if (array_key_exists('notice', $input)) {
            if (!is_string($input['notice'])) {
                throw new ApiError(422, 'notice must be text');
            }

            $values['notice'] = cutString(trim($input['notice']), 180);
        }

        return $values;
    }
}
