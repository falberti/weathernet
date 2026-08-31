#!/usr/bin/env bash
# Alerts via Telegram when a sensor hasn't reported fresh data in too
# long (telemetry app). Run every 15 minutes by systemd (see
# weathernet-sensor-health.timer) -- safe to run more often than that,
# each run is a fresh, independent check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SERVER_DIR}"

docker compose exec -T django python manage.py check_sensor_health
