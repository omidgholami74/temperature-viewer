<?php

declare(strict_types=1);

namespace App;

use RuntimeException;

/**
 * An error that is safe to report to API clients together with an HTTP status.
 */
final class ApiError extends RuntimeException
{
    /**
     * @param array<string, mixed>|null $details
     */
    public function __construct(
        public readonly int $status,
        string $message,
        public readonly ?array $details = null,
    ) {
        parent::__construct($message, $status);
    }
}
