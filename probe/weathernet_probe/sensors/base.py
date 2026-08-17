from abc import ABC, abstractmethod
from typing import Optional


class SensorReadError(Exception):
    """Raised by a Sensor when it fails to produce a reading.

    A failed sensor must not crash the reporting cycle -- callers catch
    this (and log it) per-sensor and carry on with the rest.
    """


class Sensor(ABC):
    """Common interface every sensor driver (mock or real) implements.

    Adding a real driver later (BME680, SPS30, ...) means adding one
    module here and one entry in registry.py -- nothing in main.py or
    transport.py needs to change.
    """

    sensor_type: str
    unit: Optional[str] = None

    @abstractmethod
    def read(self) -> float:
        """Return the current reading, or raise SensorReadError."""
        raise NotImplementedError
