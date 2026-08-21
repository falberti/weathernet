import pytest

from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.sps30 import (
    SPS30PM1_0Sensor,
    SPS30PM2_5Sensor,
    SPS30PM4_0Sensor,
    SPS30PM10Sensor,
)


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (SPS30PM1_0Sensor, "sps30_pm1_0_ug_m3"),
        (SPS30PM2_5Sensor, "sps30_pm2_5_ug_m3"),
        (SPS30PM4_0Sensor, "sps30_pm4_0_ug_m3"),
        (SPS30PM10Sensor, "sps30_pm10_ug_m3"),
    ],
)
def test_sps30_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `sensirion_i2c_sps30`
    # package -- only read() does. This is what lets sensors/registry.py
    # import (and every other test in this suite run) on a dev machine
    # with no I2C bus and none of the sensirion_* packages installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize(
    "sensor_cls", [SPS30PM1_0Sensor, SPS30PM2_5Sensor, SPS30PM4_0Sensor, SPS30PM10Sensor]
)
def test_sps30_read_fails_clearly_without_the_library_installed(sensor_cls, monkeypatch):
    # Unlike bme680/smbus2 (Linux-only C extensions, naturally absent on
    # a dev machine or most CI runners), the sensirion_i2c_sps30 package
    # is pure Python and may actually be installed here -- simulate its
    # absence explicitly rather than relying on the test environment
    # happening to lack it.
    monkeypatch.setattr("weathernet_probe.sensors.sps30.Sps30Device", None)
    with pytest.raises(SensorReadError, match="sensirion-i2c-sps30.*not installed"):
        sensor_cls().read()
