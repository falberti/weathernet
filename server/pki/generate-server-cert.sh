#!/usr/bin/env bash
# Generate the server's TLS certificate/key, signed by the internal CA,
# with the server's public IP as a Subject Alternative Name.
#
# There is no DNS name for this server (see PROJECT_SPEC.md Section 5.5),
# so the public IP must be passed explicitly -- we deliberately do not
# fall back to "localhost" or try to guess it.
#
# Usage: generate-server-cert.sh <server-public-ip>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="${SCRIPT_DIR}/ca"
CA_KEY="${CA_DIR}/ca.key.pem"
CA_CERT="${CA_DIR}/ca.cert.pem"
SERVER_DIR="${SCRIPT_DIR}/server"
CERT_DAYS="${WEATHERNET_SERVER_CERT_DAYS:-825}"

PUBLIC_IP="${1:-}"
if [[ -z "${PUBLIC_IP}" ]]; then
  echo "Usage: $0 <server-public-ip>" >&2
  echo "The server's public IP is required -- there is no DNS name to fall back to." >&2
  exit 1
fi

if [[ ! -f "${CA_KEY}" || ! -f "${CA_CERT}" ]]; then
  echo "No internal CA found at ${CA_DIR}. Run generate-ca.sh first." >&2
  exit 1
fi

mkdir -p "${SERVER_DIR}"
KEY="${SERVER_DIR}/server.key.pem"
CSR="${SERVER_DIR}/server.csr.pem"
CERT="${SERVER_DIR}/server.cert.pem"
EXT_FILE="${SERVER_DIR}/server.ext.cnf"

openssl genrsa -out "${KEY}" 4096
chmod 600 "${KEY}"

openssl req -new -key "${KEY}" -out "${CSR}" -subj "/O=WeatherNet/CN=${PUBLIC_IP}"

cat > "${EXT_FILE}" <<EOF
subjectAltName = IP:${PUBLIC_IP}
extendedKeyUsage = serverAuth
EOF

openssl x509 -req -in "${CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" -CAcreateserial \
  -out "${CERT}" -days "${CERT_DAYS}" -sha256 -extfile "${EXT_FILE}"

rm -f "${CSR}" "${EXT_FILE}"

echo "Created server certificate for IP ${PUBLIC_IP}:"
echo "  key:  ${KEY}"
echo "  cert: ${CERT}"
echo "These are the files server/nginx expects -- see server/.env.example / setup.sh."
