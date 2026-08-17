import random

from .base import Sensor

# Stand in for real BME680 / SPS30 / wind-rain drivers, which are
# explicitly deferred -- see PROJECT_SPEC.md Section 12. Do not wire up
# real I2C/SPI hardware libraries here.


class MockTemperatureSensor(Sensor):
    sensor_type = "temperature_c"
    unit = "c"

    def read(self) -> float:
        return round(random.uniform(10.0, 30.0), 2)


class MockHumiditySensor(Sensor):
    sensor_type = "humidity_pct"
    unit = "pct"

    def read(self) -> float:
        return round(random.uniform(30.0, 90.0), 2)


class MockPressureSensor(Sensor):
    sensor_type = "pressure_hpa"
    unit = "hpa"

    def read(self) -> float:
        return round(random.uniform(980.0, 1040.0), 2)
