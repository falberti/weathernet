<?php
/**
 * WeatherNet -- public read-only dashboard.
 *
 * Meant to be uploaded as-is to PHP-only hosting (e.g. falberti.it).
 * Reads from the local MySQL `probe_cache` table (schema.sql) --
 * never talks to the VM directly. sync.php, run every 10 minutes by
 * cron, is what actually fetches from the VM's /api/v1/public/summary
 * and /api/v1/public/history endpoints and keeps that table current.
 * That split means a burst of visitors here never translates into a
 * burst of requests against the VM, and this page never needs the
 * VM's CA certificate or an mTLS-adjacent API key at all -- only
 * sync.php does.
 *
 * See README.md in this directory for deployment steps.
 */

date_default_timezone_set('UTC');

require __DIR__ . '/db.php';
require __DIR__ . '/i18n.php';

$locale = detect_locale();

// A .php config file, not a plain-text .env -- see config.php.example
// for why. Missing file (a fresh deploy before it's been created)
// degrades to an empty config rather than a fatal error, matching the
// "show the graceful empty state" behavior elsewhere on this page.
$configFile = __DIR__ . '/config.php';
$env = is_file($configFile) ? require $configFile : [];
$telegramBotUsername = $env['TELEGRAM_BOT_USERNAME'] ?? '';

/**
 * Reads every probe currently in the MySQL cache, shaped exactly like
 * the VM's /api/v1/public/summary `probes[]` so the rendering code
 * below doesn't need to care that the data came from MySQL instead of
 * a live fetch.
 */
function load_probes_from_cache(array $env): array
{
    try {
        $pdo = get_db($env);
    } catch (PDOException $e) {
        error_log('WeatherNet public page: could not connect to MySQL: ' . $e->getMessage());
        return [];
    }

    $rows = $pdo->query('SELECT * FROM probe_cache ORDER BY probe_name')->fetchAll();
    return array_map(static function (array $row): array {
        return [
            'name' => $row['probe_name'],
            'hardware_type' => $row['hardware_type'],
            'latitude' => (float) $row['latitude'],
            'longitude' => (float) $row['longitude'],
            'last_seen_at' => $row['last_seen_at'],
            'readings' => [
                'temperature_c' => $row['temperature_c'] !== null ? (float) $row['temperature_c'] : null,
                'humidity_pct' => $row['humidity_pct'] !== null ? (float) $row['humidity_pct'] : null,
                'pressure_hpa' => $row['pressure_hpa'] !== null ? (float) $row['pressure_hpa'] : null,
                'gas_resistance_ohm' => $row['gas_resistance_ohm'] !== null ? (float) $row['gas_resistance_ohm'] : null,
                'air_quality_index' => $row['air_quality_index'] !== null ? (int) $row['air_quality_index'] : null,
            ],
        ];
    }, $rows);
}

function fmt(?float $value, string $suffix, int $decimals = 1): string
{
    return $value === null ? '--' : number_format($value, $decimals) . $suffix;
}

function aqi_label(?int $score, string $locale): array
{
    if ($score === null) {
        return ['--', '#999'];
    }
    if ($score >= 70) {
        return [t($locale, 'aqi_good'), '#2e7d32'];
    }
    if ($score >= 40) {
        return [t($locale, 'aqi_moderate'), '#f9a825'];
    }
    return [t($locale, 'aqi_poor'), '#c62828'];
}

function time_ago(?string $iso8601, string $locale): string
{
    if ($iso8601 === null) {
        return t($locale, 'time_never');
    }
    $diff = time() - strtotime($iso8601);
    if ($diff < 90) {
        return t($locale, 'time_just_now');
    }
    if ($diff < 3600) {
        return t($locale, 'time_minutes_ago', (int) floor($diff / 60));
    }
    return t($locale, 'time_hours_ago', (int) floor($diff / 3600));
}

$probes = load_probes_from_cache($env);
?>
<!DOCTYPE html>
<html lang="<?= htmlspecialchars($locale) ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= t($locale, 'page_title') ?></title>
<!-- Emoji as favicon, not an image asset: free by construction (a
     Unicode character, not a copyrighted graphic), rendered by the
     visitor's own OS emoji font, no extra request or license to track. -->
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>%E2%9B%85</text></svg>">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root { color-scheme: light; }
  body {
    margin: 0; padding: 2rem 1rem 4rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f4f6f8; color: #1a1a1a;
  }
  h1 { text-align: center; font-size: 1.6rem; margin-bottom: 0.25rem; }
  h2.section-title { max-width: 1000px; margin: 3rem auto 1rem; font-size: 1.2rem; }
  p.subtitle { text-align: center; color: #666; margin-top: 0; margin-bottom: 2rem; }
  .grid {
    display: grid; gap: 1.25rem; max-width: 1000px; margin: 0 auto;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  }
  .card {
    background: #fff; border-radius: 12px; padding: 1.25rem 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .card h2, .card h3 { margin: 0 0 0.25rem; font-size: 1.2rem; }
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
  .footer-links {
    display: flex; justify-content: center; align-items: center;
    gap: 1.5rem; margin-bottom: 0.75rem;
  }
  .footer-links a {
    display: inline-flex; align-items: center; gap: 0.4rem;
    color: #667; font-weight: 600; text-decoration: none; font-size: 0.85rem;
  }
  .footer-links a:hover { color: #229ED9; }
  .footer-links svg { width: 20px; height: 20px; fill: currentColor; }

  .map-section { max-width: 1000px; margin: 0 auto; }
  #map { height: 360px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .map-controls {
    display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem;
  }
  .map-controls label {
    background: #fff; border-radius: 999px; padding: 0.4rem 0.9rem;
    font-size: 0.85rem; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  }
  .map-controls input { margin-right: 0.35rem; }

  .lang-switcher {
    position: fixed; top: 1rem; right: 1rem; z-index: 1000;
  }
  .lang-switcher select {
    background: #fff; border: 1px solid #ddd; border-radius: 999px;
    padding: 0.35rem 0.75rem; font-size: 0.85rem; cursor: pointer;
    box-shadow: 0 1px 2px rgba(0,0,0,0.08); appearance: auto;
  }
  .lang-switcher noscript button {
    margin-left: 0.35rem; border-radius: 999px; border: 1px solid #ddd;
    background: #fff; padding: 0.35rem 0.6rem; font-size: 0.8rem; cursor: pointer;
  }
</style>
</head>
<body>
  <form class="lang-switcher" method="get">
    <select name="lang" onchange="this.form.submit()" aria-label="Language / Lingua">
      <?php foreach (LOCALE_LABELS as $code => $label): ?>
        <option value="<?= $code ?>"<?= $code === $locale ? ' selected' : '' ?>><?= $label ?></option>
      <?php endforeach; ?>
    </select>
    <noscript><button type="submit">OK</button></noscript>
  </form>

  <h1>&#9925; WeatherNet</h1>
  <p class="subtitle"><?= t($locale, 'subtitle') ?></p>

  <?php if (empty($probes)): ?>
    <p class="empty"><?= t($locale, 'empty_state') ?></p>
  <?php else: ?>
    <div class="grid">
      <?php foreach ($probes as $probe): ?>
        <?php
          $r = $probe['readings'];
          [$aqiLabel, $aqiColor] = aqi_label($r['air_quality_index'], $locale);
        ?>
        <div class="card">
          <h2><?= htmlspecialchars($probe['name']) ?></h2>
          <div class="meta">
            <?= t($locale, 'zone') ?>: <?= number_format($probe['latitude'], 2) ?>, <?= number_format($probe['longitude'], 2) ?>
            &middot; <?= t($locale, 'updated') ?> <?= time_ago($probe['last_seen_at'], $locale) ?>
          </div>
          <div class="readings">
            <div class="reading">
              <div class="label"><?= t($locale, 'reading_temperature') ?></div>
              <div class="value"><?= fmt($r['temperature_c'], ' &deg;C') ?></div>
            </div>
            <div class="reading">
              <div class="label"><?= t($locale, 'reading_humidity') ?></div>
              <div class="value"><?= fmt($r['humidity_pct'], '%', 0) ?></div>
            </div>
            <div class="reading">
              <div class="label"><?= t($locale, 'reading_pressure') ?></div>
              <div class="value"><?= fmt($r['pressure_hpa'], ' hPa', 0) ?></div>
            </div>
            <div class="reading">
              <div class="label"><?= t($locale, 'reading_aqi') ?></div>
              <div class="value">
                <span class="aqi-badge" style="background:<?= $aqiColor ?>"><?= $aqiLabel ?></span>
              </div>
            </div>
          </div>
        </div>
      <?php endforeach; ?>
    </div>

    <h2 class="section-title"><?= t($locale, 'map_title') ?></h2>
    <div class="map-section">
      <div class="map-controls">
        <label><input type="radio" name="map-param" value="air_quality_index" checked> <?= t($locale, 'reading_aqi') ?></label>
        <label><input type="radio" name="map-param" value="temperature_c"> <?= t($locale, 'reading_temperature') ?></label>
        <label><input type="radio" name="map-param" value="humidity_pct"> <?= t($locale, 'reading_humidity') ?></label>
        <label><input type="radio" name="map-param" value="pressure_hpa"> <?= t($locale, 'reading_pressure') ?></label>
      </div>
      <div id="map"></div>
    </div>
  <?php endif; ?>

  <footer>
    <div class="footer-links">
      <?php if ($telegramBotUsername !== ''): ?>
        <a href="https://t.me/<?= htmlspecialchars($telegramBotUsername) ?>" target="_blank" rel="noopener"
           aria-label="<?= t($locale, 'telegram_tooltip') ?>" title="<?= t($locale, 'telegram_tooltip') ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M2 21l21-9L2 3v7l15 2-15 2z"></path>
          </svg>
        </a>
      <?php endif; ?>
      <a href="https://github.com/falberti/weathernet" target="_blank" rel="noopener"
         aria-label="<?= t($locale, 'github_tooltip') ?>" title="<?= t($locale, 'github_tooltip') ?>">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
        </svg>
      </a>
    </div>
    <?= t($locale, 'footer_disclaimer') ?>
  </footer>

<?php if (!empty($probes)): ?>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const probes = <?= json_encode($probes) ?>;
    const LABELS = {
      temperature: <?= json_encode(t($locale, 'reading_temperature')) ?>,
      humidity: <?= json_encode(t($locale, 'reading_humidity')) ?>,
      pressure: <?= json_encode(t($locale, 'reading_pressure')) ?>,
      aqi: <?= json_encode(t($locale, 'reading_aqi')) ?>,
    };

    // --- Map ---
    // One marker per probe today, colored by the selected parameter --
    // with a single probe this is a colored point, not a heatmap; once
    // there's more than one, the same data (and this same control) is
    // what a real interpolated heatmap (e.g. leaflet.heat) would read
    // from too, so nothing here needs to change to add one later.
    const PARAM_RANGES = {
      temperature_c: { min: -5, max: 35 },
      humidity_pct: { min: 0, max: 100 },
      pressure_hpa: { min: 990, max: 1030 },
    };

    // A probe's real position is only known to ~1km (coordinates are
    // rounded server-side, see PUBLIC_LOCATION_PRECISION_DECIMALS) --
    // this is that same uncertainty in meters, roughly matching a
    // 2-decimal rounding at Italian latitudes. Update this if that
    // server-side setting ever changes.
    const PROBE_LOCATION_UNCERTAINTY_METERS = 800;

    function gradientColor(t) {
      t = Math.max(0, Math.min(1, t));
      const r = Math.round(30 + t * (220 - 30));
      const g = Math.round(120 + t * (40 - 120));
      const b = Math.round(220 + t * (40 - 220));
      return `rgb(${r},${g},${b})`;
    }

    function colorFor(param, readings) {
      const value = readings[param];
      if (value === null || value === undefined) {
        return '#999';
      }
      if (param === 'air_quality_index') {
        if (value >= 70) return '#2e7d32';
        if (value >= 40) return '#f9a825';
        return '#c62828';
      }
      const range = PARAM_RANGES[param];
      return gradientColor((value - range.min) / (range.max - range.min));
    }

    const map = L.map('map');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 15,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    const bounds = [];
    let markers = [];

    function renderMarkers(param) {
      markers.forEach((m) => map.removeLayer(m));
      markers = probes.map((probe) => {
        // L.circle (radius in meters), not L.circleMarker (radius in
        // pixels): a pixel-fixed marker's apparent ground footprint
        // shrinks as you zoom in and grows as you zoom out, so it
        // either understates or overstates how precisely the probe is
        // actually located depending on zoom level. A meter-radius
        // circle always covers the same real area -- a soft blob
        // signaling "somewhere in here", not a pin claiming an exact
        // spot the rounded coordinates don't actually have.
        const marker = L.circle([probe.latitude, probe.longitude], {
          radius: PROBE_LOCATION_UNCERTAINTY_METERS,
          color: colorFor(param, probe.readings),
          weight: 1,
          opacity: 0.6,
          fillColor: colorFor(param, probe.readings),
          fillOpacity: 0.35,
        }).addTo(map);
        marker.bindPopup(
          `<strong>${probe.name}</strong><br>` +
          `${LABELS.temperature}: ${probe.readings.temperature_c ?? '--'} &deg;C<br>` +
          `${LABELS.humidity}: ${probe.readings.humidity_pct ?? '--'}%<br>` +
          `${LABELS.pressure}: ${probe.readings.pressure_hpa ?? '--'} hPa<br>` +
          `${LABELS.aqi}: ${probe.readings.air_quality_index ?? '--'}`
        );
        return marker;
      });
    }

    probes.forEach((probe) => bounds.push([probe.latitude, probe.longitude]));
    if (bounds.length === 1) {
      map.setView(bounds[0], 11);
    } else if (bounds.length > 1) {
      map.fitBounds(bounds, { padding: [30, 30] });
    } else {
      map.setView([41.9, 12.5], 5); // fallback: Italy, no probes to center on
    }
    renderMarkers('air_quality_index');

    document.querySelectorAll('input[name="map-param"]').forEach((input) => {
      input.addEventListener('change', (e) => renderMarkers(e.target.value));
    });
  </script>
<?php endif; ?>
</body>
</html>
