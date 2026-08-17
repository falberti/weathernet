"""Real BME680 driver (temperature, humidity, pressure, gas resistance)
over I2C.

Requires the sensor wired for I2C mode and I2C enabled on the Pi -- see
the top-level README's wiring section. Uses Pimoroni's `bme680`
library, which handles the chip's factory calibration/compensation
internally; reimplementing that by hand isn't worth it when a
maintained library already exists for exactly this chip.

The `bme680` package is a hardware-specific dependency (pulls in
`smbus2`, which only works on Linux) -- importing it is guarded so this
module, and therefore the whole sensor registry, still imports cleanly
on a dev machine or CI runner with no I2C bus at all. Only actually
*using* a sensor here without the package installed raises
SensorReadError, not importing this file.
"""
import threading

from .base import Sensor, SensorReadError

try:
    import bme680 as _bme680_lib
except ImportError:
    _bme680_lib = None

# All four Sensor subclasses below share one underlying device and its
# calibration state -- a second bme680.BME680() instance would
# re-initialize/re-calibrate the same physical chip on every read,
# which is wasteful and risks bus contention if it overlaps another
# instance's in-progress read.
_device = None
_device_lock = threading.Lock()

# Bosch's gas sensor needs the heater to reach a stable target
# temperature before a reading is trustworthy -- this is normal, not a
# fault, hence the dedicated exception message below rather than a
# generic failure.
_GAS_HEATER_TEMPERATURE_C = 320
_GAS_HEATER_DURATION_MS = 150


def _get_device():
    global _device
    if _bme680_lib is None:
        raise SensorReadError(
            "the 'bme680' package is not installed -- add it to probe/requirements.txt "
            "and reinstall (see the top-level README's BME680 section)"
        )
    with _device_lock:
        if _device is None:
            try:
                device = _bme680_lib.BME680(_bme680_lib.constants.I2C_ADDR_PRIMARY)
            except (RuntimeError, OSError) as exc:
                raise SensorReadError(f"could not initialize BME680 over I2C: {exc}") from exc
            device.set_humidity_oversample(_bme680_lib.constants.OS_2X)
            device.set_pressure_oversample(_bme680_lib.constants.OS_4X)
            device.set_temperature_oversample(_bme680_lib.constants.OS_8X)
            device.set_filter(_bme680_lib.constants.FILTER_SIZE_3)
            device.set_gas_status(_bme680_lib.constants.ENABLE_GAS_MEAS)
            device.set_gas_heater_temperature(_GAS_HEATER_TEMPERATURE_C)
            device.set_gas_heater_duration(_GAS_HEATER_DURATION_MS)
            device.select_gas_heater_profile(0)
            _device = device
        return _device


def _read_all():
    device = _get_device()
    if not device.get_sensor_data():
        raise SensorReadError("BME680 did not return fresh data this cycle")
    return device.data


class BME680TemperatureSensor(Sensor):
    sensor_type = "temperature_c"
    unit = "c"

    def read(self) -> float:
        return round(_read_all().temperature, 2)


class BME680HumiditySensor(Sensor):
    sensor_type = "humidity_pct"
    unit = "pct"

    def read(self) -> float:
        return round(_read_all().humidity, 2)


class BME680PressureSensor(Sensor):
    sensor_type = "pressure_hpa"
    unit = "hpa"

    def read(self) -> float:
        return round(_read_all().pressure, 2)


class BME680GasResistanceSensor(Sensor):
    """Raw gas sensor resistance in ohms -- higher generally means
    cleaner air, but this is uncalibrated. Bosch's own air-quality-index
    conversion (BSEC) is proprietary and needs a multi-day burn-in; out
    of scope here. Useful as a relative/trend indicator as-is.
    """

    sensor_type = "gas_resistance_ohm"
    unit = "ohm"

    def read(self) -> float:
        data = _read_all()
        if not data.heat_stable:
            raise SensorReadError("BME680 gas heater not yet stable this cycle")
        return round(data.gas_resistance, 2)
