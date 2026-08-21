import pytest

from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.bmp280 import (
    BMP280PressureSensor,
    BMP280TemperatureSensor,
)


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (BMP280TemperatureSensor, "bmp280_temperature_c"),
        (BMP280PressureSensor, "bmp280_pressure_hpa"),
    ],
)
def test_bmp280_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `bmp280` package --
    # only read() does. This is what lets sensors/registry.py import
    # (and every other test in this suite run) on a dev machine with no
    # I2C bus and no `bmp280` package installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize("sensor_cls", [BMP280TemperatureSensor, BMP280PressureSensor])
def test_bmp280_read_fails_clearly_without_the_library_installed(sensor_cls, monkeypatch):
    # Unlike bme680/smbus2 (Linux-only C extensions, naturally absent on
    # a dev machine or most CI runners), `bmp280` is pure Python and may
    # actually be installed here -- simulate its absence explicitly
    # rather than relying on the test environment happening to lack it.
    monkeypatch.setattr("weathernet_probe.sensors.bmp280._BMP280", None)
    with pytest.raises(SensorReadError, match="bmp280.*not installed"):
        sensor_cls().read()
