import pytest

from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.htu21d import (
    HTU21DHumiditySensor,
    HTU21DTemperatureSensor,
    _crc8,
)


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
