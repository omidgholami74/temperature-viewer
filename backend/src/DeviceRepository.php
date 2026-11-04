<?php

declare(strict_types=1);

namespace App;

use PDO;

/**
 * Boards, their channel labels and the commands waiting for them.
 *
 * The device talks to this repository through the heartbeat: it reports where
 * it is and what it has sent, and gets back the commands queued by the admin
 * panel. Commands stay queued until acknowledged, so a board that is offline
 * for a day still receives a Wi-Fi change when it comes back.
 */
final class DeviceRepository
{
    /** A heartbeat gap longer than this is logged as a reconnect. */
    private const RECONNECT_GAP_MS = 90000;

    /** Must match Settings.DEFAULTS in esp32-s3/common/settings.py. */
    private const DEFAULT_SETTINGS = [
        'sample_ms' => 100,
        'noise_samples' => 5,
        'noise_gap_us' => 6000,
        'ema_alpha' => 0.35,
        'heartbeat_s' => 15,
    ];

    public function __construct(private readonly PDO $pdo)
    {
    }

    /**
     * Store a heartbeat and return the work for that device.
     *
     * @param array{id: string, name: string, source: string, firmware: string, ip: string, rssi: ?int, uptime_ms: int, sent: int, pending: int, dropped: int, channels: list<int>} $device
     * @return array{commands: list<array<string, mixed>>, channels: list<array<string, mixed>>, settings: ?array<string, mixed>}
     */
    public function heartbeat(array $device, int $now): array
    {
        $existing = $this->find($device['id']);

        if ($existing === null) {
            $statement = $this->pdo->prepare(
                'INSERT INTO devices
                    (id, name, source, firmware, ip, rssi, uptime_ms, sent, pending, dropped, created_at_ms, last_seen_ms)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            );
            $statement->execute([
                $device['id'],
                $device['name'],
                $device['source'],
                $device['firmware'],
                $device['ip'],
                $device['rssi'],
                $device['uptime_ms'],
                $device['sent'],
                $device['pending'],
                $device['dropped'],
                $now,
                $now,
            ]);
            $this->event($device['id'], 'registered', 'first heartbeat', $now);
        } else {
            $gap = $now - (int) $existing['last_seen_ms'];

            if ($gap > self::RECONNECT_GAP_MS) {
                $this->event(
                    $device['id'],
                    'reconnect',
                    sprintf('back after %d s', intdiv($gap, 1000)),
                    $now,
                );
            }

            $statement = $this->pdo->prepare(
                'UPDATE devices SET
                    name = ?, source = ?, firmware = ?, ip = ?, rssi = ?, uptime_ms = ?,
                    sent = ?, pending = ?, dropped = ?, last_seen_ms = ?
                 WHERE id = ?',
            );
            $statement->execute([
                $device['name'],
                $device['source'],
                $device['firmware'],
                $device['ip'],
                $device['rssi'],
                $device['uptime_ms'],
                $device['sent'],
                $device['pending'],
                $device['dropped'],
                $now,
                $device['id'],
            ]);
        }

        $this->syncChannels($device['id'], $device['channels']);
        $commands = $this->pendingCommands($device['id']);

        return [
            'commands' => $commands,
            'channels' => $this->channels($device['id']),
            'settings' => $this->settings($device['id']),
        ];
    }

    /**
     * @return array<string, mixed>|null
     */
    public function find(string $deviceId): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM devices WHERE id = ?');
        $statement->execute([$deviceId]);
        $row = $statement->fetch();

        return $row === false ? null : $row;
    }

    /**
     * Every device with its channels and latest command, newest first.
     *
     * @return list<array<string, mixed>>
     */
    public function all(int $now): array
    {
        $rows = $this->pdo->query('SELECT * FROM devices ORDER BY last_seen_ms DESC')->fetchAll();
        $devices = [];

        foreach ($rows as $row) {
            $id = (string) $row['id'];
            $lastCommand = $this->pdo->prepare(
                'SELECT kind, status, result, created_at_ms, ack_at_ms
                 FROM device_commands WHERE device_id = ? ORDER BY id DESC LIMIT 1',
            );
            $lastCommand->execute([$id]);
            $command = $lastCommand->fetch();
            $pending = $this->pdo->prepare(
                "SELECT COUNT(*) FROM device_commands WHERE device_id = ? AND status IN ('queued', 'delivered')",
            );
            $pending->execute([$id]);

            $profile = $this->profile($id);

            $devices[] = [
                'id' => $id,
                'name' => (string) $row['name'],
                'display_name' => $profile['display_name'],
                'location' => $profile['location'],
                'notes' => $profile['notes'],
                'source' => (string) ($row['source'] ?? ''),
                'firmware' => (string) $row['firmware'],
                'ip' => (string) $row['ip'],
                'rssi' => $row['rssi'] === null ? null : (int) $row['rssi'],
                'uptime_ms' => (int) $row['uptime_ms'],
                'sent' => (int) $row['sent'],
                'pending_samples' => (int) $row['pending'],
                'dropped' => (int) $row['dropped'],
                'last_seen_ms' => (int) $row['last_seen_ms'],
                'age_ms' => max(0, $now - (int) $row['last_seen_ms']),
                'channels' => $this->channels($id),
                'settings' => $this->settings($id),
                'settings_at_ms' => $row['settings_at_ms'] === null ? null : (int) $row['settings_at_ms'],
                'pending_commands' => (int) $pending->fetchColumn(),
                'last_command' => $command === false ? null : [
                    'kind' => (string) $command['kind'],
                    'status' => (string) $command['status'],
                    'result' => $command['result'] === null ? null : (string) $command['result'],
                    'created_at_ms' => (int) $command['created_at_ms'],
                    'ack_at_ms' => $command['ack_at_ms'] === null ? null : (int) $command['ack_at_ms'],
                ],
            ];
        }

        return $devices;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function channels(string $deviceId): array
    {
        $statement = $this->pdo->prepare(
            'SELECT channel, label, present, enabled, offset, offset_ranges FROM device_channels
             WHERE device_id = ? ORDER BY channel',
        );
        $statement->execute([$deviceId]);
        $channels = [];

        foreach ($statement->fetchAll() as $row) {
            $channels[] = [
                'channel' => (int) $row['channel'],
                'label' => (string) $row['label'],
                'present' => (bool) $row['present'],
                'enabled' => (bool) $row['enabled'],
                'offset' => (float) $row['offset'],
                'offset_ranges' => self::ranges($row['offset_ranges'] ?? null),
            ];
        }

        return $channels;
    }

    /**
     * Decode the band-calibration JSON, tolerating a row written before the
     * column existed or a hand-edited database.
     *
     * @return list<array{min: float, max: float, offset: float}>
     */
    private static function ranges(?string $raw): array
    {
        if ($raw === null || $raw === '') {
            return [];
        }

        $decoded = json_decode($raw, true);

        if (!is_array($decoded)) {
            return [];
        }

        $ranges = [];

        foreach ($decoded as $entry) {
            if (!is_array($entry)) {
                continue;
            }

            $min = isset($entry['min']) ? (float) $entry['min'] : null;
            $max = isset($entry['max']) ? (float) $entry['max'] : null;
            $offset = isset($entry['offset']) ? (float) $entry['offset'] : 0.0;

            if ($min === null || $max === null || !is_finite($min) || !is_finite($max) || $min >= $max) {
                continue;
            }

            $ranges[] = [
                'min' => round($min, 2),
                'max' => round($max, 2),
                'offset' => round($offset, 2),
            ];
        }

        return $ranges;
    }

    /**
     * Operator-owned board profile. Empty values mean "use the reported name
     * and show no location/notes".
     *
     * @return array{display_name: string, location: string, notes: string}
     */
    public function profile(string $deviceId): array
    {
        $statement = $this->pdo->prepare(
            'SELECT display_name, location, notes FROM device_profiles WHERE device_id = ?',
        );
        $statement->execute([$deviceId]);
        $row = $statement->fetch();

        return [
            'display_name' => $row === false ? '' : (string) $row['display_name'],
            'location' => $row === false ? '' : (string) $row['location'],
            'notes' => $row === false ? '' : (string) $row['notes'],
        ];
    }

    /**
     * Save the operator profile without touching the name reported by firmware.
     *
     * @return array{display_name: string, location: string, notes: string}
     */
    public function saveProfile(string $deviceId, string $name, string $location, string $notes, int $now): array
    {
        $statement = $this->pdo->prepare(
            'INSERT INTO device_profiles (device_id, display_name, location, notes, updated_at_ms)
             VALUES (?, ?, ?, ?, ?)
             ON CONFLICT (device_id) DO UPDATE SET
                display_name = excluded.display_name,
                location = excluded.location,
                notes = excluded.notes,
                updated_at_ms = excluded.updated_at_ms',
        );
        $statement->execute([$deviceId, $name, $location, $notes, $now]);
        $this->event($deviceId, 'profile', 'operator profile updated', $now);

        return $this->profile($deviceId);
    }

    /**
     * Labels survive a heartbeat; only presence comes from the board.
     *
     * @param list<int> $present
     */
    private function syncChannels(string $deviceId, array $present): void
    {
        $insert = $this->pdo->prepare(
            'INSERT OR IGNORE INTO device_channels (device_id, channel, label, present, enabled)
             VALUES (?, ?, ?, 0, 1)',
        );

        foreach ([1, 2, 3, 4] as $channel) {
            $insert->execute([$deviceId, $channel, sprintf('Furnace %d', $channel)]);
        }

        $update = $this->pdo->prepare(
            'UPDATE device_channels SET present = ? WHERE device_id = ? AND channel = ?',
        );

        foreach ([1, 2, 3, 4] as $channel) {
            $update->execute([in_array($channel, $present, true) ? 1 : 0, $deviceId, $channel]);
        }
    }

    /**
     * @param list<array{channel: int, label: string, enabled: bool, offset: float}> $channels
     */
    public function setChannels(string $deviceId, array $channels): void
    {
        $statement = $this->pdo->prepare(
            'UPDATE device_channels SET label = ?, enabled = ?, offset = ?, offset_ranges = ?
             WHERE device_id = ? AND channel = ?',
        );

        foreach ($channels as $channel) {
            $statement->execute([
                $channel['label'],
                $channel['enabled'] ? 1 : 0,
                $channel['offset'],
                json_encode($channel['offset_ranges'] ?? []),
                $deviceId,
                $channel['channel'],
            ]);
        }
    }

    /**
     * The snapshot last saved from the panel, with channels refreshed from the
     * registry. Null means the operator has never saved settings, which lets a
     * fresh board keep its config.py defaults instead of being overwritten.
     *
     * @return array<string, mixed>|null
     */
    public function settings(string $deviceId): ?array
    {
        $statement = $this->pdo->prepare('SELECT settings FROM devices WHERE id = ?');
        $statement->execute([$deviceId]);
        $row = $statement->fetch();

        if ($row === false || $row['settings'] === null || $row['settings'] === '') {
            return null;
        }

        $stored = json_decode((string) $row['settings'], true);

        if (!is_array($stored)) {
            return null;
        }

        $values = self::DEFAULT_SETTINGS;

        foreach (array_keys(self::DEFAULT_SETTINGS) as $key) {
            if (array_key_exists($key, $stored)) {
                $values[$key] = $stored[$key];
            }
        }

        $values['channels'] = array_map(
            static fn (array $channel): array => [
                'channel' => (int) $channel['channel'],
                'enabled' => (bool) $channel['enabled'],
                'offset' => (float) $channel['offset'],
                'offset_ranges' => $channel['offset_ranges'] ?? [],
            ],
            $this->channels($deviceId),
        );

        return $values;
    }

    /**
     * Store the desired snapshot and queue it for the board. The heartbeat also
     * returns it, so a board that lost /settings.json recovers on its own.
     *
     * @param array<string, mixed> $globals
     * @return array<string, mixed>
     */
    public function saveSettings(string $deviceId, array $globals, int $now): array
    {
        $current = $this->settings($deviceId) ?? self::DEFAULT_SETTINGS;
        $values = [];

        foreach (array_keys(self::DEFAULT_SETTINGS) as $key) {
            $values[$key] = array_key_exists($key, $globals) ? $globals[$key] : $current[$key];
        }

        $values['channels'] = array_map(
            static fn (array $channel): array => [
                'channel' => (int) $channel['channel'],
                'enabled' => (bool) $channel['enabled'],
                'offset' => (float) $channel['offset'],
                'offset_ranges' => $channel['offset_ranges'] ?? [],
            ],
            $this->channels($deviceId),
        );

        $statement = $this->pdo->prepare(
            'UPDATE devices SET settings = ?, settings_at_ms = ? WHERE id = ?',
        );
        $statement->execute([json_encode($values), $now, $deviceId]);

        return $this->queue($deviceId, 'settings', ['settings' => $values], $now);
    }

    /**
     * Queue a command for a device. Payload is JSON, never returned to the UI.
     *
     * @param array<string, mixed> $payload
     * @return array<string, mixed>
     */
    public function queue(string $deviceId, string $kind, array $payload, int $now): array
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO device_commands (device_id, kind, payload, created_at_ms, status)
             VALUES (?, ?, ?, ?, 'queued')",
        );
        $statement->execute([$deviceId, $kind, json_encode($payload), $now]);
        $this->event($deviceId, 'command', $kind . ' queued', $now);

        return [
            'id' => (int) $this->pdo->lastInsertId(),
            'kind' => $kind,
            'status' => 'queued',
            'created_at_ms' => $now,
        ];
    }

    /**
     * Commands the board has not acknowledged yet, oldest first.
     *
     * @return list<array<string, mixed>>
     */
    private function pendingCommands(string $deviceId): array
    {
        $statement = $this->pdo->prepare(
            "SELECT * FROM device_commands
             WHERE device_id = ? AND status IN ('queued', 'delivered')
             ORDER BY id LIMIT 10",
        );
        $statement->execute([$deviceId]);
        $rows = $statement->fetchAll();

        if ($rows === []) {
            return [];
        }

        $mark = $this->pdo->prepare(
            "UPDATE device_commands SET status = 'delivered', delivered_at_ms = ?
             WHERE id = ? AND status = 'queued'",
        );
        $commands = [];

        foreach ($rows as $row) {
            if ($row['status'] === 'queued') {
                $mark->execute([time() * 1000, (int) $row['id']]);
            }

            $payload = json_decode((string) $row['payload'], true);
            $commands[] = [
                'id' => (int) $row['id'],
                'kind' => (string) $row['kind'],
                'payload' => is_array($payload) ? $payload : [],
                'created_at_ms' => (int) $row['created_at_ms'],
            ];
        }

        return $commands;
    }

    public function ack(string $deviceId, int $commandId, string $status, string $result, int $now): bool
    {
        $statement = $this->pdo->prepare(
            'SELECT kind FROM device_commands WHERE id = ? AND device_id = ?',
        );
        $statement->execute([$commandId, $deviceId]);
        $kind = $statement->fetchColumn();

        if ($kind === false) {
            return false;
        }

        $update = $this->pdo->prepare(
            'UPDATE device_commands SET status = ?, ack_at_ms = ?, result = ?
             WHERE id = ? AND device_id = ?',
        );
        $update->execute([$status, $now, $result, $commandId, $deviceId]);
        $this->event($deviceId, 'command', sprintf('%s %s: %s', $kind, $status, $result), $now);

        return true;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function events(int $limit): array
    {
        $statement = $this->pdo->prepare(
            'SELECT id, device_id, kind, detail, at_ms FROM device_events
             ORDER BY id DESC LIMIT ?',
        );
        $statement->bindValue(1, $limit, PDO::PARAM_INT);
        $statement->execute();
        $events = [];

        foreach ($statement->fetchAll() as $row) {
            $events[] = [
                'id' => (int) $row['id'],
                'device_id' => (string) $row['device_id'],
                'kind' => (string) $row['kind'],
                'detail' => $row['detail'] === null ? null : (string) $row['detail'],
                'at_ms' => (int) $row['at_ms'],
            ];
        }

        return $events;
    }

    private function event(string $deviceId, string $kind, string $detail, int $now): void
    {
        $statement = $this->pdo->prepare(
            'INSERT INTO device_events (device_id, kind, detail, at_ms) VALUES (?, ?, ?, ?)',
        );
        $statement->execute([$deviceId, $kind, $detail, $now]);
    }
}
