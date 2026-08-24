import pytest

from weathernet_probe.sensors import bmp280
from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.bmp280 import (
    BMP280PressureSensor,
    BMP280TemperatureSensor,
)


class _FakeBmp280Device:
    def __init__(self, temperature=21.0, pressure=1013.0):
        self.temperature = temperature
        self.pressure = pressure

    def get_temperature(self):
        return self.temperature

    def get_pressure(self):
        return self.pressure


@pytest.fixture(autouse=True)
def _reset_bmp280_module_state(monkeypatch):
    # Module-level singleton device (see bmp280.py's docstring on
    # `_device`) -- reset around every test so they don't leak into
    # each other; in the real process this is naturally fresh per-run.
    monkeypatch.setattr(bmp280, "_device", None)


def _install_fake_device(monkeypatch, **kwargs):
    fake = _FakeBmp280Device(**kwargs)
    monkeypatch.setattr(bmp280, "_device", fake)
    # _get_device() gates on `_BMP280` (the package) being importable
    # before ever looking at `_device` -- stub it too so this test
    # doesn't depend on the `bmp280` package actually being installed.
    monkeypatch.setattr(bmp280, "_BMP280", object)
    return fake


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (BMP280TemperatureSensor, "bmp280_temperature_c"),
        (BMP280PressureSensor, "bmp280_pressure_hpa"),
    ],
)
def test_bmp280_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `bmp280` package --
    # only read() does. This is what lets sensors/registry.py import
    # (and every other test in this suite run) on a dev machine with no
    # I2C bus and no `bmp280` package installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize("sensor_cls", [BMP280TemperatureSensor, BMP280PressureSensor])
def test_bmp280_read_fails_clearly_without_the_library_installed(sensor_cls, monkeypatch):
    # Unlike bme680/smbus2 (Linux-only C extensions, naturally absent on
    # a dev machine or most CI runners), `bmp280` is pure Python and may
    # actually be installed here -- simulate its absence explicitly
    # rather than relying on the test environment happening to lack it.
    monkeypatch.setattr("weathernet_probe.sensors.bmp280._BMP280", None)
    with pytest.raises(SensorReadError, match="bmp280.*not installed"):
        sensor_cls().read()


def test_plausible_temperature_and_pressure_pass_through(monkeypatch):
    _install_fake_device(monkeypatch, temperature=21.4, pressure=1012.4)
    assert BMP280TemperatureSensor().read() == 21.4
    assert BMP280PressureSensor().read() == 1012.4


@pytest.mark.parametrize("value", [-40.0, 85.0])
def test_temperature_at_the_chip_s_own_range_boundary_is_accepted(monkeypatch, value):
    _install_fake_device(monkeypatch, temperature=value)
    assert BMP280TemperatureSensor().read() == value


def test_temperature_far_outside_operating_range_is_rejected(monkeypatch):
    # Real incident this guards against: a corrupted I2C read once
    # compensated into a plausible-looking-but-impossible 180C, which
    # silently polluted Grafana for hours before anyone noticed.
    _install_fake_device(monkeypatch, temperature=180.0)
    with pytest.raises(SensorReadError, match=r"outside the chip's own operating range"):
        BMP280TemperatureSensor().read()


@pytest.mark.parametrize("value", [-41.0, 86.0])
def test_temperature_just_past_the_range_boundary_is_rejected(monkeypatch, value):
    _install_fake_device(monkeypatch, temperature=value)
    with pytest.raises(SensorReadError, match=r"outside the chip's own operating range"):
        BMP280TemperatureSensor().read()


@pytest.mark.parametrize("value", [299.0, 1101.0])
def test_pressure_outside_operating_range_is_rejected(monkeypatch, value):
    _install_fake_device(monkeypatch, pressure=value)
    with pytest.raises(SensorReadError, match=r"outside the chip's own operating range"):
        BMP280PressureSensor().read()
