import pytest

from weathernet_probe.sensors.mock import (
    MockHumiditySensor,
    MockPressureSensor,
    MockTemperatureSensor,
)
from weathernet_probe.sensors.registry import SENSOR_REGISTRY, build_sensor


@pytest.mark.parametrize(
    "sensor_cls,expected_type,value_range",
    [
        (MockTemperatureSensor, "temperature_c", (10.0, 30.0)),
        (MockHumiditySensor, "humidity_pct", (30.0, 90.0)),
        (MockPressureSensor, "pressure_hpa", (980.0, 1040.0)),
    ],
)
def test_mock_sensor_returns_value_in_expected_shape(sensor_cls, expected_type, value_range):
    sensor = sensor_cls()
    value = sensor.read()

    assert sensor.sensor_type == expected_type
    assert isinstance(value, float)
    low, high = value_range
    assert low <= value <= high


def test_registry_builds_every_known_sensor():
    for name in SENSOR_REGISTRY:
        sensor = build_sensor(name)
        assert sensor.sensor_type


def test_registry_rejects_unknown_sensor_name():
    with pytest.raises(ValueError):
        build_sensor("not_a_real_sensor")
