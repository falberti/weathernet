"""Real SPS30 driver (particulate matter mass concentration) over I2C.

Uses Sensirion's own maintained `sensirion-i2c-sps30` package (plus its
`sensirion-i2c-driver` / `sensirion-driver-adapters` dependencies)
rather than a hand-rolled I2C protocol implementation or one of the
several unofficial single-maintainer PyPI packages -- the SPS30's I2C
framing (per-word CRC-8, a UART/I2C-shared command set, selectable
uint16/float output format) is fiddly enough, and the manufacturer's
own driver is actively maintained (unlike the alternatives found), that
reimplementing it isn't worth it. Same reasoning as bme680.py.

Imports of the `sensirion_i2c_*` / `sensirion_driver_adapters` packages
are guarded so this module, and therefore the whole sensor registry,
still imports cleanly without them installed or with no I2C bus at all.
Only actually *using* a sensor here without the packages installed
raises SensorReadError, not importing this file.
"""
import threading
import time

from .base import Sensor, SensorReadError

try:
    from sensirion_driver_adapters.i2c_adapter.i2c_channel import I2cChannel
    from sensirion_i2c_driver import CrcCalculator, I2cConnection, LinuxI2cTransceiver
    from sensirion_i2c_sps30.commands import OutputFormat
    from sensirion_i2c_sps30.device import Sps30Device
except ImportError:
    Sps30Device = None

_I2C_DEVICE_FILE = "/dev/i2c-1"
_I2C_ADDRESS = 0x69

# One physical sensor, shared across the four Sensor subclasses below --
# same rationale as bme680.py's shared `_device`: a second
# LinuxI2cTransceiver/Sps30Device would reopen /dev/i2c-1 and try to
# restart measurement mode needlessly (the chip only allows
# start_measurement() from Idle-Mode -- see Sps30Device.start_measurement).
_device = None
_device_lock = threading.Lock()
_measurement_started_at = None
_last_ready_at = None

# The SPS30's fan needs to physically spin up and the airflow needs to
# stabilize before mass-concentration readings are trustworthy -- this
# is normal, not a fault (Sensirion's datasheet gives ~8s; padded here).
_WARMUP_SECONDS = 10

# If the chip goes this long without producing fresh data -- well past
# the ~1s cadence the datasheet promises in continuous Measurement-Mode,
# but short enough to recover within the next reporting cycle regardless
# of report_interval_seconds -- assume it silently reverted to
# Idle-Mode (e.g. after an internal reset caused by a brief brownout
# that wasn't enough to drop it off I2C entirely; this driver only ever
# calls start_measurement() once, at process start, so it would
# otherwise have no way to notice and would report "not ready" forever)
# and re-issue start_measurement() to recover automatically.
_STALE_AFTER_SECONDS = 30

# The chip only produces a new sample about once a second (in
# continuous Measurement-Mode); caching a read for a couple of seconds
# means the four PM Sensor subclasses below share one I2C round trip
# per reporting cycle instead of four.
_CACHE_TTL_SECONDS = 2
_cached_values = None
_cached_at = 0.0


def _get_device():
    global _device, _measurement_started_at, _last_ready_at
    if Sps30Device is None:
        raise SensorReadError(
            "the 'sensirion-i2c-sps30' package (and its sensirion-i2c-driver / "
            "sensirion-driver-adapters dependencies) is not installed -- add it to "
            "probe/requirements.txt and reinstall (see the top-level README's SPS30 section)"
        )
    with _device_lock:
        if _device is None:
            try:
                transceiver = LinuxI2cTransceiver(_I2C_DEVICE_FILE)
                channel = I2cChannel(
                    I2cConnection(transceiver),
                    slave_address=_I2C_ADDRESS,
                    crc=CrcCalculator(8, 0x31, 0xFF, 0x0),
                )
                device = Sps30Device(channel)
                device.start_measurement(OutputFormat.OUTPUT_FORMAT_UINT16)
            except OSError as exc:
                raise SensorReadError(f"could not initialize SPS30 over I2C: {exc}") from exc
            _device = device
            _measurement_started_at = time.monotonic()
            _last_ready_at = _measurement_started_at
        return _device


def _restart_measurement(device):
    """Recovery for a device that appears to have silently reverted to
    Idle-Mode -- see _STALE_AFTER_SECONDS above. stop_measurement()
    first because start_measurement() only succeeds from Idle-Mode, and
    stopping is safe/a no-op if it's already there; if the device was
    actually still in Measurement-Mode and just slow, this briefly
    interrupts it, which is harmless.
    """
    global _measurement_started_at, _last_ready_at
    try:
        device.stop_measurement()
    except OSError:
        pass
    device.start_measurement(OutputFormat.OUTPUT_FORMAT_UINT16)
    now = time.monotonic()
    _measurement_started_at = now
    _last_ready_at = now


def _read_all():
    global _cached_values, _cached_at, _last_ready_at
    device = _get_device()
    with _device_lock:
        now = time.monotonic()
        if _cached_values is not None and (now - _cached_at) < _CACHE_TTL_SECONDS:
            return _cached_values

        if (now - _measurement_started_at) < _WARMUP_SECONDS:
            raise SensorReadError(
                f"SPS30 still warming up (readings settle {_WARMUP_SECONDS}s "
                "after measurement start)"
            )

        try:
            ready = device.read_data_ready_flag()
        except OSError as exc:
            raise SensorReadError(f"could not read SPS30 data-ready flag: {exc}") from exc

        if not ready:
            if (now - _last_ready_at) > _STALE_AFTER_SECONDS:
                try:
                    _restart_measurement(device)
                except OSError as exc:
                    raise SensorReadError(f"could not restart SPS30 measurement mode: {exc}") from exc
                raise SensorReadError(
                    f"SPS30 had no fresh data for over {_STALE_AFTER_SECONDS}s -- it may have "
                    "silently reset to Idle-Mode; re-issued start_measurement(), retrying warm-up"
                )
            raise SensorReadError("SPS30 did not return fresh data this cycle")

        try:
            mc_1p0, mc_2p5, mc_4p0, mc_10p0, *_rest = device.read_measurement_values_uint16()
        except OSError as exc:
            raise SensorReadError(f"could not read SPS30 measurement values: {exc}") from exc

        _last_ready_at = now
        _cached_values = (mc_1p0, mc_2p5, mc_4p0, mc_10p0)
        _cached_at = now
        return _cached_values


class SPS30PM1_0Sensor(Sensor):
    sensor_type = "sps30_pm1_0_ug_m3"
    unit = "ug_m3"

    def read(self) -> float:
        return round(_read_all()[0], 2)


class SPS30PM2_5Sensor(Sensor):
    sensor_type = "sps30_pm2_5_ug_m3"
    unit = "ug_m3"

    def read(self) -> float:
        return round(_read_all()[1], 2)


class SPS30PM4_0Sensor(Sensor):
    sensor_type = "sps30_pm4_0_ug_m3"
    unit = "ug_m3"

    def read(self) -> float:
        return round(_read_all()[2], 2)


class SPS30PM10Sensor(Sensor):
    sensor_type = "sps30_pm10_ug_m3"
    unit = "ug_m3"

    def read(self) -> float:
        return round(_read_all()[3], 2)
