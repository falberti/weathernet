from typing import Dict, Type

from .base import Sensor
from .bme680 import (
    BME680GasResistanceSensor,
    BME680HumiditySensor,
    BME680PressureSensor,
    BME680TemperatureSensor,
)
from .bmp280 import BMP280PressureSensor, BMP280TemperatureSensor
from .htu21d import HTU21DHumiditySensor, HTU21DTemperatureSensor
from .mock import MockHumiditySensor, MockPressureSensor, MockTemperatureSensor
from .sps30 import (
    SPS30PM1_0Sensor,
    SPS30PM2_5Sensor,
    SPS30PM4_0Sensor,
    SPS30PM10Sensor,
)

# Maps the sensor names used in probe.yaml's `sensors:` list to a
# driver class. This is the extension point: a probe's active sensors
# are a config change, and adding a new driver (real or otherwise) is
# one module plus one entry here.
#
# Importing bme680.py/bmp280.py/htu21d.py/sps30.py here is safe even
# without their real-hardware packages or real hardware installed --
# each module guards its own hardware import and only raises when one
# of its sensors is actually *read*, not when it's merely instantiated
# (see each module's docstring).
#
# BMP280/HTU21D-F/SPS30 sensor_type values are prefixed with the chip
# name (unlike BME680/mock's generic `temperature_c` etc.) so that a
# probe wiring more than one of these at once -- e.g. during breadboard
# bring-up, comparing BMP280 vs. HTU21D-F temperature side by side --
# gets distinct time series instead of two sensors silently colliding
# under the same sensor_type.
SENSOR_REGISTRY: Dict[str, Type[Sensor]] = {
    "mock_temperature": MockTemperatureSensor,
    "mock_humidity": MockHumiditySensor,
    "mock_pressure": MockPressureSensor,
    "bme680_temperature": BME680TemperatureSensor,
    "bme680_humidity": BME680HumiditySensor,
    "bme680_pressure": BME680PressureSensor,
    "bme680_gas": BME680GasResistanceSensor,
    "bmp280_temperature": BMP280TemperatureSensor,
    "bmp280_pressure": BMP280PressureSensor,
    "htu21d_temperature": HTU21DTemperatureSensor,
    "htu21d_humidity": HTU21DHumiditySensor,
    "sps30_pm1_0": SPS30PM1_0Sensor,
    "sps30_pm2_5": SPS30PM2_5Sensor,
    "sps30_pm4_0": SPS30PM4_0Sensor,
    "sps30_pm10": SPS30PM10Sensor,
}


def build_sensor(name: str) -> Sensor:
    try:
        sensor_cls = SENSOR_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(SENSOR_REGISTRY))
        raise ValueError(f"Unknown sensor '{name}' in config (known: {known})") from None
    return sensor_cls()
