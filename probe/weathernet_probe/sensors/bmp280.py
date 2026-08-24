"""Real BMP280 driver (temperature, pressure) over I2C.

Requires the sensor wired for I2C mode -- see the top-level README's
wiring section. Uses Pimoroni's `bmp280` library (same author and style
as the `bme680` package bme680.py already depends on), which handles
the chip's factory calibration/compensation internally.

Assumes I2C address 0x77 (Pimoroni's `I2C_ADDRESS_VCC`), not the more
common 0x76 default -- this project's BMP280 breakout (Adafruit #2651)
also supports SPI and pulls its SDO pin up to 3.3V through an onboard
10k resistor, so leaving SDO disconnected (as I2C mode requires) lands
on 0x77, not 0x76. See the README's wiring section.

The `bmp280` package is a hardware-specific dependency (needs `smbus2`,
which only works on Linux) -- importing it is guarded so this module,
and therefore the whole sensor registry, still imports cleanly on a dev
machine or CI runner with no I2C bus at all. Only actually *using* a
sensor here without the package installed raises SensorReadError, not
importing this file.
"""
import threading

from .base import Sensor, SensorReadError

try:
    from bmp280 import BMP280 as _BMP280
    from bmp280 import I2C_ADDRESS_VCC as _I2C_ADDRESS
    from smbus2 import SMBus
except ImportError:
    _BMP280 = None

# The two Sensor subclasses below share one underlying device -- a
# second BMP280() instance would open a second /dev/i2c-1 handle and
# re-run setup() needlessly. Unlike BME680 (see bme680.py), the chip
# defaults to "normal" power mode, i.e. continuous background
# conversion, so reads don't need the same TTL-cache treatment: each
# get_temperature()/get_pressure() call just fetches the latest sample
# the chip already converted on its own, rather than triggering and
# waiting for a new one.
_device = None
_device_lock = threading.Lock()

# Bosch's own operating range (BMP280 datasheet, "Absolute Maximum
# Ratings" / "Operating temperature" -- bst-bmp280-ds001.pdf, checked
# directly, not assumed): a reading outside these bounds cannot be a
# real measurement regardless of how extreme the actual weather is --
# it's a corrupted read (e.g. an I2C bit error) that happened to
# compensate into a plausible-looking number instead of an obvious
# garbage one. Hit for real: a bit error once produced a reading of
# 180C, which silently polluted Grafana for hours before anyone
# noticed, precisely because 180 doesn't *look* like garbage the way
# e.g. a negative Kelvin value would.
_TEMPERATURE_RANGE_C = (-40.0, 85.0)
_PRESSURE_RANGE_HPA = (300.0, 1100.0)


def _get_device():
    global _device
    if _BMP280 is None:
        raise SensorReadError(
            "the 'bmp280' package is not installed -- add it to probe/requirements.txt "
            "and reinstall (see the top-level README's BMP280 section)"
        )
    with _device_lock:
        if _device is None:
            try:
                _device = _BMP280(i2c_addr=_I2C_ADDRESS, i2c_dev=SMBus(1))
            except (RuntimeError, OSError) as exc:
                raise SensorReadError(f"could not initialize BMP280 over I2C: {exc}") from exc
        return _device


def _check_plausible(value: float, value_range: tuple, unit: str) -> float:
    low, high = value_range
    if not (low <= value <= high):
        raise SensorReadError(
            f"BMP280 reading {value}{unit} is outside the chip's own operating range "
            f"({low}..{high}{unit}) -- treating as a corrupted read, not a real measurement"
        )
    return value


class BMP280TemperatureSensor(Sensor):
    sensor_type = "bmp280_temperature_c"
    unit = "c"

    def read(self) -> float:
        device = _get_device()
        with _device_lock:
            try:
                value = round(device.get_temperature(), 2)
            except (RuntimeError, OSError) as exc:
                raise SensorReadError(f"BMP280 temperature read failed: {exc}") from exc
        return _check_plausible(value, _TEMPERATURE_RANGE_C, "C")


class BMP280PressureSensor(Sensor):
    sensor_type = "bmp280_pressure_hpa"
    unit = "hpa"

    def read(self) -> float:
        device = _get_device()
        with _device_lock:
            try:
                value = round(device.get_pressure(), 2)
            except (RuntimeError, OSError) as exc:
                raise SensorReadError(f"BMP280 pressure read failed: {exc}") from exc
        return _check_plausible(value, _PRESSURE_RANGE_HPA, "hPa")
