#!/usr/bin/env bash
# One-shot setup for a WeatherNet probe. Run this on the probe device
# itself (e.g. the Raspberry Pi), from inside a clone of this repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROBE_DIR}/venv"
CONFIG_DIR="/etc/weathernet-probe"
CERTS_DIR="${CONFIG_DIR}/certs"
CONFIG_PATH="${CONFIG_DIR}/probe.yaml"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

log() { echo "==> $*"; }

# --- 1. Python + venv ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi
log "Using $(python3 --version)"

if [[ ! -d "${VENV_DIR}" ]]; then
  log "Creating virtualenv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

log "Installing probe dependencies"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${PROBE_DIR}/requirements.txt"

# --- 2. Identity ---
DEFAULT_PROBE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
read -rp "Probe UUID [${DEFAULT_PROBE_ID}]: " PROBE_ID
PROBE_ID="${PROBE_ID:-${DEFAULT_PROBE_ID}}"

echo "Hardware type (must match probes.models.Probe.HardwareType on the server):"
select HARDWARE_TYPE in raspberry_pi_3 raspberry_pi_4 raspberry_pi_5 generic_linux; do
  [[ -n "${HARDWARE_TYPE:-}" ]] && break
  echo "Please choose 1-4."
done

read -rp "Server public IP: " SERVER_PUBLIC_IP
if [[ -z "${SERVER_PUBLIC_IP}" ]]; then
  echo "Server public IP is required." >&2
  exit 1
fi

# --- 3. probe.yaml ---
log "Writing ${CONFIG_PATH}"
sudo mkdir -p "${CONFIG_DIR}"
sed -e "s|^probe_id:.*|probe_id: \"${PROBE_ID}\"|" \
    -e "s|^hardware_type:.*|hardware_type: ${HARDWARE_TYPE}|" \
    -e "s|^server_url:.*|server_url: \"https://${SERVER_PUBLIC_IP}/api/v1/ingest\"|" \
    "${PROBE_DIR}/config/probe.example.yaml" | sudo tee "${CONFIG_PATH}" >/dev/null

# --- 4. Certificates ---
MISSING=()
for f in client.cert.pem client.key.pem ca.cert.pem; do
  [[ -f "${CERTS_DIR}/${f}" ]] || MISSING+=("${CERTS_DIR}/${f}")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo
  echo "Missing certificate file(s):"
  for f in "${MISSING[@]}"; do echo "  ${f}"; done
  echo
  echo "On the server, generate this probe's certificate with:"
  echo "  server/pki/generate-probe-cert.sh ${PROBE_ID}"
  echo "then copy the printed files into ${CERTS_DIR}/ (e.g. via scp) and re-run this script."
  exit 1
fi
log "Found client certificate, key, and CA cert in ${CERTS_DIR}"

# --- 5. systemd unit ---
SERVICE_PATH=/etc/systemd/system/weathernet-probe.service
log "Installing systemd unit at ${SERVICE_PATH}"
sed -e "s|__INSTALL_DIR__|${PROBE_DIR}|g" \
    -e "s|__VENV_DIR__|${VENV_DIR}|g" \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    -e "s|__RUN_GROUP__|${RUN_GROUP}|g" \
    "${PROBE_DIR}/config/weathernet-probe.service" | sudo tee "${SERVICE_PATH}" >/dev/null

sudo mkdir -p /var/lib/weathernet-probe /var/log/weathernet-probe
sudo chown -R "${RUN_USER}:${RUN_GROUP}" /var/lib/weathernet-probe /var/log/weathernet-probe "${CONFIG_DIR}"
sudo chmod 700 "${CERTS_DIR}"
sudo chmod 600 "${CERTS_DIR}"/*.pem

sudo systemctl daemon-reload

# --- 6. Enable + start ---
log "Enabling and starting weathernet-probe"
sudo systemctl enable --now weathernet-probe

# --- 7. WireGuard remote access ---
# A second, independent channel from the mTLS telemetry path above --
# lets the operator SSH into this probe (via the server as a bastion)
# for troubleshooting even behind a home NAT. See PROJECT_SPEC.md
# Section 6.7. This is OS-level config, not part of the Python app.
log "Setting up WireGuard remote access"

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

sudo mkdir -p /etc/wireguard
if [[ ! -f /etc/wireguard/privatekey ]]; then
  sudo bash -c 'umask 077; wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey'
fi
PROBE_WG_PUBLIC_KEY="$(sudo cat /etc/wireguard/publickey)"

echo
echo "This probe's WireGuard public key -- you'll paste this into Django"
echo "Admin in a moment:"
echo "  ${PROBE_WG_PUBLIC_KEY}"
echo

read -rp "Server's WireGuard public key (from server setup output): " SERVER_WG_PUBLIC_KEY
if [[ -z "${SERVER_WG_PUBLIC_KEY}" ]]; then
  echo "Server WireGuard public key is required." >&2
  exit 1
fi
read -rp "Server's WireGuard tunnel IP [10.10.0.1]: " SERVER_WG_TUNNEL_IP
SERVER_WG_TUNNEL_IP="${SERVER_WG_TUNNEL_IP:-10.10.0.1}"
read -rp "Server's WireGuard UDP port [51820]: " SERVER_WG_PORT
SERVER_WG_PORT="${SERVER_WG_PORT:-51820}"
read -rp "This probe's assigned WireGuard tunnel IP (must match what you enter in Django Admin): " PROBE_TUNNEL_IP
if [[ -z "${PROBE_TUNNEL_IP}" ]]; then
  echo "A tunnel IP is required." >&2
  exit 1
fi

sed -e "s|\${PROBE_WIREGUARD_PRIVATE_KEY}|$(sudo cat /etc/wireguard/privatekey)|" \
    -e "s|\${PROBE_TUNNEL_IP}|${PROBE_TUNNEL_IP}|" \
    -e "s|\${SERVER_WIREGUARD_PUBLIC_KEY}|${SERVER_WG_PUBLIC_KEY}|" \
    -e "s|\${SERVER_PUBLIC_IP}|${SERVER_PUBLIC_IP}|" \
    -e "s|\${SERVER_WIREGUARD_PORT}|${SERVER_WG_PORT}|" \
    -e "s|\${SERVER_TUNNEL_IP}|${SERVER_WG_TUNNEL_IP}|" \
    "${PROBE_DIR}/config/wg0.conf.template" | sudo tee /etc/wireguard/wg0.conf >/dev/null
sudo chmod 600 /etc/wireguard/wg0.conf

sudo systemctl enable --now wg-quick@wg0

# Defense in depth: the tunnel is already only reachable by the
# authenticated server peer, but scope the local firewall down anyway.
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow in on wg0 to any port 22 proto tcp
  log "Configured ufw to allow SSH on wg0 only"
else
  log "ufw not found -- skipping the wg0-only SSH firewall rule (not required, just defense in depth)"
fi

# --- 8. Summary ---
cat <<SUMMARY

WeatherNet probe ${PROBE_ID} is running as user '${RUN_USER}'.

  Tail logs:    journalctl -u weathernet-probe -f
  Spool status: wc -l /var/lib/weathernet-probe/spool.jsonl 2>/dev/null || echo "(no spool file yet -- good, nothing backed up)"
  WireGuard:    sudo wg show wg0

Make sure a Probe row with id=${PROBE_ID} exists (and is active) in
Django Admin on the server, or ingestion will be rejected with 404/403.

To finish WireGuard remote access setup: on the server, edit that same
Probe row in Django Admin and set:
  wireguard_public_key = ${PROBE_WG_PUBLIC_KEY}
  wireguard_tunnel_ip  = ${PROBE_TUNNEL_IP}
then run wireguard/sync-peers.sh on the server host so it picks up this
probe as a peer.
SUMMARY
