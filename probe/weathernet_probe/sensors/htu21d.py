"""Real HTU21D-F driver (temperature, humidity) over I2C.

Unlike BME680/BMP280, no third-party PyPI package for this chip is both
current and reliably maintained -- Adafruit's is CircuitPython/Blinka
based, a heavier and differently-styled dependency than the smbus2-direct
approach the rest of this project's real drivers use. The chip's I2C
protocol is simple enough (one trigger command, a 16-bit result, one
CRC-8 byte) that implementing it directly against `smbus2` -- already an
unconditional dependency, see bme680.py -- keeps this consistent with the
rest of the codebase instead of introducing a new dependency style for
one sensor.

Deliberately uses "no hold master" mode (the chip releases the I2C bus
after a trigger command and expects the host to come back and read once
conversion is done) rather than "hold master" mode (the chip stretches
SCL for the whole conversion): the Raspberry Pi's I2C controller has
well-documented problems with clock stretching, so this avoids relying
on it at all -- the host just sleeps for the datasheet's max conversion
time instead.

Importing this module never touches hardware; only actually *reading* a
sensor without `smbus2` installed, or without the chip wired up, raises
SensorReadError.
"""
import threading
import time

from .base import Sensor, SensorReadError

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:
    SMBus = None

_I2C_ADDRESS = 0x40
_CMD_TRIGGER_TEMPERATURE_NOHOLD = 0xF3
_CMD_TRIGGER_HUMIDITY_NOHOLD = 0xF5

# TE Connectivity's own operating range (HTU21D(F) datasheet, "Operating
# Conditions"): a reading outside these bounds cannot be a real
# measurement -- it's a corrupted read that passed the CRC-8 check
# below (checksums catch bit errors in transit, not a chip that
# converted garbage in the first place) but is still physically
# impossible. The humidity formula is known to overshoot slightly past
# 0/100% right at the physical extremes (per the same datasheet) --
# that's a normal artifact of the linear fit, not corruption, so the
# plausible range is padded a few points past 0/100 to still accept it
# rather than rejecting a legitimate near-boundary reading.
_TEMPERATURE_RANGE_C = (-40.0, 125.0)
_HUMIDITY_PLAUSIBLE_RANGE_PCT = (-5.0, 105.0)

# Datasheet max conversion times at the chip's default (14-bit
# temperature / 12-bit humidity) resolution, with a small margin.
_TEMPERATURE_CONVERSION_SECONDS = 0.06
_HUMIDITY_CONVERSION_SECONDS = 0.03

# Shared across both Sensor subclasses below, same rationale as
# bme680.py's shared `_device`: a second SMBus(1) would open a second
# handle to the same physical bus needlessly.
_bus = None
_bus_lock = threading.Lock()


def _get_bus():
    global _bus
    if SMBus is None:
        raise SensorReadError(
            "the 'smbus2' package is not installed -- add it to probe/requirements.txt "
            "and reinstall (see the top-level README's HTU21D-F section)"
        )
    with _bus_lock:
        if _bus is None:
            try:
                _bus = SMBus(1)
            except OSError as exc:
                raise SensorReadError(f"could not open I2C bus for HTU21D-F: {exc}") from exc
        return _bus


def _crc8(data: bytes) -> int:
    """HTU21D-F's checksum: CRC-8 with polynomial x^8+x^5+x^4+1 (0x31), init 0x00."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def _read_measurement(command: int, delay_seconds: float) -> int:
    bus = _get_bus()
    with _bus_lock:
        try:
            bus.write_byte(_I2C_ADDRESS, command)
            time.sleep(delay_seconds)
            read = i2c_msg.read(_I2C_ADDRESS, 3)
            bus.i2c_rdwr(read)
        except OSError as exc:
            raise SensorReadError(f"HTU21D-F I2C read failed: {exc}") from exc

        msb, lsb, checksum = bytes(read)
        if _crc8(bytes((msb, lsb))) != checksum:
            raise SensorReadError("HTU21D-F reading failed CRC-8 checksum")

        # The two LSBs are status bits, not part of the measurement.
        return ((msb << 8) | lsb) & 0xFFFC


def _check_plausible(value: float, value_range: tuple, label: str, unit: str) -> float:
    low, high = value_range
    if not (low <= value <= high):
        raise SensorReadError(
            f"HTU21D-F {label} reading {value}{unit} is outside the sensor's plausible range "
            f"({low}..{high}{unit}) -- treating as a corrupted read, not a real measurement"
        )
    return value


class HTU21DTemperatureSensor(Sensor):
    sensor_type = "htu21d_temperature_c"
    unit = "c"

    def read(self) -> float:
        raw = _read_measurement(_CMD_TRIGGER_TEMPERATURE_NOHOLD, _TEMPERATURE_CONVERSION_SECONDS)
        value = round(-46.85 + 175.72 * raw / 65536.0, 2)
        return _check_plausible(value, _TEMPERATURE_RANGE_C, "temperature", "C")


class HTU21DHumiditySensor(Sensor):
    sensor_type = "htu21d_humidity_pct"
    unit = "pct"

    def read(self) -> float:
        raw = _read_measurement(_CMD_TRIGGER_HUMIDITY_NOHOLD, _HUMIDITY_CONVERSION_SECONDS)
        humidity = round(-6.0 + 125.0 * raw / 65536.0, 2)
        _check_plausible(humidity, _HUMIDITY_PLAUSIBLE_RANGE_PCT, "humidity", "%")
        # Clamp the formula's known small overshoot at the physical
        # extremes (already confirmed plausible above) to a valid
        # percentage -- corrupted/implausible values were already
        # rejected by _check_plausible and never reach this line.
        return round(max(0.0, min(100.0, humidity)), 2)
