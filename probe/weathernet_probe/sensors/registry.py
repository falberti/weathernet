from typing import Dict, Type

from .base import Sensor
from .mock import MockHumiditySensor, MockPressureSensor, MockTemperatureSensor

# Maps the sensor names used in probe.yaml's `sensors:` list to a
# driver class. This is the extension point: a probe's active sensors
# are a config change, and adding a new driver (real or otherwise) is
# one module plus one entry here.
SENSOR_REGISTRY: Dict[str, Type[Sensor]] = {
    "mock_temperature": MockTemperatureSensor,
    "mock_humidity": MockHumiditySensor,
    "mock_pressure": MockPressureSensor,
}


def build_sensor(name: str) -> Sensor:
    try:
        sensor_cls = SENSOR_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(SENSOR_REGISTRY))
        raise ValueError(f"Unknown sensor '{name}' in config (known: {known})") from None
    return sensor_cls()
