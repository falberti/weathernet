"""Air quality index calculations.

Two independent sources feed into the public-facing
`air_quality_index`, in preference order:

1. A real, EPA-standard AQI computed from SPS30 particulate matter
   readings (compute_pm25_aqi/compute_pm10_aqi below) -- unlike the
   heuristic this project shipped with initially, this follows a
   published, non-proprietary standard (EPA's own breakpoint tables
   and linear-interpolation formula, current as of the May 2024 PM2.5
   revision -- see "Technical Assistance Document for the Reporting of
   Daily Air Quality" at document.airnow.gov, Table 6 and Equation 1).
   EPA only defines breakpoints for PM2.5 and PM10 -- SPS30 also
   reports PM1.0 and PM4.0, but there's no official index for those
   size fractions, so they're surfaced as raw concentrations only (see
   the Grafana dashboard), not folded into this score.
2. A heuristic, uncalibrated score from a BME680 reading
   (compute_air_quality_index) -- used only as a fallback, for a probe
   that has no PM sensor at all. NOT an official/certified AQI -- a
   real calibrated IAQ from BME680's own gas sensor requires Bosch's
   proprietary BSEC library, out of scope for this project (see
   probe/weathernet_probe/sensors/bme680.py). This mirrors the formula
   used by the Grafana "Air Quality Index" panel's SQL fallback branch
   (server/grafana/provisioning/dashboards/weathernet-health.json) --
   keep both in sync if the weights ever change.

compute_overall_air_quality_index ties the two together and is what
the public API and the Telegram digest actually call.
"""
import math

_GAS_WEIGHT = 0.75
_HUMIDITY_WEIGHT = 0.25
_HUMIDITY_IDEAL_PCT = 40

# EPA AQI breakpoints: (concentration_lo, concentration_hi, aqi_lo, aqi_hi).
# Concentrations in micrograms per cubic meter, 24-hour average (this
# project reports the *latest* single reading, not a real 24h rolling
# average -- an acknowledged simplification, see compute_pm25_aqi's
# docstring). Source: Table 6, "Technical Assistance Document for the
# Reporting of Daily Air Quality", document.airnow.gov.
_PM25_BREAKPOINTS = (
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
)
_PM10_BREAKPOINTS = (
    (0, 54, 0, 50),
    (55, 154, 51, 100),
    (155, 254, 101, 150),
    (255, 354, 151, 200),
    (355, 424, 201, 300),
    (425, 604, 301, 500),
)


def compute_air_quality_index(gas_resistance, gas_baseline, humidity):
    """Returns an int 0-100, or None if any input is missing/unusable.

    gas_baseline is expected to be the recent (e.g. 7-day) rolling
    maximum gas resistance for the same probe -- the score is relative
    to that, not an absolute threshold.
    """
    if gas_resistance is None or humidity is None or not gas_baseline:
        return None

    gas_score = min(100.0, 100.0 * gas_resistance / gas_baseline)
    humidity_score = 100.0 - min(100.0, abs(humidity - _HUMIDITY_IDEAL_PCT) * 2.5)
    return round(_GAS_WEIGHT * gas_score + _HUMIDITY_WEIGHT * humidity_score)


def _epa_aqi_from_breakpoints(concentration, breakpoints):
    """Equation 1 from the AirNow technical document: linear
    interpolation between the two breakpoints the concentration falls
    between. Concentrations above the table's highest breakpoint are
    extrapolated using that same top band's slope rather than capped
    at 500, per the document's own footnote on the Hazardous category.
    """
    if concentration is None or concentration < 0:
        return None
    for bp_lo, bp_hi, i_lo, i_hi in breakpoints:
        if bp_lo <= concentration <= bp_hi:
            return round((i_hi - i_lo) / (bp_hi - bp_lo) * (concentration - bp_lo) + i_lo)
    bp_lo, bp_hi, i_lo, i_hi = breakpoints[-1]
    return round((i_hi - i_lo) / (bp_hi - bp_lo) * (concentration - bp_lo) + i_lo)


def compute_pm25_aqi(pm25_ug_m3):
    """EPA AQI from a PM2.5 mass concentration reading (µg/m³), 0-500
    (or higher -- see _epa_aqi_from_breakpoints), or None if no
    reading is available.

    EPA's own procedure truncates PM2.5 to 1 decimal place before the
    breakpoint lookup (Table 6's own truncation rule) and is defined
    against a 24-hour average concentration; this project instead
    truncates and converts the single latest SensorReading, a real
    simplification worth stating plainly rather than implying more
    rigor than there is -- the shape of the number (which category,
    roughly where in it) is meaningful, treat the exact integer as
    indicative rather than a lab-grade reading.
    """
    if pm25_ug_m3 is None:
        return None
    truncated = math.floor(pm25_ug_m3 * 10) / 10
    return _epa_aqi_from_breakpoints(truncated, _PM25_BREAKPOINTS)


def compute_pm10_aqi(pm10_ug_m3):
    """Same as compute_pm25_aqi, for PM10 (EPA truncates to a whole
    integer for this pollutant specifically, per Table 6).
    """
    if pm10_ug_m3 is None:
        return None
    truncated = math.floor(pm10_ug_m3)
    return _epa_aqi_from_breakpoints(truncated, _PM10_BREAKPOINTS)


def compute_overall_air_quality_index(pm25, pm10, gas_resistance, gas_baseline, humidity):
    """The single `air_quality_index` figure the public API and the
    Telegram digest report, as (value, is_epa_scale).

    EPA's own methodology for a combined AQI is "report the highest
    (worst) index across every pollutant with data available" (same
    document, "How to handle values from multiple pollutants") -- so
    when a probe has SPS30 data, this is max(pm25 AQI, pm10 AQI) over
    whichever of the two are available, not an average or a
    PM2.5-only figure. Falls back to the older BME680 gas-resistance
    heuristic only when *neither* PM value is available (e.g. a probe
    still running BME680 instead of SPS30) -- the two scores are on
    different scales (EPA's is 0-500+, the heuristic is 0-100) and are
    never combined with each other, hence the second return value:
    callers must label/scale the number differently depending on which
    one actually produced it, not assume either.
    """
    candidates = [
        aqi
        for aqi in (compute_pm25_aqi(pm25), compute_pm10_aqi(pm10))
        if aqi is not None
    ]
    if candidates:
        return max(candidates), True
    return compute_air_quality_index(gas_resistance, gas_baseline, humidity), False
