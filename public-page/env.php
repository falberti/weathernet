<?php
/**
 * Minimal, dependency-free .env loader -- KEY=VALUE per line, '#' for
 * comments, blank lines ignored. No quoting/escaping support: every
 * value this page needs (a hostname, an opaque API key) is a single
 * plain token, nothing that needs more than that.
 */
function load_env(string $path): array
{
    $values = [];
    if (!is_file($path)) {
        return $values;
    }
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#') {
            continue;
        }
        [$key, $value] = array_pad(explode('=', $line, 2), 2, '');
        $values[trim($key)] = trim($value);
    }
    return $values;
}
