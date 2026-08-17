import pytest

from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.bme680 import (
    BME680GasResistanceSensor,
    BME680HumiditySensor,
    BME680PressureSensor,
    BME680TemperatureSensor,
)


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (BME680TemperatureSensor, "temperature_c"),
        (BME680HumiditySensor, "humidity_pct"),
        (BME680PressureSensor, "pressure_hpa"),
        (BME680GasResistanceSensor, "gas_resistance_ohm"),
    ],
)
def test_bme680_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `bme680` package --
    # only read() does. This is what lets sensors/registry.py import
    # (and every other test in this suite run) on a dev machine with no
    # I2C bus and no `bme680` package installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize(
    "sensor_cls",
    [BME680TemperatureSensor, BME680HumiditySensor, BME680PressureSensor, BME680GasResistanceSensor],
)
def test_bme680_read_fails_clearly_without_the_library_installed(sensor_cls):
    # On this test machine (and any CI runner) the `bme680` package
    # isn't installed -- read() must fail with a clear, actionable
    # SensorReadError (caught and logged per-sensor by main.py, not a
    # crash), not some opaque ImportError/AttributeError.
    with pytest.raises(SensorReadError, match="bme680.*not installed"):
        sensor_cls().read()
