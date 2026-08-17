#!/usr/bin/env bash
# Regenerate /etc/wireguard/wg0.conf from the current probe registry
# and apply it: live, without dropping existing tunnels, if wg0 is
# already up; brought up for the first time otherwise. Run this on the
# server VM host itself (not inside a container -- WireGuard runs
# outside Docker, see PROJECT_SPEC.md Section 5.1) after registering or
# editing a probe's WireGuard fields in Django Admin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PRIVATE_KEY_FILE="${SCRIPT_DIR}/server_private.key"
WG_CONF=/etc/wireguard/wg0.conf

log() { echo "==> $*"; }

cd "${SERVER_DIR}"

if [[ ! -f "${PRIVATE_KEY_FILE}" ]]; then
  echo "No server WireGuard keypair found. Run wireguard/generate-server-keys.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

SERVER_ADDRESS_CIDR="$(python3 -c "
import ipaddress, os
net = ipaddress.ip_network(os.environ['WIREGUARD_SUBNET'])
print(f'{next(net.hosts())}/{net.prefixlen}')
")"

HEADER_TMP="$(mktemp)"
PEERS_TMP="$(mktemp)"
trap 'rm -f "${HEADER_TMP}" "${PEERS_TMP}"' EXIT

log "Rendering the [Interface] block"
sed -e "s|\${WIREGUARD_SERVER_PRIVATE_KEY}|$(cat "${PRIVATE_KEY_FILE}")|" \
    -e "s|\${WIREGUARD_SERVER_ADDRESS_CIDR}|${SERVER_ADDRESS_CIDR}|" \
    -e "s|\${WIREGUARD_LISTEN_PORT}|${WIREGUARD_LISTEN_PORT}|" \
    wireguard/wg0.conf.header > "${HEADER_TMP}"

log "Fetching the peer list from the probe registry"
docker compose exec -T django python manage.py generate_wireguard_peers > "${PEERS_TMP}"

log "Writing ${WG_CONF}"
cat "${HEADER_TMP}" "${PEERS_TMP}" | sudo tee "${WG_CONF}" >/dev/null
sudo chmod 600 "${WG_CONF}"

if systemctl is-active --quiet wg-quick@wg0; then
  log "wg0 is already up -- applying the new peer list without dropping existing tunnels"
  sudo wg syncconf wg0 <(sudo wg-quick strip "${WG_CONF}")
else
  log "Bringing up wg0 for the first time"
  sudo systemctl enable --now wg-quick@wg0
fi

log "Current peers:"
sudo wg show wg0
