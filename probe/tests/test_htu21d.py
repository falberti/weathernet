import pytest

from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.htu21d import (
    HTU21DHumiditySensor,
    HTU21DTemperatureSensor,
    _crc8,
)
from weathernet_probe.sensors import htu21d


def _raw_for_temperature(temp_c: float) -> int:
    """Inverse of HTU21DTemperatureSensor's own conversion formula, so
    tests can drive read() to a chosen temperature without needing a
    real chip or hand-computing CRC-valid I2C bytes.
    """
    return round((temp_c + 46.85) * 65536.0 / 175.72)


def _raw_for_humidity(humidity_pct: float) -> int:
    """Inverse of HTU21DHumiditySensor's own conversion formula."""
    return round((humidity_pct + 6.0) * 65536.0 / 125.0)


def _stub_raw_reading(monkeypatch, raw: int):
    # Bypasses the real I2C transaction (and its CRC-8 check) entirely
    # -- _read_measurement is what talks to hardware; read() just feeds
    # whatever it returns through the temperature/humidity formula.
    monkeypatch.setattr(htu21d, "_read_measurement", lambda command, delay_seconds: raw)


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (HTU21DTemperatureSensor, "htu21d_temperature_c"),
        (HTU21DHumiditySensor, "htu21d_humidity_pct"),
    ],
)
def test_htu21d_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware -- only read() does. This
    # is what lets sensors/registry.py import (and every other test in
    # this suite run) on a dev machine with no I2C bus at all.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize("sensor_cls", [HTU21DTemperatureSensor, HTU21DHumiditySensor])
def test_htu21d_read_fails_clearly_without_smbus2_installed(sensor_cls, monkeypatch):
    # smbus2 IS installed on this test machine (it's an unconditional
    # dependency, see requirements.txt), so simulate its absence the
    # same way a Pi-less dev machine without it would see it, rather
    # than relying on the test environment happening to lack it.
    monkeypatch.setattr("weathernet_probe.sensors.htu21d.SMBus", None)
    with pytest.raises(SensorReadError, match="smbus2.*not installed"):
        sensor_cls().read()


def test_crc8_matches_known_htu21d_datasheet_example():
    # From the HTU21D(-F) datasheet's own worked example: the message
    # 0xDC (a single byte) checksums to 0x79 under this CRC-8 variant.
    assert _crc8(bytes([0xDC])) == 0x79


def test_plausible_temperature_passes_through(monkeypatch):
    _stub_raw_reading(monkeypatch, _raw_for_temperature(21.4))
    assert HTU21DTemperatureSensor().read() == pytest.approx(21.4, abs=0.05)


def test_temperature_far_outside_operating_range_is_rejected(monkeypatch):
    # Same class of incident bmp280.py's/bme680.py's checks guard
    # against: a corrupted I2C read that still passes CRC-8 (the
    # checksum only proves the bytes weren't mangled in transit, not
    # that the chip converted a real measurement) but decodes to a
    # physically impossible value.
    _stub_raw_reading(monkeypatch, _raw_for_temperature(-80.0))
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        HTU21DTemperatureSensor().read()


def test_plausible_humidity_passes_through(monkeypatch):
    _stub_raw_reading(monkeypatch, _raw_for_humidity(45.0))
    assert HTU21DHumiditySensor().read() == pytest.approx(45.0, abs=0.05)


@pytest.mark.parametrize("value", [-3.0, 103.0])
def test_humidity_within_known_formula_overshoot_is_clamped_not_rejected(monkeypatch, value):
    # The datasheet's own linear fit is known to overshoot slightly
    # past 0/100% right at the physical extremes -- a legitimate
    # near-boundary reading, not corruption, so it must be clamped to
    # a valid percentage rather than raising.
    _stub_raw_reading(monkeypatch, _raw_for_humidity(value))
    result = HTU21DHumiditySensor().read()
    assert result == (0.0 if value < 0 else 100.0)


def test_humidity_far_outside_plausible_range_is_rejected(monkeypatch):
    _stub_raw_reading(monkeypatch, _raw_for_humidity(200.0))
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        HTU21DHumiditySensor().read()
