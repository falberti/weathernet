#!/usr/bin/env bash
# One-shot setup for a WeatherNet probe -- one command, via zero-touch
# enrollment (PROJECT_SPEC.md Section 5.7). Run this on the probe
# device itself (e.g. the Raspberry Pi), from inside a clone of this
# repo. Get --token (and --fingerprint) from Django Admin: adding an
# "Enrollment token" there prints the exact command to run here.
#
# Usage: setup.sh --server <server-public-ip> --token <token> [--fingerprint <sha256>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROBE_DIR}/venv"
CONFIG_DIR="/etc/weathernet-probe"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

log() { echo "==> $*"; }

usage() {
  echo "Usage: $0 --server <server-public-ip> --token <token> [--fingerprint <sha256>]" >&2
}

SERVER=""
TOKEN=""
FINGERPRINT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --fingerprint) FINGERPRINT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${SERVER}" || -z "${TOKEN}" ]]; then
  usage
  exit 1
fi
if [[ -z "${FINGERPRINT}" ]]; then
  log "WARNING: no --fingerprint given -- enroll.py will proceed without TLS pinning."
fi

# --- 1. Python, venv, and system dependencies ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi
log "Using $(python3 --version)"

if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtualenv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# psutil almost always has no prebuilt wheel for a Pi's exact
# Python-version/arch combination and falls back to compiling from
# source -- which fails without Python's headers and a compiler. Install
# both before pip ever gets a chance to need them, rather than letting
# the build fail first.
if command -v apt-get >/dev/null 2>&1; then
  log "Installing build dependencies (needed to compile psutil's C extension)"
  sudo apt-get update && sudo apt-get install -y python3-dev build-essential
fi

log "Installing probe dependencies"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${PROBE_DIR}/requirements.txt"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required (used to generate this probe's mTLS key/CSR) and was not found." >&2
  exit 1
fi

if ! command -v wg >/dev/null 2>&1; then
  log "Installing wireguard-tools"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y wireguard-tools
  else
    echo "wireguard-tools ('wg') is not installed and apt-get is unavailable." >&2
    echo "Install it manually, then re-run this script." >&2
    exit 1
  fi
fi

# --- 2. Enroll ---
# Runs as root: it writes to /etc/weathernet-probe and /etc/wireguard,
# both of which wg-quick@wg0 (which always runs as root) also needs.
# Ownership of the weathernet-probe half is handed to RUN_USER right
# after, since that service runs unprivileged (see step 3).
log "Redeeming enrollment token"
ENROLL_ARGS=(--server "${SERVER}" --token "${TOKEN}")
if [[ -n "${FINGERPRINT}" ]]; then
  ENROLL_ARGS+=(--fingerprint "${FINGERPRINT}")
fi
sudo "${VENV_DIR}/bin/python3" "${SCRIPT_DIR}/enroll.py" "${ENROLL_ARGS[@]}"

log "Setting ownership so weathernet-probe (running as ${RUN_USER}) can read its config/certs"
sudo mkdir -p /var/lib/weathernet-probe /var/log/weathernet-probe
sudo chown -R "${RUN_USER}:${RUN_GROUP}" "${CONFIG_DIR}" /var/lib/weathernet-probe /var/log/weathernet-probe

PROBE_ID="$(grep '^probe_id:' "${CONFIG_DIR}/probe.yaml" | sed 's/probe_id: *//' | tr -d '"')"

# --- 3. systemd unit for the probe daemon ---
SERVICE_PATH=/etc/systemd/system/weathernet-probe.service
log "Installing systemd unit at ${SERVICE_PATH}"
sed -e "s|__INSTALL_DIR__|${PROBE_DIR}|g" \
    -e "s|__VENV_DIR__|${VENV_DIR}|g" \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    -e "s|__RUN_GROUP__|${RUN_GROUP}|g" \
    "${PROBE_DIR}/config/weathernet-probe.service" | sudo tee "${SERVICE_PATH}" >/dev/null
sudo systemctl daemon-reload

# --- 4. Enable + start both services ---
log "Enabling and starting weathernet-probe and the WireGuard tunnel"
sudo systemctl enable --now weathernet-probe
sudo systemctl enable --now wg-quick@wg0

# --- 5. Summary ---
cat <<SUMMARY

WeatherNet probe ${PROBE_ID} is enrolled and running as user '${RUN_USER}'.

  Tail logs:    journalctl -u weathernet-probe -f
  Spool status: wc -l /var/lib/weathernet-probe/spool.jsonl 2>/dev/null || echo "(no spool file yet -- good, nothing backed up)"
  WireGuard:    sudo wg show wg0

You should see this probe appear in Grafana within its report interval.
It should also be SSH-reachable from the server at its WireGuard tunnel
IP within about a minute -- the server's peer-sync timer picks it up
automatically, nothing else to do.
SUMMARY
