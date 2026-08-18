<?php
/** PDO connection helper, shared by sync.php and index.php. */
function get_db(array $env): PDO
{
    $host = $env['DB_HOST'] ?? 'localhost';
    $name = $env['DB_NAME'] ?? '';
    $user = $env['DB_USER'] ?? '';
    $pass = $env['DB_PASS'] ?? '';

    // utf8, not utf8mb4 -- matches schema.sql's table charset (see the
    // comment there for why).
    $dsn = "mysql:host={$host};dbname={$name};charset=utf8";
    return new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
}
