import pytest

from weathernet_probe.sensors import bme680
from weathernet_probe.sensors.base import SensorReadError
from weathernet_probe.sensors.bme680 import (
    BME680GasResistanceSensor,
    BME680HumiditySensor,
    BME680PressureSensor,
    BME680TemperatureSensor,
)


class _FakeBme680Data:
    def __init__(self, temperature=21.0, humidity=45.0, pressure=1013.0,
                 gas_resistance=50000.0, heat_stable=True):
        self.temperature = temperature
        self.humidity = humidity
        self.pressure = pressure
        self.gas_resistance = gas_resistance
        self.heat_stable = heat_stable


class _FakeBme680Device:
    def __init__(self, **kwargs):
        self.data = _FakeBme680Data(**kwargs)

    def get_sensor_data(self):
        return True


@pytest.fixture(autouse=True)
def _reset_bme680_module_state(monkeypatch):
    # Module-level singleton device + cache (see bme680.py's docstring
    # on `_device`/caching) -- reset around every test so they don't
    # leak into each other; in the real process this is naturally fresh
    # per-run.
    monkeypatch.setattr(bme680, "_device", None)
    monkeypatch.setattr(bme680, "_cached_data", None)
    monkeypatch.setattr(bme680, "_cached_at", 0.0)


def _install_fake_device(monkeypatch, **kwargs):
    fake = _FakeBme680Device(**kwargs)
    monkeypatch.setattr(bme680, "_device", fake)
    # _get_device() gates on `_bme680_lib` (the package) being
    # importable before ever looking at `_device` -- stub it too so
    # this test doesn't depend on the `bme680` package actually being
    # installed.
    monkeypatch.setattr(bme680, "_bme680_lib", object)
    return fake


@pytest.mark.parametrize(
    "sensor_cls,expected_type",
    [
        (BME680TemperatureSensor, "temperature_c"),
        (BME680HumiditySensor, "humidity_pct"),
        (BME680PressureSensor, "pressure_hpa"),
        (BME680GasResistanceSensor, "gas_resistance_ohm"),
    ],
)
def test_bme680_sensor_instantiates_without_hardware(sensor_cls, expected_type):
    # Instantiating must never touch hardware or the `bme680` package --
    # only read() does. This is what lets sensors/registry.py import
    # (and every other test in this suite run) on a dev machine with no
    # I2C bus and no `bme680` package installed.
    sensor = sensor_cls()
    assert sensor.sensor_type == expected_type


@pytest.mark.parametrize(
    "sensor_cls",
    [BME680TemperatureSensor, BME680HumiditySensor, BME680PressureSensor, BME680GasResistanceSensor],
)
def test_bme680_read_fails_clearly_without_the_library_installed(sensor_cls):
    # On this test machine (and any CI runner) the `bme680` package
    # isn't installed -- read() must fail with a clear, actionable
    # SensorReadError (caught and logged per-sensor by main.py, not a
    # crash), not some opaque ImportError/AttributeError.
    with pytest.raises(SensorReadError, match="bme680.*not installed"):
        sensor_cls().read()


def test_plausible_readings_pass_through(monkeypatch):
    _install_fake_device(monkeypatch, temperature=21.4, humidity=48.2, pressure=1012.4,
                          gas_resistance=75000.0)
    assert BME680TemperatureSensor().read() == 21.4
    assert BME680HumiditySensor().read() == 48.2
    assert BME680PressureSensor().read() == 1012.4
    assert BME680GasResistanceSensor().read() == 75000.0


def test_temperature_far_outside_operating_range_is_rejected(monkeypatch):
    # Same class of incident bmp280.py's check guards against: a
    # corrupted I2C read compensating into a plausible-looking but
    # impossible value.
    _install_fake_device(monkeypatch, temperature=180.0)
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        BME680TemperatureSensor().read()


def test_pressure_outside_operating_range_is_rejected(monkeypatch):
    _install_fake_device(monkeypatch, pressure=50.0)
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        BME680PressureSensor().read()


@pytest.mark.parametrize("value", [-5.0, 150.0])
def test_humidity_outside_physical_range_is_rejected(monkeypatch, value):
    _install_fake_device(monkeypatch, humidity=value)
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        BME680HumiditySensor().read()


@pytest.mark.parametrize("value", [0.0, -100.0, 50_000_000.0])
def test_gas_resistance_outside_sanity_range_is_rejected(monkeypatch, value):
    _install_fake_device(monkeypatch, gas_resistance=value)
    with pytest.raises(SensorReadError, match=r"outside the sensor's plausible range"):
        BME680GasResistanceSensor().read()
