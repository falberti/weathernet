import struct
import time

import pytest

from weathernet_probe.sensors import sps30
from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.sps30 import (
    SPS30PM1_0Sensor,
    SPS30PM2_5Sensor,
    SPS30PM4_0Sensor,
    SPS30PM10Sensor,
)


class _FakeSps30Device:
    """Stands in for sensirion_uart_sps30's Sps30Device -- exposes just
    the three methods _read_all()/_restart_measurement() actually call.

    Unlike the I2C variant of this chip, there's no separate
    read_data_ready_flag() on UART -- read_measurement_values_uint16()
    itself raises struct.error when there's nothing new (confirmed
    against the real sensirion_driver_adapters package, see sps30.py's
    docstring on that except clause), so `ready=False` here raises
    instead of returning a sentinel.
    """

    def __init__(self):
        self.ready = True
        self.values = (1.0, 2.0, 3.0, 4.0, 0, 0, 0, 0, 0, 0)
        self.start_calls = 0
        self.stop_calls = 0
        self.wake_up_calls = 0
        # Simulates the real Sleep-Mode scenario: the first Wake-up
        # gets no response (interface still off when it's sent), the
        # second succeeds once the interface has activated.
        self.wake_up_first_call_fails = False

    def read_measurement_values_uint16(self):
        if not self.ready:
            raise struct.error("unpack_from requires a buffer of at least 2 bytes")
        return self.values

    def start_measurement(self, output_format):
        self.start_calls += 1

    def stop_measurement(self):
        self.stop_calls += 1

    def wake_up(self):
        self.wake_up_calls += 1
        if self.wake_up_calls == 1 and self.wake_up_first_call_fails:
            raise OSError("simulated: no response (device was asleep)")


class _DummyShdlcSerialPort:
    def __init__(self, **kwargs):
        pass


class _DummyShdlcChannel:
    def __init__(self, port):
        pass


@pytest.fixture(autouse=True)
def _reset_sps30_module_state(monkeypatch):
    # Module-level singleton state (see sps30.py's docstring on _device)
    # -- reset around every test so they don't leak into each other;
    # in the real process this state is naturally fresh per-run.
    monkeypatch.setattr(sps30, "_device", None)
    monkeypatch.setattr(sps30, "_measurement_started_at", None)
    monkeypatch.setattr(sps30, "_last_ready_at", None)
    monkeypatch.setattr(sps30, "_cached_values", None)
    monkeypatch.setattr(sps30, "_cached_at", 0.0)


def _install_fake_device(monkeypatch, started_at, last_ready_at=None):
    fake = _FakeSps30Device()
    monkeypatch.setattr(sps30, "_device", fake)
    monkeypatch.setattr(sps30, "_measurement_started_at", started_at)
    monkeypatch.setattr(sps30, "_last_ready_at", started_at if last_ready_at is None else last_ready_at)
    # _get_device() only creates a real device when _device is None
    # (already false here) but still gates on Sps30Device (the class,
    # not the instance) being importable -- stub it too so these tests
    # don't depend on sensirion-uart-sps30 actually being installed.
    monkeypatch.setattr(sps30, "Sps30Device", object)
    return fake


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (SPS30PM1_0Sensor, "sps30_pm1_0_ug_m3"),
        (SPS30PM2_5Sensor, "sps30_pm2_5_ug_m3"),
        (SPS30PM4_0Sensor, "sps30_pm4_0_ug_m3"),
        (SPS30PM10Sensor, "sps30_pm10_ug_m3"),
    ],
)
def test_sps30_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `sensirion_uart_sps30`
    # package -- only read() does. This is what lets sensors/registry.py
    # import (and every other test in this suite run) on a dev machine
    # with no serial port and none of the sensirion_* packages installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


def test_get_device_wakes_up_twice_before_first_init(monkeypatch):
    # Real-world scenario this exists for: the chip was found sitting
    # in Sleep-Mode (fan audibly off, UART interface disabled per the
    # datasheet) with no wiring change at all -- the documented
    # software-only recovery is to send Wake-up twice in a row.
    fake = _FakeSps30Device()
    fake.wake_up_first_call_fails = True
    monkeypatch.setattr(sps30, "Sps30Device", lambda channel: fake)
    monkeypatch.setattr(sps30, "ShdlcSerialPort", _DummyShdlcSerialPort)
    monkeypatch.setattr(sps30, "ShdlcChannel", _DummyShdlcChannel)

    device = sps30._get_device()

    assert device is fake
    assert fake.wake_up_calls == 2  # first call got no response, second succeeded
    assert fake.stop_calls == 1
    assert fake.start_calls == 1


def test_get_device_wake_up_is_harmless_when_already_awake(monkeypatch):
    # Sending Wake-up to a device that was never asleep must not break
    # normal initialization -- both calls just succeed as no-ops.
    fake = _FakeSps30Device()
    monkeypatch.setattr(sps30, "Sps30Device", lambda channel: fake)
    monkeypatch.setattr(sps30, "ShdlcSerialPort", _DummyShdlcSerialPort)
    monkeypatch.setattr(sps30, "ShdlcChannel", _DummyShdlcChannel)

    device = sps30._get_device()

    assert device is fake
    assert fake.wake_up_calls == 2
    assert fake.start_calls == 1


@pytest.mark.parametrize(
    "sensor_cls", [SPS30PM1_0Sensor, SPS30PM2_5Sensor, SPS30PM4_0Sensor, SPS30PM10Sensor]
)
def test_sps30_read_fails_clearly_without_the_library_installed(sensor_cls, monkeypatch):
    # sensirion_uart_sps30 is pure Python and may actually be installed
    # here -- simulate its absence explicitly rather than relying on
    # the test environment happening to lack it.
    monkeypatch.setattr("weathernet_probe.sensors.sps30.Sps30Device", None)
    with pytest.raises(SensorReadError, match="sensirion-uart-sps30.*not installed"):
        sensor_cls().read()


def test_still_warming_up_raises_before_checking_ready(monkeypatch):
    now = time.monotonic()
    fake = _install_fake_device(monkeypatch, started_at=now)
    with pytest.raises(SensorReadError, match="still warming up"):
        sps30._read_all()
    assert fake.start_calls == 0  # never even asked the device anything yet


def test_read_all_returns_values_once_warmed_up_and_ready(monkeypatch):
    now = time.monotonic()
    _install_fake_device(monkeypatch, started_at=now - sps30._WARMUP_SECONDS - 1)
    assert sps30._read_all() == (1.0, 2.0, 3.0, 4.0)


def test_second_read_within_cache_ttl_reuses_cached_values(monkeypatch):
    now = time.monotonic()
    fake = _install_fake_device(monkeypatch, started_at=now - sps30._WARMUP_SECONDS - 1)
    first = sps30._read_all()
    fake.values = (99.0, 99.0, 99.0, 99.0, 0, 0, 0, 0, 0, 0)  # would differ if actually re-read
    second = sps30._read_all()
    assert first == second == (1.0, 2.0, 3.0, 4.0)


def test_not_ready_within_stale_threshold_raises_without_restarting(monkeypatch):
    # A single missed cycle shortly after startup/the last good read is
    # normal (the chip may just not have produced a fresh sample yet) --
    # must not trigger a measurement-mode restart every time.
    now = time.monotonic()
    fake = _install_fake_device(monkeypatch, started_at=now - sps30._WARMUP_SECONDS - 1)
    fake.ready = False
    with pytest.raises(SensorReadError, match="did not return fresh data this cycle"):
        sps30._read_all()
    assert fake.start_calls == 0
    assert fake.stop_calls == 0


def test_not_ready_past_stale_threshold_restarts_measurement_mode(monkeypatch):
    # Simulates the real failure this recovery exists for: the chip
    # silently reverted to Idle-Mode (e.g. after an internal reset) and
    # would otherwise report "not ready" forever, since this driver
    # only calls start_measurement() once by default.
    now = time.monotonic()
    stale_start = now - sps30._STALE_AFTER_SECONDS - sps30._WARMUP_SECONDS - 5
    fake = _install_fake_device(monkeypatch, started_at=stale_start, last_ready_at=stale_start)
    fake.ready = False

    with pytest.raises(SensorReadError, match="silently reset to Idle-Mode"):
        sps30._read_all()

    assert fake.stop_calls == 1
    assert fake.start_calls == 1
    # The warmup/staleness clocks must be reset so the very next read
    # goes through a fresh warmup window rather than immediately
    # re-triggering another "restart" on top of the one just done.
    assert sps30._measurement_started_at == pytest.approx(time.monotonic(), abs=1.0)
