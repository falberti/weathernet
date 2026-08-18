<?php
/**
 * WeatherNet -- public read-only dashboard.
 *
 * Meant to be uploaded as-is to PHP-only hosting (e.g. falberti.it).
 * Fetches server-side from the VM's /api/v1/public/summary endpoint
 * (probes/views.py PublicSummaryView) and renders it -- the endpoint
 * itself is gated by an API key and rate-limited, but that call never
 * touches the visitor's browser, so the VM's self-signed certificate
 * never shows a warning to anyone: visitors only ever see this page's
 * own (regularly issued) TLS certificate.
 *
 * See README.md in this directory for deployment steps.
 */

// --- Configuration -- fill these in before uploading ---
const VM_HOST = 'CHANGE-ME.example.com'; // the WeatherNet server's public IP or hostname
const API_KEY = 'CHANGE-ME';             // must match PUBLIC_SUMMARY_API_KEY in server/.env
const CA_CERT_FILE = __DIR__ . '/weathernet-ca.pem'; // copied from server/pki/ca/ca.cert.pem
const CACHE_FILE = __DIR__ . '/summary-cache.json';
const CACHE_TTL_SECONDS = 60; // readings only change every few minutes server-side anyway

/**
 * Fetches the public summary, using a short-lived local cache so a
 * burst of visitors doesn't hammer the VM (which also rate-limits this
 * endpoint at the nginx level -- see server/nginx/nginx.conf.template).
 * Falls back to a stale cache on a transient failure rather than
 * showing nothing, since this is a public page and the VM being
 * briefly unreachable shouldn't blank it out.
 */
function fetch_summary(): ?array
{
    if (is_file(CACHE_FILE) && (time() - filemtime(CACHE_FILE)) < CACHE_TTL_SECONDS) {
        $cached = json_decode((string) file_get_contents(CACHE_FILE), true);
        if (is_array($cached)) {
            return $cached;
        }
    }

    $ch = curl_init('https://' . VM_HOST . '/api/v1/public/summary');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_HTTPHEADER => ['X-Api-Key: ' . API_KEY],
        // Verify against the WeatherNet CA specifically, rather than
        // disabling verification: the VM's certificate is signed by a
        // private CA (not a public one), so the system trust store
        // won't recognize it, but that doesn't mean skipping
        // verification entirely -- pinning to the one CA that's
        // actually meant to be trusted here is the correct middle
        // ground.
        CURLOPT_CAINFO => CA_CERT_FILE,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $body = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($body === false || $status !== 200) {
        error_log("WeatherNet public summary fetch failed (status={$status}): {$error}");
        if (is_file(CACHE_FILE)) {
            $cached = json_decode((string) file_get_contents(CACHE_FILE), true);
            return is_array($cached) ? $cached : null;
        }
        return null;
    }

    $data = json_decode($body, true);
    if (!is_array($data)) {
        return null;
    }

    file_put_contents(CACHE_FILE, $body);
    return $data;
}

function fmt(?float $value, string $suffix, int $decimals = 1): string
{
    return $value === null ? '--' : number_format($value, $decimals) . $suffix;
}

function aqi_label(?int $score): array
{
    if ($score === null) {
        return ['--', '#999'];
    }
    if ($score >= 70) {
        return ['Buona', '#2e7d32'];
    }
    if ($score >= 40) {
        return ['Moderata', '#f9a825'];
    }
    return ['Scarsa', '#c62828'];
}

function time_ago(?string $iso8601): string
{
    if ($iso8601 === null) {
        return 'mai';
    }
    $diff = time() - strtotime($iso8601);
    if ($diff < 90) {
        return 'poco fa';
    }
    if ($diff < 3600) {
        return floor($diff / 60) . ' min fa';
    }
    return floor($diff / 3600) . ' h fa';
}

$summary = fetch_summary();
$probes = $summary['probes'] ?? [];
?>
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeatherNet -- Dati in tempo reale</title>
<style>
  :root { color-scheme: light; }
  body {
    margin: 0; padding: 2rem 1rem 4rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f4f6f8; color: #1a1a1a;
  }
  h1 { text-align: center; font-size: 1.6rem; margin-bottom: 0.25rem; }
  p.subtitle { text-align: center; color: #666; margin-top: 0; margin-bottom: 2rem; }
  .grid {
    display: grid; gap: 1.25rem; max-width: 1000px; margin: 0 auto;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  }
  .card {
    background: #fff; border-radius: 12px; padding: 1.25rem 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .card h2 { margin: 0 0 0.25rem; font-size: 1.2rem; }
  .card .meta { color: #888; font-size: 0.85rem; margin-bottom: 1rem; }
  .readings { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem 1rem; }
  .reading .label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.03em; }
  .reading .value { font-size: 1.3rem; font-weight: 600; }
  .aqi-badge {
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
    color: #fff; font-size: 0.85rem; font-weight: 600;
  }
  .empty { text-align: center; color: #888; margin-top: 3rem; }
  footer { text-align: center; color: #aaa; font-size: 0.8rem; margin-top: 3rem; }
</style>
</head>
<body>
  <h1>WeatherNet</h1>
  <p class="subtitle">Rilevazioni meteo in tempo reale dalle sonde della rete</p>

  <?php if (empty($probes)): ?>
    <p class="empty">Dati momentaneamente non disponibili. Riprova tra qualche minuto.</p>
  <?php else: ?>
    <div class="grid">
      <?php foreach ($probes as $probe): ?>
        <?php
          $r = $probe['readings'];
          [$aqiLabel, $aqiColor] = aqi_label($r['air_quality_index']);
        ?>
        <div class="card">
          <h2><?= htmlspecialchars($probe['name']) ?></h2>
          <div class="meta">
            Zona: <?= number_format($probe['latitude'], 2) ?>, <?= number_format($probe['longitude'], 2) ?>
            &middot; aggiornato <?= time_ago($probe['last_seen_at']) ?>
          </div>
          <div class="readings">
            <div class="reading">
              <div class="label">Temperatura</div>
              <div class="value"><?= fmt($r['temperature_c'], ' &deg;C') ?></div>
            </div>
            <div class="reading">
              <div class="label">Umidit&agrave;</div>
              <div class="value"><?= fmt($r['humidity_pct'], '%', 0) ?></div>
            </div>
            <div class="reading">
              <div class="label">Pressione</div>
              <div class="value"><?= fmt($r['pressure_hpa'], ' hPa', 0) ?></div>
            </div>
            <div class="reading">
              <div class="label">Qualit&agrave; aria</div>
              <div class="value">
                <span class="aqi-badge" style="background:<?= $aqiColor ?>"><?= $aqiLabel ?></span>
              </div>
            </div>
          </div>
        </div>
      <?php endforeach; ?>
    </div>
  <?php endif; ?>

  <footer>
    Coordinate approssimate per tutela della privacy &middot; Indice qualit&agrave;
    aria euristico, non certificato
  </footer>
</body>
</html>
