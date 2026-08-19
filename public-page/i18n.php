<?php
/**
 * Minimal i18n: an explicit choice from the picker (?lang=, see
 * index.php's <form>) wins, remembered in a cookie for next time;
 * otherwise browser-language auto-detection (Accept-Language).
 *
 * The cookie is first-party and purely functional -- it stores
 * nothing but the language the visitor just explicitly picked, for
 * exactly the purpose they picked it for. That's the textbook
 * "strictly necessary" exemption under ePrivacy/GDPR (storage the
 * user's own action explicitly requested, not tracking), so it does
 * not require cookie consent any more than the rest of this
 * otherwise-cookie-free site does -- see README.md.
 */

const SUPPORTED_LOCALES = ['it', 'en', 'fr', 'de'];
const DEFAULT_LOCALE = 'en';
const LOCALE_COOKIE = 'weathernet_lang';

const LOCALE_LABELS = [
    'it' => '🇮🇹 Italiano',
    'en' => '🇬🇧 English',
    'fr' => '🇫🇷 Français',
    'de' => '🇩🇪 Deutsch',
];

function detect_locale(): string
{
    if (isset($_GET['lang']) && in_array($_GET['lang'], SUPPORTED_LOCALES, true)) {
        $locale = $_GET['lang'];
        // Only set when a request actually carries a fresh, explicit
        // choice -- never on a plain page load, so this cookie only
        // ever reflects something the visitor just did themselves.
        // secure:true means it's silently skipped over a plain-HTTP
        // deployment, degrading to Accept-Language every time rather
        // than failing -- acceptable, this page should be HTTPS anyway.
        setcookie(LOCALE_COOKIE, $locale, [
            'expires' => time() + 60 * 60 * 24 * 365,
            'path' => '/',
            'secure' => true,
            'httponly' => true,
            'samesite' => 'Lax',
        ]);
        return $locale;
    }

    if (isset($_COOKIE[LOCALE_COOKIE]) && in_array($_COOKIE[LOCALE_COOKIE], SUPPORTED_LOCALES, true)) {
        return $_COOKIE[LOCALE_COOKIE];
    }

    $header = $_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? '';
    if ($header === '') {
        return DEFAULT_LOCALE;
    }

    // "it-IT,it;q=0.9,en;q=0.8" -> [["it", 1.0], ["it", 0.9], ["en", 0.8]],
    // sorted by quality descending; first entry that's one of our
    // supported locales wins.
    $candidates = [];
    foreach (explode(',', $header) as $part) {
        $part = trim($part);
        if ($part === '') {
            continue;
        }
        $pieces = explode(';', $part);
        $lang = strtolower(substr(trim($pieces[0]), 0, 2));
        $quality = 1.0;
        if (isset($pieces[1]) && preg_match('/q=([\d.]+)/', $pieces[1], $m)) {
            $quality = (float) $m[1];
        }
        $candidates[] = [$lang, $quality];
    }
    usort($candidates, static function (array $a, array $b): int {
        return $b[1] <=> $a[1];
    });

    foreach ($candidates as $candidate) {
        if (in_array($candidate[0], SUPPORTED_LOCALES, true)) {
            return $candidate[0];
        }
    }
    return DEFAULT_LOCALE;
}

const TRANSLATIONS = [
    'it' => [
        'page_title' => 'WeatherNet -- Dati in tempo reale',
        'subtitle' => 'Rilevazioni meteo in tempo reale dalle sonde della rete',
        'empty_state' => 'Dati momentaneamente non disponibili. Riprova tra qualche minuto.',
        'zone' => 'Zona',
        'updated' => 'aggiornato',
        'time_never' => 'mai',
        'time_just_now' => 'poco fa',
        'time_minutes_ago' => '%d min fa',
        'time_hours_ago' => '%d h fa',
        'reading_temperature' => 'Temperatura',
        'reading_humidity' => 'Umidità',
        'reading_pressure' => 'Pressione',
        'reading_aqi' => 'Qualità aria',
        'aqi_good' => 'Buona',
        'aqi_moderate' => 'Moderata',
        'aqi_poor' => 'Scarsa',
        'map_title' => 'Mappa',
        'footer_disclaimer' => 'Coordinate approssimate per tutela della privacy &middot; Indice qualit&agrave; aria euristico, non certificato',
        'telegram_tooltip' => 'Bot &quot;Bernacca&quot; su Telegram',
        'github_tooltip' => 'Il progetto su GitHub',
    ],
    'en' => [
        'page_title' => 'WeatherNet -- Live Weather Data',
        'subtitle' => "Real-time weather readings from the network's probes",
        'empty_state' => 'Data temporarily unavailable. Please try again in a few minutes.',
        'zone' => 'Area',
        'updated' => 'updated',
        'time_never' => 'never',
        'time_just_now' => 'just now',
        'time_minutes_ago' => '%d min ago',
        'time_hours_ago' => '%d h ago',
        'reading_temperature' => 'Temperature',
        'reading_humidity' => 'Humidity',
        'reading_pressure' => 'Pressure',
        'reading_aqi' => 'Air quality',
        'aqi_good' => 'Good',
        'aqi_moderate' => 'Moderate',
        'aqi_poor' => 'Poor',
        'map_title' => 'Map',
        'footer_disclaimer' => 'Coordinates approximated for privacy &middot; Air quality index is a heuristic, not certified',
        'telegram_tooltip' => 'The &quot;Bernacca&quot; bot on Telegram',
        'github_tooltip' => 'The project on GitHub',
    ],
    'fr' => [
        'page_title' => 'WeatherNet -- Données météo en direct',
        'subtitle' => 'Relevés météo en temps réel des sondes du réseau',
        'empty_state' => 'Données temporairement indisponibles. Réessayez dans quelques minutes.',
        'zone' => 'Zone',
        'updated' => 'mis à jour',
        'time_never' => 'jamais',
        'time_just_now' => "à l'instant",
        'time_minutes_ago' => 'il y a %d min',
        'time_hours_ago' => 'il y a %d h',
        'reading_temperature' => 'Température',
        'reading_humidity' => 'Humidité',
        'reading_pressure' => 'Pression',
        'reading_aqi' => "Qualité de l'air",
        'aqi_good' => 'Bonne',
        'aqi_moderate' => 'Modérée',
        'aqi_poor' => 'Mauvaise',
        'map_title' => 'Carte',
        'footer_disclaimer' => 'Coordonnées approximatives pour la protection de la vie privée &middot; Indice de qualité de l\'air heuristique, non certifié',
        'telegram_tooltip' => 'Le bot &quot;Bernacca&quot; sur Telegram',
        'github_tooltip' => 'Le projet sur GitHub',
    ],
    'de' => [
        'page_title' => 'WeatherNet -- Live-Wetterdaten',
        'subtitle' => 'Echtzeit-Wetterdaten von den Sonden des Netzwerks',
        'empty_state' => 'Daten vorübergehend nicht verfügbar. Bitte in ein paar Minuten erneut versuchen.',
        'zone' => 'Zone',
        'updated' => 'aktualisiert',
        'time_never' => 'nie',
        'time_just_now' => 'gerade eben',
        'time_minutes_ago' => 'vor %d Min',
        'time_hours_ago' => 'vor %d Std',
        'reading_temperature' => 'Temperatur',
        'reading_humidity' => 'Luftfeuchtigkeit',
        'reading_pressure' => 'Luftdruck',
        'reading_aqi' => 'Luftqualität',
        'aqi_good' => 'Gut',
        'aqi_moderate' => 'Mäßig',
        'aqi_poor' => 'Schlecht',
        'map_title' => 'Karte',
        'footer_disclaimer' => 'Koordinaten aus Datenschutzgründen ungenau &middot; Luftqualitätsindex heuristisch, nicht zertifiziert',
        'telegram_tooltip' => 'Der &quot;Bernacca&quot;-Bot auf Telegram',
        'github_tooltip' => 'Das Projekt auf GitHub',
    ],
];

/** Translated string for the given key in $locale, with optional
 * sprintf()-style args (used for e.g. "%d min ago"). Falls back to
 * the English string, then to the key itself, if something's missing
 * -- a translation gap should never break the page.
 */
function t(string $locale, string $key, ...$args): string
{
    $template = TRANSLATIONS[$locale][$key] ?? TRANSLATIONS[DEFAULT_LOCALE][$key] ?? $key;
    return $args ? vsprintf($template, $args) : $template;
}
