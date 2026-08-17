from typing import Dict, Type

from .base import Sensor
from .bme680 import (
    BME680GasResistanceSensor,
    BME680HumiditySensor,
    BME680PressureSensor,
    BME680TemperatureSensor,
)
from .mock import MockHumiditySensor, MockPressureSensor, MockTemperatureSensor

# Maps the sensor names used in probe.yaml's `sensors:` list to a
# driver class. This is the extension point: a probe's active sensors
# are a config change, and adding a new driver (real or otherwise) is
# one module plus one entry here.
#
# Importing bme680.py here is safe even without the `bme680` package or
# real hardware installed -- that module guards its own hardware import
# and only raises when a BME680 sensor is actually *read*, not when
# it's merely instantiated (see its docstring).
SENSOR_REGISTRY: Dict[str, Type[Sensor]] = {
    "mock_temperature": MockTemperatureSensor,
    "mock_humidity": MockHumiditySensor,
    "mock_pressure": MockPressureSensor,
    "bme680_temperature": BME680TemperatureSensor,
    "bme680_humidity": BME680HumiditySensor,
    "bme680_pressure": BME680PressureSensor,
    "bme680_gas": BME680GasResistanceSensor,
}


def build_sensor(name: str) -> Sensor:
    try:
        sensor_cls = SENSOR_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(SENSOR_REGISTRY))
        raise ValueError(f"Unknown sensor '{name}' in config (known: {known})") from None
    return sensor_cls()
