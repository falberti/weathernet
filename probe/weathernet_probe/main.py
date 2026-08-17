import argparse
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import health
from .config import ProbeConfig
from .sensors.base import SensorReadError
from .sensors.registry import build_sensor
from .spool import Spool
from .transport import Transport, TransportError

logger = logging.getLogger("weathernet_probe")

DEFAULT_CONFIG_PATH = "/etc/weathernet-probe/probe.yaml"


def _setup_logging(log_path):
    handlers = [logging.StreamHandler()]
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _read_sensors(sensors) -> list:
    readings = []
    for name, sensor in sensors.items():
        try:
            value = sensor.read()
        except SensorReadError as exc:
            logger.warning("Sensor '%s' failed to read: %s", name, exc)
            continue
        except Exception:
            logger.exception("Unexpected error reading sensor '%s'", name)
            continue
        readings.append({"sensor_type": sensor.sensor_type, "value": value})
    return readings


def _build_payload(config: ProbeConfig, sensors) -> dict:
    snapshot = health.collect()
    return {
        "probe_id": config.probe_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readings": _read_sensors(sensors),
        "health": {
            "cpu_temp_c": snapshot.cpu_temp_c,
            "cpu_percent": snapshot.cpu_percent,
            "mem_percent": snapshot.mem_percent,
            "disk_percent": snapshot.disk_percent,
            "uptime_seconds": snapshot.uptime_seconds,
        },
    }


def _flush_and_send(transport: Transport, spool: Spool, payload: dict) -> None:
    """Send everything spooled from prior failed cycles, oldest first,
    then the current reading. On the first failure, whatever is left
    (including the reading that just failed) goes back into the spool
    and we stop for this cycle -- retried next time.
    """
    backlog = spool.pop_all()
    pending = backlog + [payload]

    for index, item in enumerate(pending):
        try:
            transport.send(item)
        except TransportError as exc:
            remaining = pending[index:]
            logger.warning(
                "Send failed (%s); spooling %d reading(s) for retry", exc, len(remaining)
            )
            spool.requeue_front(remaining)
            return

    if backlog:
        logger.info("Flushed %d spooled reading(s)", len(backlog))


def _run_cycle(config: ProbeConfig, sensors, transport: Transport, spool: Spool) -> None:
    try:
        payload = _build_payload(config, sensors)
        _flush_and_send(transport, spool, payload)
    except Exception:
        logger.exception("Unhandled error in reporting cycle; will retry next cycle")


def run(config_path: str) -> None:
    config = ProbeConfig.load(config_path)
    _setup_logging(config.log_path)
    logger.info("Starting WeatherNet probe %s (%s)", config.probe_id, config.hardware_type)

    sensors = {name: build_sensor(name) for name in config.sensors}
    transport = Transport(
        server_url=config.server_url,
        client_cert_path=config.client_cert_path,
        client_key_path=config.client_key_path,
        ca_cert_path=config.ca_cert_path,
    )
    spool = Spool(config.spool_path, config.spool_max_entries)

    while True:
        _run_cycle(config, sensors, transport, spool)
        time.sleep(config.report_interval_seconds)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="WeatherNet probe daemon")
    parser.add_argument(
        "--config",
        default=os.environ.get("WEATHERNET_PROBE_CONFIG", DEFAULT_CONFIG_PATH),
        help="Path to probe.yaml",
    )
    args = parser.parse_args(argv)
    run(args.config)


if __name__ == "__main__":
    main()
