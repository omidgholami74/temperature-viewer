<?php

declare(strict_types=1);

/**
 * Create or migrate the database. Safe to run repeatedly.
 *
 *   php backend/bin/install.php
 */

require __DIR__ . '/../src/autoload.php';

/** @var array<string, mixed> $config */
$config = require __DIR__ . '/../config.php';

try {
    $pdo = App\Database::connect($config);
} catch (Throwable $exception) {
    fwrite(STDERR, 'Database setup failed: ' . $exception->getMessage() . PHP_EOL);
    exit(1);
}

$tables = $pdo
    ->query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    ->fetchAll(PDO::FETCH_COLUMN);

printf("Database ready: %s\n", $config['db_path']);
printf("Schema version: %s\n", App\Database::meta($pdo, 'schema_version') ?? 'unknown');
printf("Tables: %s\n", implode(', ', $tables));

$free = (int) $pdo->query('PRAGMA freelist_count')->fetchColumn();
if ($free > 0) {
    printf("Free pages: %d (run bin/maintenance.php --vacuum to reclaim disk space)\n", $free);
}
