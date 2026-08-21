"""Canonical, public-facing sensor field names, each backed by one or
more concrete `sensor_type` strings in priority order.

BME680 (this project's original sensor) is preferred when a probe has
it; the newer chip-specific sensors (see weathernet_probe/sensors/
bmp280.py, htu21d.py -- their sensor_type values are chip-prefixed
precisely so they can coexist with BME680's without colliding, per the
README's wiring section) act as a fallback so callers never need to
know or care which hardware a given probe actually has.

Shared by the public API views (views.py) and the Telegram daily
digest (subscriptions/management/commands/send_daily_digest.py) --
both need the exact same fallback policy, and duplicating these tuples
in two places would risk them drifting out of sync whenever a new
sensor is added.

There's no equivalent of BME680's gas_resistance_ohm on the other
sensors, so that one has no fallback list -- a probe without BME680
simply reports no gas reading / no AQI, same as before any fallback
existed.
"""

TEMPERATURE_SENSOR_TYPES = ("temperature_c", "bmp280_temperature_c", "htu21d_temperature_c")
HUMIDITY_SENSOR_TYPES = ("humidity_pct", "htu21d_humidity_pct")
PRESSURE_SENSOR_TYPES = ("pressure_hpa", "bmp280_pressure_hpa")
GAS_SENSOR_TYPES = ("gas_resistance_ohm",)

# PM2.5/PM10 have no fallback chain (SPS30 is the only sensor that
# reports them) -- named here anyway so the AQI computation
# (probes/aqi.py's compute_overall_air_quality_index) and anything
# that needs to query for the latest reading share the exact same
# sensor_type strings as sensors/sps30.py, rather than repeating the
# literal string in three places.
PM25_SENSOR_TYPE = "sps30_pm2_5_ug_m3"
PM10_SENSOR_TYPE = "sps30_pm10_ug_m3"
PM_SENSOR_TYPES = (PM25_SENSOR_TYPE, PM10_SENSOR_TYPE)

ALL_SENSOR_TYPES = TEMPERATURE_SENSOR_TYPES + HUMIDITY_SENSOR_TYPES + PRESSURE_SENSOR_TYPES + GAS_SENSOR_TYPES
