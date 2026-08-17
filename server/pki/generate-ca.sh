#!/usr/bin/env bash
# Generate the internal Certificate Authority used to sign the server's
# TLS certificate and every probe's client certificate.
#
# This CA is only trusted by WeatherNet components (nginx and the probes);
# it is never meant to be trusted by browsers. Run this once per server
# deployment. The CA private key never leaves this directory and must
# never be committed to git (see server/pki/.gitignore).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="${SCRIPT_DIR}/ca"
CA_KEY="${CA_DIR}/ca.key.pem"
CA_CERT="${CA_DIR}/ca.cert.pem"
CA_DAYS="${WEATHERNET_CA_DAYS:-3650}"

if [[ -f "${CA_KEY}" || -f "${CA_CERT}" ]]; then
  echo "CA already exists at ${CA_DIR}, doing nothing. Remove it manually if you want to regenerate it." >&2
  exit 0
fi

mkdir -p "${CA_DIR}"

openssl genrsa -out "${CA_KEY}" 4096
chmod 600 "${CA_KEY}"

# basicConstraints/keyUsage aren't cosmetic here: recent OpenSSL (via
# urllib3/requests, which the probe uses -- see transport.py) rejects
# the whole chain with "CA cert does not include key usage extension"
# if the CA cert doesn't explicitly declare keyCertSign. A bare
# `openssl req -x509` without -addext produces a CA cert that verifies
# fine with some TLS clients (e.g. plain openssl s_client) and fails
# with others -- don't drop these flags because a quick manual test
# happened to pass.
openssl req -new -x509 -days "${CA_DAYS}" -key "${CA_KEY}" -out "${CA_CERT}" \
  -subj "/O=WeatherNet/CN=WeatherNet Internal CA" \
  -addext "basicConstraints=critical,CA:true" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

echo "Created internal CA:"
echo "  key:  ${CA_KEY} (keep this on the server, never copy it anywhere)"
echo "  cert: ${CA_CERT} (this is the file probes need as their CA trust anchor)"
