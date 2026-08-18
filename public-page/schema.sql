-- Run once against your MySQL database (e.g. via phpMyAdmin or the
-- mysql CLI) before the first sync.php run. One row per probe --
-- sync.php overwrites it in place on every cron run, it does not
-- accumulate history here yet (see sync.php's docblock).
CREATE TABLE IF NOT EXISTS probe_cache (
    probe_name VARCHAR(200) NOT NULL PRIMARY KEY,
    hardware_type VARCHAR(32) NOT NULL,
    latitude DECIMAL(9, 6) NOT NULL,
    longitude DECIMAL(9, 6) NOT NULL,
    last_seen_at DATETIME NULL,
    temperature_c FLOAT NULL,
    humidity_pct FLOAT NULL,
    pressure_hpa FLOAT NULL,
    gas_resistance_ohm FLOAT NULL,
    air_quality_index INT NULL,
    -- JSON blob of the last-fetched /api/v1/public/history response
    -- for this probe ({"temperature_c": [...], "humidity_pct": [...],
    -- ...}) -- refreshed each cron run, not appended to. MEDIUMTEXT
    -- rather than JSON: works identically on MySQL and MariaDB, and on
    -- older MySQL versions without native JSON column support.
    history_json MEDIUMTEXT NULL,
    updated_at DATETIME NOT NULL
-- utf8 (not utf8mb4): none of this data needs 4-byte characters
-- (emoji etc.), and utf8mb4 requires MySQL 5.5.3+ -- some shared
-- hosting still runs older MySQL/MariaDB that only knows plain utf8.
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
