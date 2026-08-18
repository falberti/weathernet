#!/usr/bin/env bash
# Sends the Telegram daily digest to every subscription with a probe
# close enough (subscriptions app). Run once a day by systemd (see
# weathernet-daily-digest.timer) -- not meant to run more than once
# per calendar day, since "yesterday" is computed relative to whenever
# this actually runs (see send_daily_digest.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SERVER_DIR}"

docker compose exec -T django python manage.py send_daily_digest
