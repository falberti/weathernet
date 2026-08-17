#!/usr/bin/env bash
# One-shot setup for the WeatherNet server. Safe to re-run: steps that
# already succeeded (an existing .env, an existing CA, existing certs)
# are skipped rather than redone.
#
# Usage: setup.sh [server-public-ip]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SERVER_DIR}"

log() { echo "==> $*"; }

# --- 1. Docker + Compose plugin ---
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker before running this script (not automated on purpose)." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The 'docker compose' plugin is not available. Install it before running this script." >&2
  exit 1
fi

# --- 2. Server public IP ---
PUBLIC_IP="${1:-}"
if [[ -z "${PUBLIC_IP}" ]]; then
  read -rp "Server public IP (no DNS name is used for v1): " PUBLIC_IP
fi
if [[ -z "${PUBLIC_IP}" ]]; then
  echo "A public IP is required." >&2
  exit 1
fi

# --- 3. .env ---
random_secret() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }
random_password() { python3 -c "import secrets; print(secrets.token_urlsafe(18))"; }

if [[ ! -f .env ]]; then
  log "Creating .env from .env.example"
  cp .env.example .env

  DJANGO_SECRET_KEY="$(random_secret)"
  POSTGRES_PASSWORD="$(random_password)"
  GRAFANA_DB_PASSWORD="$(random_password)"
  GRAFANA_ADMIN_PASSWORD="$(random_password)"

  sed -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PASSWORD}|" \
      -e "s|^GRAFANA_DB_PASSWORD=.*|GRAFANA_DB_PASSWORD=${GRAFANA_DB_PASSWORD}|" \
      -e "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}|" \
      -e "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=${PUBLIC_IP}|" \
      -e "s|^SERVER_PUBLIC_IP=.*|SERVER_PUBLIC_IP=${PUBLIC_IP}|" \
      .env > .env.tmp
  mv .env.tmp .env
  log "Generated a fresh Django secret key and random passwords in .env"
else
  log ".env already exists, leaving it as-is"
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

# --- 4. PKI ---
if [[ ! -f pki/ca/ca.cert.pem ]]; then
  log "Generating internal CA"
  ./pki/generate-ca.sh
else
  log "Internal CA already exists"
fi

if [[ ! -f pki/server/server.cert.pem ]]; then
  log "Generating server certificate for ${PUBLIC_IP}"
  ./pki/generate-server-cert.sh "${PUBLIC_IP}"
else
  log "Server certificate already exists"
fi

# --- 5. Render nginx.conf ---
log "Rendering nginx.conf for ${PUBLIC_IP}"
sed "s|\${SERVER_PUBLIC_IP}|${PUBLIC_IP}|g" \
  nginx/nginx.conf.template > nginx/nginx.conf

# --- 6. Build and start ---
log "Building and starting the stack"
docker compose build
docker compose up -d

# --- 7. Migrations ---
log "Waiting for postgres to be healthy"
for _ in $(seq 1 30); do
  status="$(docker compose ps --format '{{.Health}}' postgres 2>/dev/null || true)"
  [[ "${status}" == "healthy" ]] && break
  sleep 2
done

log "Running Django migrations"
docker compose exec -T django python manage.py migrate --noinput

log "Collecting static files for Django Admin"
docker compose exec -T django python manage.py collectstatic --noinput

log "Ensuring the read-only Grafana database role exists"
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${GRAFANA_DB_USER}') THEN
    CREATE ROLE ${GRAFANA_DB_USER} WITH LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${GRAFANA_DB_USER};
GRANT USAGE ON SCHEMA public TO ${GRAFANA_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${GRAFANA_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER};
SQL

docker compose restart grafana >/dev/null

# --- 8. Superuser ---
if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
  log "Creating Django superuser ${DJANGO_SUPERUSER_USERNAME} (non-interactive)"
  docker compose exec -T \
    -e DJANGO_SUPERUSER_USERNAME="${DJANGO_SUPERUSER_USERNAME}" \
    -e DJANGO_SUPERUSER_PASSWORD="${DJANGO_SUPERUSER_PASSWORD}" \
    -e DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" \
    django python manage.py createsuperuser --noinput || true
else
  read -rp "Create a Django Admin superuser now? [Y/n] " create_super
  if [[ "${create_super:-Y}" =~ ^[Yy]$ ]]; then
    docker compose exec django python manage.py createsuperuser
  fi
fi

# --- 9. Sanity check ---
log "Confirming hypertables exist"
HYPERTABLES="$(docker compose exec -T postgres psql -tA -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1;")"
if echo "${HYPERTABLES}" | grep -q "telemetry_probehealth" && echo "${HYPERTABLES}" | grep -q "telemetry_sensorreading"; then
  log "Both hypertables exist: $(echo "${HYPERTABLES}" | tr '\n' ' ')"
else
  echo "WARNING: expected hypertables not found (got: ${HYPERTABLES}). Check migration logs." >&2
fi

# --- 10. WireGuard remote access ---
# A second, independent access path for operator troubleshooting --
# unrelated to the mTLS telemetry path above. See PROJECT_SPEC.md
# Section 5.7.
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

log "Generating the server's WireGuard keypair (if it doesn't already exist)"
./wireguard/generate-server-keys.sh
SERVER_WG_PUBLIC_KEY="$(cat wireguard/server_public.key)"

log "Bringing up the WireGuard interface"
./wireguard/sync-peers.sh

# --- 11. Summary ---
cat <<SUMMARY

WeatherNet server is up.

  Grafana:      http://${PUBLIC_IP}:3000
  Django Admin: https://${PUBLIC_IP}/admin/

Django Admin's certificate is signed by WeatherNet's own internal CA
(server/pki/ca/ca.cert.pem), not a browser-trusted one -- your browser
will show a warning until you import that CA or click through it. There
is no DNS name yet, so this is expected for v1 (see PROJECT_SPEC.md
Section 12). Treat this VM as trusted-network-only for admin access.

IMPORTANT: open UDP port ${WIREGUARD_LISTEN_PORT} in this VM's
firewall/security group for WireGuard, in addition to 443 (nginx) and
3000 (Grafana). Nothing fails locally if you forget this -- probes will
just silently be unable to open a tunnel.

Server WireGuard public key (every probe needs this during its own
setup):
  ${SERVER_WG_PUBLIC_KEY}

Next step: register your first probe.
  1. Generate its client certificate:
       ./pki/generate-probe-cert.sh <probe-uuid>
  2. Register a Probe with that same UUID in Django Admin.
  3. Copy the printed cert/key/CA files to the probe and run its setup.sh
     -- it will also walk through WireGuard setup and print the probe's
     public key to paste back into that Probe's Django Admin record.
  4. After saving a probe's WireGuard fields in Django Admin, run
     ./wireguard/sync-peers.sh here to pick it up as a peer.
SUMMARY
