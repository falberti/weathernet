import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

# Standard sysfs path for the SoC thermal zone on Raspberry Pi OS (and
# most Linux SBCs). Reports millidegrees Celsius as plain text.
THERMAL_ZONE_PATH = Path("/sys/class/thermal/thermal_zone0/temp")

# `vcgencmd get_throttled` prints a single hex bitmask, e.g.
# "throttled=0x50005". Bit layout per the Raspberry Pi firmware docs:
# bit 0 = under-voltage right now, bit 16 = under-voltage has occurred
# since boot (sticky -- stays set until the next reboot). The other
# bits (frequency capping, thermal throttling/limit) aren't collected
# here; this is specifically about the failure mode that was actually
# hit -- a sensor's fan inrush current browning out the Pi's own 5V
# rail and knocking it off I2C -- not a general vcgencmd wrapper.
_THROTTLED_RE = re.compile(r"throttled=0x([0-9a-fA-F]+)")
_UNDERVOLTAGE_NOW_BIT = 0x1
_UNDERVOLTAGE_OCCURRED_BIT = 0x10000

_process_start_monotonic = time.monotonic()
_warned_missing_thermal_zone = False
_warned_missing_vcgencmd = False


@dataclass(frozen=True)
class HealthSnapshot:
    cpu_temp_c: Optional[float]
    cpu_percent: float
    mem_percent: float
    disk_percent: float
    uptime_seconds: int
    undervoltage_now: Optional[bool]
    undervoltage_occurred: Optional[bool]


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


def _read_undervoltage_status() -> tuple:
    """(undervoltage_now, undervoltage_occurred) from `vcgencmd
    get_throttled`. (None, None) on any non-Pi host, or if `vcgencmd`
    itself is missing or fails -- this is a diagnostic nicety, not
    something that should ever be able to break a reporting cycle.
    """
    global _warned_missing_vcgencmd
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        if not _warned_missing_vcgencmd:
            logger.warning(
                "Could not run 'vcgencmd get_throttled' (not a Pi / vcgencmd not "
                "installed?); reporting null for undervoltage_now/undervoltage_occurred "
                "from now on"
            )
            _warned_missing_vcgencmd = True
        return None, None

    match = _THROTTLED_RE.search(result.stdout)
    if not match:
        return None, None
    raw = int(match.group(1), 16)
    return bool(raw & _UNDERVOLTAGE_NOW_BIT), bool(raw & _UNDERVOLTAGE_OCCURRED_BIT)


def collect() -> HealthSnapshot:
    """Collect infrastructure-level health metrics for this cycle.

    This is genuinely functional (not mocked): it's what an operator
    would actually use to notice a probe in distress.
    """
    undervoltage_now, undervoltage_occurred = _read_undervoltage_status()
    return HealthSnapshot(
        cpu_temp_c=_read_cpu_temperature(),
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=psutil.virtual_memory().percent,
        disk_percent=psutil.disk_usage("/").percent,
        uptime_seconds=int(time.monotonic() - _process_start_monotonic),
        undervoltage_now=undervoltage_now,
        undervoltage_occurred=undervoltage_occurred,
    )
