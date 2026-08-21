"""Real SPS30 driver (particulate matter mass concentration) over UART.

Uses Sensirion's own maintained `sensirion-uart-sps30` package (plus its
`sensirion-shdlc-driver` / `sensirion-driver-adapters` dependencies)
rather than a hand-rolled SHDLC protocol implementation -- same
reasoning as bme680.py.

UART instead of I2C (this project's original choice) specifically
because Sensirion's own datasheet recommends UART for its "intrinsic
robustness against electromagnetic interference" on longer/breadboard
cabling -- confirmed necessary in practice: on I2C, this sensor's own
fan motor reliably knocked it off the shared bus on every
start_measurement(), even after ruling out every power-supply
explanation (a proper DIN-rail PSU, direct point-to-point VDD *and*
GND wiring bypassing the breadboard entirely -- see the README's SPS30
wiring section for the full story). UART uses the Pi's dedicated
serial pins instead of the shared I2C bus, so it isn't exposed to
whatever was coupling noise onto SDA/SCL.

Imports of the `sensirion_shdlc_driver` / `sensirion_uart_sps30` /
`sensirion_driver_adapters` packages are guarded so this module, and
therefore the whole sensor registry, still imports cleanly without
them installed or with no serial port at all. Only actually *using* a
sensor here without the packages installed raises SensorReadError, not
importing this file.
"""
import struct
import threading
import time

from .base import Sensor, SensorReadError

try:
    from sensirion_driver_adapters.shdlc_adapter.shdlc_channel import ShdlcChannel
    from sensirion_shdlc_driver import ShdlcSerialPort
    from sensirion_shdlc_driver.errors import ShdlcError
    from sensirion_uart_sps30.commands import OutputFormat
    from sensirion_uart_sps30.device import Sps30Device
except ImportError:
    Sps30Device = None

# Requires the Pi's full PL011 UART on these pins, not the variable-
# clock "mini UART" -- see the README's SPS30 wiring section for the
# `dtoverlay=disable-bt` config.txt change this depends on (the PL011
# is routed to onboard Bluetooth by default on a Pi 3).
_SERIAL_PORT = "/dev/ttyAMA0"
_BAUDRATE = 115200

# One physical sensor, shared across the four Sensor subclasses below --
# same rationale as bme680.py's shared `_device`: a second
# ShdlcSerialPort/Sps30Device would reopen the serial port and try to
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
# Idle-Mode (e.g. after an internal reset) and re-issue
# start_measurement() to recover automatically. Same reasoning as the
# I2C version of this driver had; kept because the underlying chip
# firmware's Idle/Measurement state machine doesn't care which
# transport is talking to it.
_STALE_AFTER_SECONDS = 30

# The chip only produces a new sample about once a second (in
# continuous Measurement-Mode); caching a read for a couple of seconds
# means the four PM Sensor subclasses below share one serial round trip
# per reporting cycle instead of four.
_CACHE_TTL_SECONDS = 2
_cached_values = None
_cached_at = 0.0


def _get_device():
    global _device, _measurement_started_at, _last_ready_at
    if Sps30Device is None:
        raise SensorReadError(
            "the 'sensirion-uart-sps30' package (and its sensirion-shdlc-driver / "
            "sensirion-driver-adapters dependencies) is not installed -- add it to "
            "probe/requirements.txt and reinstall (see the top-level README's SPS30 section)"
        )
    with _device_lock:
        if _device is None:
            try:
                port = ShdlcSerialPort(port=_SERIAL_PORT, baudrate=_BAUDRATE, additional_response_time=0.02)
                channel = ShdlcChannel(port)
                device = Sps30Device(channel)
                # The chip may be sitting in Sleep-Mode -- this driver
                # never sends the Sleep command (0x10) itself, but the
                # device can end up there anyway (e.g. a prior process,
                # or just an unknown state across a probe restart), and
                # Sleep-Mode disables the UART interface entirely, so
                # every command below would otherwise time out with no
                # response at all (datasheet 4.1 "Sleep"). Per 5.3.5
                # "Wake-up": sending the Wake-up command twice in a row
                # is the documented software-only recovery -- the first
                # call is expected to get no response (the interface is
                # still off when it's sent) but its transmission alone
                # activates the interface; the second succeeds
                # normally. Harmless if the device was already awake.
                for _ in range(2):
                    try:
                        device.wake_up()
                    except (OSError, ShdlcError):
                        pass
                try:
                    device.stop_measurement()
                except (OSError, ShdlcError):
                    pass  # already idle -- fine, start_measurement below is what matters
                device.start_measurement(OutputFormat.OUTPUT_FORMAT_UINT16)
            except (OSError, ShdlcError) as exc:
                raise SensorReadError(f"could not initialize SPS30 over UART: {exc}") from exc
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
    except (OSError, ShdlcError):
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
            mc_1p0, mc_2p5, mc_4p0, mc_10p0, *_rest = device.read_measurement_values_uint16()
        except struct.error:
            # Confirmed empirically (not just inferred from the docs):
            # an empty SHDLC response frame -- the chip's way of saying
            # "no new measurement since your last read" -- unpacks into
            # exactly this exception, since the fixed-width struct
            # format expects a full payload and got zero bytes. There's
            # no separate ready-flag command on UART the way there is
            # on I2C (Sps30Device here has no read_data_ready_flag()).
            if (now - _last_ready_at) > _STALE_AFTER_SECONDS:
                try:
                    _restart_measurement(device)
                except (OSError, ShdlcError) as exc:
                    raise SensorReadError(f"could not restart SPS30 measurement mode: {exc}") from exc
                raise SensorReadError(
                    f"SPS30 had no fresh data for over {_STALE_AFTER_SECONDS}s -- it may have "
                    "silently reset to Idle-Mode; re-issued start_measurement(), retrying warm-up"
                )
            raise SensorReadError("SPS30 did not return fresh data this cycle")
        except (OSError, ShdlcError) as exc:
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
