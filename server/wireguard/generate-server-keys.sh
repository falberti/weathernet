#!/usr/bin/env bash
# Generate the server's WireGuard keypair, used for the operator
# troubleshooting tunnel (PROJECT_SPEC.md Section 5.7) -- a separate,
# independent channel from the mTLS telemetry path. Run this once at
# server setup; safe to re-run, it does nothing if a keypair already
# exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_KEY="${SCRIPT_DIR}/server_private.key"
PUBLIC_KEY="${SCRIPT_DIR}/server_public.key"

if ! command -v wg >/dev/null 2>&1; then
  echo "wireguard-tools ('wg') is not installed. Install it before running this script." >&2
  exit 1
fi

if [[ -f "${PRIVATE_KEY}" ]]; then
  echo "Server WireGuard keypair already exists at ${SCRIPT_DIR}, doing nothing." >&2
  echo "Public key: $(cat "${PUBLIC_KEY}")"
  exit 0
fi

umask 077
wg genkey | tee "${PRIVATE_KEY}" | wg pubkey > "${PUBLIC_KEY}"
chmod 600 "${PRIVATE_KEY}"

echo "Generated server WireGuard keypair:"
echo "  private: ${PRIVATE_KEY} (never leaves this host)"
echo "  public:  ${PUBLIC_KEY}"
echo
echo "Public key (every probe needs this during its own setup):"
cat "${PUBLIC_KEY}"
