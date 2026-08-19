<?php
/**
 * Cron entrypoint -- run every 10 minutes (see README.md), fetches
 * current readings + a 24h history window from the VM's
 * /api/v1/public/summary and /api/v1/public/history endpoints and
 * upserts them into MySQL's probe_cache table (schema.sql).
 *
 * index.php never talks to the VM directly -- it only reads this
 * table. That split means a page visitor's load time and the VM's
 * mTLS-adjacent nginx rate limit (server/nginx/nginx.conf.template)
 * are now completely decoupled: however many visitors hit the page in
 * a burst, the VM only ever sees one request per cron interval.
 *
 * Only overwrites what it actually fetched this run: if the history
 * call fails but summary succeeds (or vice versa isn't possible here
 * since both come from the same run, but the principle holds for
 * partial failures), the previous good history_json is left in place
 * rather than being wiped with a null. See the ON DUPLICATE KEY UPDATE
 * below.
 *
 * history_json is a snapshot of the VM's own history window, not an
 * accumulating archive -- every run replaces it wholesale. Building a
 * real local history (INSERT instead of UPDATE, keeping every row) is
 * future work once that's actually wanted; not done now on purpose.
 */

date_default_timezone_set('UTC');

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit("sync.php is meant to run from cron, not a browser.\n");
}

require __DIR__ . '/db.php';

const CA_CERT_FILE = __DIR__ . '/weathernet-ca.pem';
const HISTORY_WINDOW_HOURS = 24;

function fetch_public_api(string $path, string $vmHost, string $apiKey): ?array
{
    $ch = curl_init('https://' . $vmHost . $path);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 15,
        CURLOPT_HTTPHEADER => ['X-Api-Key: ' . $apiKey],
        // Verify against the WeatherNet CA specifically -- see
        // README.md for why (the VM's cert is signed by a private CA,
        // not a publicly trusted one).
        CURLOPT_CAINFO => CA_CERT_FILE,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $body = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($body === false || $status !== 200) {
        fwrite(STDERR, "sync.php: fetch failed for {$path} (status={$status}): {$error}\n");
        return null;
    }
    $data = json_decode($body, true);
    return is_array($data) ? $data : null;
}

function to_mysql_datetime(?string $iso8601): ?string
{
    if ($iso8601 === null) {
        return null;
    }
    return date('Y-m-d H:i:s', strtotime($iso8601));
}

$configFile = __DIR__ . '/config.php';
$env = is_file($configFile) ? require $configFile : [];
$vmHost = $env['VM_HOST'] ?? '';
$apiKey = $env['API_KEY'] ?? '';

if ($vmHost === '' || $apiKey === '') {
    fwrite(STDERR, "sync.php: VM_HOST/API_KEY not configured in config.php -- see README.md\n");
    exit(1);
}

$summary = fetch_public_api('/api/v1/public/summary', $vmHost, $apiKey);
if ($summary === null) {
    fwrite(STDERR, "sync.php: no summary data fetched, leaving MySQL cache untouched\n");
    exit(1);
}

$history = fetch_public_api('/api/v1/public/history?hours=' . HISTORY_WINDOW_HOURS, $vmHost, $apiKey);
$historyByProbe = [];
foreach (($history['probes'] ?? []) as $entry) {
    $historyByProbe[$entry['name']] = $entry['series'];
}

try {
    $pdo = get_db($env);
} catch (PDOException $e) {
    fwrite(STDERR, "sync.php: could not connect to MySQL: " . $e->getMessage() . "\n");
    exit(1);
}

$stmt = $pdo->prepare('
    INSERT INTO probe_cache
        (probe_name, hardware_type, latitude, longitude, last_seen_at,
         temperature_c, humidity_pct, pressure_hpa, gas_resistance_ohm,
         air_quality_index, history_json, updated_at)
    VALUES
        (:name, :hardware_type, :lat, :lon, :last_seen_at,
         :temp, :hum, :pres, :gas, :aqi, :history, NOW())
    ON DUPLICATE KEY UPDATE
        hardware_type = VALUES(hardware_type),
        latitude = VALUES(latitude),
        longitude = VALUES(longitude),
        last_seen_at = VALUES(last_seen_at),
        temperature_c = VALUES(temperature_c),
        humidity_pct = VALUES(humidity_pct),
        pressure_hpa = VALUES(pressure_hpa),
        gas_resistance_ohm = VALUES(gas_resistance_ohm),
        air_quality_index = VALUES(air_quality_index),
        history_json = COALESCE(VALUES(history_json), history_json),
        updated_at = NOW()
');

$count = 0;
foreach (($summary['probes'] ?? []) as $probe) {
    $r = $probe['readings'];
    $series = $historyByProbe[$probe['name']] ?? null;
    $stmt->execute([
        ':name' => $probe['name'],
        ':hardware_type' => $probe['hardware_type'],
        ':lat' => $probe['latitude'],
        ':lon' => $probe['longitude'],
        ':last_seen_at' => to_mysql_datetime($probe['last_seen_at']),
        ':temp' => $r['temperature_c'],
        ':hum' => $r['humidity_pct'],
        ':pres' => $r['pressure_hpa'],
        ':gas' => $r['gas_resistance_ohm'],
        ':aqi' => $r['air_quality_index'],
        // Bound as SQL NULL (not the string "null") when this run's
        // history fetch failed, so ON DUPLICATE KEY UPDATE's COALESCE
        // above keeps whatever was there before instead of wiping it.
        ':history' => $series !== null ? json_encode($series) : null,
    ]);
    $count++;
}

echo "sync.php: updated {$count} probe(s) at " . date('c') . "\n";
