#!/usr/bin/env bash
# Generate a client certificate/key pair for one probe, signed by the
# internal CA, with the certificate's Common Name set to the probe's
# UUID. That UUID is also the primary key of the Probe row in Django --
# cert generation and probe registration must agree on it.
#
# Usage: generate-probe-cert.sh <probe-uuid>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="${SCRIPT_DIR}/ca"
CA_KEY="${CA_DIR}/ca.key.pem"
CA_CERT="${CA_DIR}/ca.cert.pem"
ISSUED_DIR="${SCRIPT_DIR}/issued"
CERT_DAYS="${WEATHERNET_PROBE_CERT_DAYS:-825}"

# Path the probe's setup.sh expects certs to be installed at (see
# probe/config/probe.example.yaml and probe/scripts/setup.sh).
PROBE_INSTALL_DIR="/etc/weathernet-probe/certs"

PROBE_ID="${1:-}"
if [[ -z "${PROBE_ID}" ]]; then
  echo "Usage: $0 <probe-uuid>" >&2
  echo "The UUID must match the id you will register for this probe in Django Admin." >&2
  exit 1
fi

UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
if [[ ! "${PROBE_ID}" =~ ${UUID_RE} ]]; then
  echo "Error: '${PROBE_ID}' is not a UUID. The cert's CN must exactly match the Probe's" >&2
  echo "id in Django (it is looked up by this value on every ingest). Generate one with:" >&2
  echo "  python3 -c 'import uuid; print(uuid.uuid4())'" >&2
  exit 1
fi

if [[ ! -f "${CA_KEY}" || ! -f "${CA_CERT}" ]]; then
  echo "No internal CA found at ${CA_DIR}. Run generate-ca.sh first." >&2
  exit 1
fi

OUT_DIR="${ISSUED_DIR}/${PROBE_ID}"
if [[ -e "${OUT_DIR}" ]]; then
  echo "A certificate already exists at ${OUT_DIR}. Remove it manually if you want to reissue." >&2
  exit 1
fi
mkdir -p "${OUT_DIR}"

KEY="${OUT_DIR}/client.key.pem"
CSR="${OUT_DIR}/client.csr.pem"
CERT="${OUT_DIR}/client.cert.pem"
EXT_FILE="${OUT_DIR}/client.ext.cnf"

openssl genrsa -out "${KEY}" 4096
chmod 600 "${KEY}"

openssl req -new -key "${KEY}" -out "${CSR}" -subj "/O=WeatherNet/CN=${PROBE_ID}"

cat > "${EXT_FILE}" <<EOF
extendedKeyUsage = clientAuth
EOF

openssl x509 -req -in "${CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" -CAcreateserial \
  -out "${CERT}" -days "${CERT_DAYS}" -sha256 -extfile "${EXT_FILE}"

cp "${CA_CERT}" "${OUT_DIR}/ca.cert.pem"
rm -f "${CSR}" "${EXT_FILE}"

echo "Created client certificate for probe ${PROBE_ID}:"
echo "  ${OUT_DIR}/"
echo
echo "Copy these three files to the probe (out of band, e.g. scp) into"
echo "${PROBE_INSTALL_DIR}/ before running the probe's setup.sh:"
echo
echo "  ssh <probe-user>@<probe-host> 'mkdir -p ${PROBE_INSTALL_DIR}'"
echo "  scp ${OUT_DIR}/client.cert.pem ${OUT_DIR}/client.key.pem ${OUT_DIR}/ca.cert.pem \\"
echo "      <probe-user>@<probe-host>:${PROBE_INSTALL_DIR}/"
echo
echo "Then register the probe (id=${PROBE_ID}) in Django Admin before its first report."
