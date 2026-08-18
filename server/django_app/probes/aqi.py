"""Heuristic, uncalibrated air quality score from a BME680 reading.

NOT an official/certified AQI -- a real calibrated IAQ requires Bosch's
proprietary BSEC library, out of scope for this project (see
probe/weathernet_probe/sensors/bme680.py). This mirrors the formula
used by the Grafana "Air Quality Index (heuristic)" panel
(server/grafana/provisioning/dashboards/weathernet-health.json) --
keep both in sync if the weights ever change.
"""

_GAS_WEIGHT = 0.75
_HUMIDITY_WEIGHT = 0.25
_HUMIDITY_IDEAL_PCT = 40


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
