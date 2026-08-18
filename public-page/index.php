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

require __DIR__ . '/env.php';
require __DIR__ . '/db.php';

const HISTORY_WINDOW_HOURS = 24;

$env = load_env(__DIR__ . '/.env');

/**
 * Reads every probe currently in the MySQL cache, shaped exactly like
 * the VM's /api/v1/public/summary `probes[]` (plus a decoded
 * `series` alongside `readings`) so the rendering code below doesn't
 * need to care that the data came from MySQL instead of a live fetch.
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
            'series' => $row['history_json'] !== null ? json_decode($row['history_json'], true) : null,
        ];
    }, $rows);
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

$probes = load_probes_from_cache($env);
$historyByProbe = [];
foreach ($probes as $probe) {
    if ($probe['series'] !== null) {
        $historyByProbe[$probe['name']] = $probe['series'];
    }
}

const SENSOR_CHART_CONFIG = [
    'temperature_c' => ['label' => 'Temperatura (°C)', 'color' => '#e65100'],
    'humidity_pct' => ['label' => 'Umidità (%)', 'color' => '#1565c0'],
    'pressure_hpa' => ['label' => 'Pressione (hPa)', 'color' => '#6a1b9a'],
    'gas_resistance_ohm' => ['label' => 'Resistenza gas (Ω)', 'color' => '#2e7d32'],
];
?>
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeatherNet -- Dati in tempo reale</title>
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

  .charts-grid {
    display: grid; gap: 1.25rem; max-width: 1000px; margin: 0 auto;
    grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  }
  .chart-card canvas { max-height: 220px; }
</style>
</head>
<body>
  <h1>&#9925; WeatherNet</h1>
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

    <h2 class="section-title">Mappa</h2>
    <div class="map-section">
      <div class="map-controls">
        <label><input type="radio" name="map-param" value="air_quality_index" checked> Qualit&agrave; aria</label>
        <label><input type="radio" name="map-param" value="temperature_c"> Temperatura</label>
        <label><input type="radio" name="map-param" value="humidity_pct"> Umidit&agrave;</label>
        <label><input type="radio" name="map-param" value="pressure_hpa"> Pressione</label>
      </div>
      <div id="map"></div>
    </div>

    <?php if (!empty($historyByProbe)): ?>
      <h2 class="section-title">Andamento (ultime <?= HISTORY_WINDOW_HOURS ?> ore)</h2>
      <?php foreach ($probes as $probeIndex => $probe): ?>
        <?php $series = $historyByProbe[$probe['name']] ?? null; ?>
        <?php if ($series): ?>
          <h3 class="section-title" style="margin-top:1.5rem;font-size:1rem;color:#666;">
            <?= htmlspecialchars($probe['name']) ?>
          </h3>
          <div class="charts-grid">
            <?php foreach (SENSOR_CHART_CONFIG as $sensorType => $cfg): ?>
              <div class="card chart-card">
                <h3><?= $cfg['label'] ?></h3>
                <canvas id="chart-<?= $probeIndex ?>-<?= $sensorType ?>"></canvas>
              </div>
            <?php endforeach; ?>
          </div>
        <?php endif; ?>
      <?php endforeach; ?>
    <?php endif; ?>
  <?php endif; ?>

  <footer>
    Coordinate approssimate per tutela della privacy &middot; Indice qualit&agrave;
    aria euristico, non certificato
    <br>
    <a href="https://github.com/falberti/weathernet" target="_blank" rel="noopener">Il progetto su GitHub</a>
  </footer>

<?php if (!empty($probes)): ?>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <script>
    // 'series' is dropped here -- it's the same data already in
    // historyByProbe below, no need to send it twice.
    const probes = <?= json_encode(array_map(function (array $p) {
        unset($p['series']);
        return $p;
    }, $probes)) ?>;
    const historyByProbe = <?= json_encode($historyByProbe) ?>;
    const sensorChartConfig = <?= json_encode(SENSOR_CHART_CONFIG) ?>;

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
          `Temperatura: ${probe.readings.temperature_c ?? '--'} &deg;C<br>` +
          `Umidità: ${probe.readings.humidity_pct ?? '--'}%<br>` +
          `Pressione: ${probe.readings.pressure_hpa ?? '--'} hPa<br>` +
          `Qualità aria: ${probe.readings.air_quality_index ?? '--'}`
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

    // --- Charts ---
    // Matched to canvas ids by array position, not by name: the PHP
    // side renders <canvas> ids from this exact same probes array
    // (json_encode()'d above) in the exact same order, so the index
    // is a stable, HTML-id-safe pairing even if a probe's name isn't.
    probes.forEach((probe, probeIndex) => {
      const series = historyByProbe[probe.name];
      if (!series) return;
      Object.entries(sensorChartConfig).forEach(([sensorType, cfg]) => {
        const canvas = document.getElementById(`chart-${probeIndex}-${sensorType}`);
        if (!canvas) return;
        const points = series[sensorType] || [];
        new Chart(canvas, {
          type: 'line',
          data: {
            labels: points.map((p) => new Date(p.time).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })),
            datasets: [{
              data: points.map((p) => p.value),
              borderColor: cfg.color,
              backgroundColor: cfg.color + '33',
              fill: true,
              pointRadius: 0,
              tension: 0.25,
            }],
          },
          options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { maxTicksLimit: 6, autoSkip: true } },
            },
          },
        });
      });
    });
  </script>
<?php endif; ?>
</body>
</html>
