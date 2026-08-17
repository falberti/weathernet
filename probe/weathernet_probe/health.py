import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

# Standard sysfs path for the SoC thermal zone on Raspberry Pi OS (and
# most Linux SBCs). Reports millidegrees Celsius as plain text.
THERMAL_ZONE_PATH = Path("/sys/class/thermal/thermal_zone0/temp")

_process_start_monotonic = time.monotonic()
_warned_missing_thermal_zone = False


@dataclass(frozen=True)
class HealthSnapshot:
    cpu_temp_c: Optional[float]
    cpu_percent: float
    mem_percent: float
    disk_percent: float
    uptime_seconds: int


def _read_cpu_temperature() -> Optional[float]:
    global _warned_missing_thermal_zone
    try:
        raw = THERMAL_ZONE_PATH.read_text().strip()
        return round(int(raw) / 1000.0, 1)
    except (FileNotFoundError, ValueError, OSError):
        if not _warned_missing_thermal_zone:
            logger.warning(
                "Could not read CPU temperature from %s (not a Pi / no thermal zone?); "
                "reporting null for cpu_temp_c from now on",
                THERMAL_ZONE_PATH,
            )
            _warned_missing_thermal_zone = True
        return None


def collect() -> HealthSnapshot:
    """Collect infrastructure-level health metrics for this cycle.

    This is genuinely functional (not mocked): it's what an operator
    would actually use to notice a probe in distress.
    """
    return HealthSnapshot(
        cpu_temp_c=_read_cpu_temperature(),
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=psutil.virtual_memory().percent,
        disk_percent=psutil.disk_usage("/").percent,
        uptime_seconds=int(time.monotonic() - _process_start_monotonic),
    )
