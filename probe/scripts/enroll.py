#!/usr/bin/env python3
"""Redeem a WeatherNet enrollment token.

Generates this probe's mTLS and WireGuard keypairs locally (neither
private key ever leaves this device), exchanges the token for a signed
client certificate and an assigned WireGuard tunnel IP, and writes
everything weathernet-probe and wg-quick@wg0 need to start. Invoked by
scripts/setup.sh -- see PROJECT_SPEC.md Section 5.7/6.7.
"""
import argparse
import hashlib
import os
import pwd
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import requests
import yaml

CONFIG_DIR = Path("/etc/weathernet-probe")
CERTS_DIR = CONFIG_DIR / "certs"
WIREGUARD_DIR = Path("/etc/wireguard")
WIREGUARD_CONFIG_PATH = WIREGUARD_DIR / "wg0.conf"
SPOOL_PATH = Path("/var/lib/weathernet-probe/spool.jsonl")

DEFAULT_SENSORS = ["mock_temperature", "mock_humidity", "mock_pressure"]


def detect_hardware_type() -> str:
    """Best-effort Raspberry Pi model detection from the device tree.
    Falls back to generic_linux rather than guessing wrong silently --
    this is only ever a sanity check against what the operator declared
    when creating the token (see server probes/views.py), never
    authoritative.
    """
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore").strip("\x00").strip().lower()
    except OSError:
        return "generic_linux"

    if "raspberry pi 5" in model:
        return "raspberry_pi_5"
    if "raspberry pi 4" in model:
        return "raspberry_pi_4"
    if "raspberry pi 3" in model:
        return "raspberry_pi_3"
    return "generic_linux"


def _run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def generate_mtls_keypair_and_csr():
    """Generate the client private key and a CSR locally via openssl.
    Shelling out to openssl (already required on any Linux box, and
    already used by the server's pki/ scripts) avoids adding a
    `cryptography` dependency to the probe just for this one-time step
    -- keeping runtime dependencies light matters more here than on the
    server (PROJECT_SPEC.md Section 6.1).
    """
    CERTS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = CERTS_DIR / "client.key.pem"
    csr_path = CERTS_DIR / "client.csr.pem"

    _run(["openssl", "genrsa", "-out", str(key_path), "2048"])
    key_path.chmod(0o600)
    # The Subject here is irrelevant: the server forces the issued
    # certificate's CN to the newly-assigned probe UUID regardless of
    # what this CSR requests (see server probes/ca.py).
    _run(["openssl", "req", "-new", "-key", str(key_path), "-out", str(csr_path), "-subj", "/CN=enrolling"])
    csr_pem = csr_path.read_text()
    csr_path.unlink()
    return key_path, csr_pem


def generate_wireguard_keypair():
    WIREGUARD_DIR.mkdir(parents=True, exist_ok=True)
    private_key_path = WIREGUARD_DIR / "privatekey"
    public_key_path = WIREGUARD_DIR / "publickey"

    private_key = _run(["wg", "genkey"]).stdout.strip()
    private_key_path.write_text(private_key + "\n")
    private_key_path.chmod(0o600)

    public_key = subprocess.run(
        ["wg", "pubkey"], input=private_key, capture_output=True, text=True, check=True
    ).stdout.strip()
    public_key_path.write_text(public_key + "\n")

    return private_key, public_key


def verify_fingerprint(server: str, expected_fingerprint: str) -> None:
    """Open a bare TLS connection to the server and compare the
    presented certificate's SHA-256 fingerprint against the expected
    one, aborting loudly before sending anything if they differ. This
    is the only defense this one bootstrap request has against a
    MITM -- there's no CA to verify against yet, that's the whole
    problem enrollment exists to solve.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((server, 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=server) as tls:
            der_cert = tls.getpeercert(binary_form=True)

    actual = hashlib.sha256(der_cert).hexdigest().upper()
    actual_colon = ":".join(actual[i : i + 2] for i in range(0, len(actual), 2))
    expected = expected_fingerprint.replace(":", "").upper()

    if actual != expected:
        sys.exit(
            "FATAL: server certificate fingerprint does not match.\n"
            f"  expected: {expected_fingerprint}\n"
            f"  actual:   {actual_colon}\n"
            "Aborting before sending anything -- this could mean a MITM, or "
            "just a stale --fingerprint value from a re-keyed server."
        )


def write_probe_yaml(probe_id, hardware_type, server_url, client_key_path, report_interval_seconds):
    probe_yaml = {
        "probe_id": probe_id,
        "hardware_type": hardware_type,
        "server_url": f"{server_url}/api/v1/ingest",
        "client_cert_path": str(CERTS_DIR / "client.cert.pem"),
        "client_key_path": str(client_key_path),
        "ca_cert_path": str(CERTS_DIR / "ca.cert.pem"),
        "report_interval_seconds": report_interval_seconds,
        "sensors": DEFAULT_SENSORS,
        "spool_path": str(SPOOL_PATH),
        "spool_max_days": 14,
        "log_path": "/var/log/weathernet-probe/probe.log",
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "probe.yaml").write_text(yaml.safe_dump(probe_yaml, sort_keys=False))


def write_wireguard_config(wg_private_key, wg):
    wg_conf = (
        "[Interface]\n"
        f"PrivateKey = {wg_private_key}\n"
        f"Address = {wg['tunnel_ip']}/32\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {wg['server_public_key']}\n"
        f"Endpoint = {wg['server_endpoint']}\n"
        f"AllowedIPs = {wg['server_tunnel_ip']}/32\n"
        # The probe is almost certainly behind a home NAT with no port
        # forwarding -- without this, the NAT mapping times out and the
        # server can never re-initiate the connection.
        "PersistentKeepalive = 25\n"
    )
    WIREGUARD_CONFIG_PATH.write_text(wg_conf)
    WIREGUARD_CONFIG_PATH.chmod(0o600)


def clear_stale_spool() -> None:
    """Drop any queued-but-unsent readings from a previous enrollment.

    Every spooled entry carries its own `probe_id`, baked in at the time
    it was queued. Once enrollment assigns a *new* probe_id, the server
    will 403 those old entries forever (probe_id/certificate mismatch --
    the certificate that could have matched them no longer exists, it
    was just overwritten). Worse than just wasted retries: the daemon
    sends the spool oldest-first and stops at the first failure, so
    leaving stale entries in place would permanently block *every*
    future reading too, not just the stale ones.
    """
    if SPOOL_PATH.exists():
        SPOOL_PATH.unlink()
        print(f"Cleared {SPOOL_PATH} (held readings queued under the previous identity)")


def install_authorized_key(ssh_user: str, public_key: str) -> None:
    """Append the server's SSH public key to ssh_user's authorized_keys,
    so the server can SSH into this probe with no manual key exchange
    (PROJECT_SPEC.md Section 5.7).

    This script runs elevated (it also writes /etc/weathernet-probe and
    /etc/wireguard, both root-only), so `$HOME`/`Path.home()` here would
    resolve to root's home, not the actual operator's -- ssh_user is
    resolved explicitly via pwd instead, and every file this touches is
    chowned back to that user.
    """
    try:
        pw = pwd.getpwnam(ssh_user)
    except KeyError:
        print(f"WARNING: no such user '{ssh_user}' -- skipping SSH key install.", file=sys.stderr)
        return

    key_line = public_key.strip()
    if not key_line:
        return

    ssh_dir = Path(pw.pw_dir) / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chown(ssh_dir, pw.pw_uid, pw.pw_gid)
    ssh_dir.chmod(0o700)

    authorized_keys = ssh_dir / "authorized_keys"
    existing = authorized_keys.read_text() if authorized_keys.exists() else ""
    if key_line in existing:
        return  # already installed -- setup.sh is meant to be re-runnable

    with authorized_keys.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(key_line + "\n")
    os.chown(authorized_keys, pw.pw_uid, pw.pw_gid)
    authorized_keys.chmod(0o600)

    print(f"Installed the server's SSH public key into {authorized_keys}")


def enroll(server: str, token: str, fingerprint: str | None, ssh_user: str | None = None) -> None:
    hardware_type = detect_hardware_type()
    print(f"Detected hardware type: {hardware_type}")

    if fingerprint:
        print("Verifying server certificate fingerprint before sending anything...")
        verify_fingerprint(server, fingerprint)
    else:
        print(
            "WARNING: no --fingerprint given, proceeding without TLS pinning. "
            "This is a real reduction in the guarantee that you're talking to "
            "the right server, not a silent one.",
            file=sys.stderr,
        )

    print("Generating local mTLS and WireGuard keys (private keys never leave this device)...")
    client_key_path, csr_pem = generate_mtls_keypair_and_csr()
    wg_private_key, wg_public_key = generate_wireguard_keypair()

    print("Calling the enrollment endpoint...")
    try:
        response = requests.post(
            f"https://{server}/api/v1/enroll",
            json={
                "token": token,
                "csr_pem": csr_pem,
                "wireguard_public_key": wg_public_key,
                "detected_hardware_type": hardware_type,
            },
            # There's no CA to verify against yet for this one request --
            # the fingerprint check above (if --fingerprint was given) is
            # the actual defense here, not TLS verification.
            verify=False,
            timeout=30,
        )
    except requests.RequestException as exc:
        sys.exit(f"FATAL: could not reach the server: {exc}")

    if response.status_code != 201:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        sys.exit(f"FATAL: enrollment failed ({response.status_code}): {detail}")

    data = response.json()
    probe_id = data["probe_id"]
    print(f"Enrolled as probe {probe_id}")

    (CERTS_DIR / "client.cert.pem").write_text(data["client_cert_pem"])
    (CERTS_DIR / "client.cert.pem").chmod(0o600)
    (CERTS_DIR / "ca.cert.pem").write_text(data["ca_cert_pem"])
    (CERTS_DIR / "ca.cert.pem").chmod(0o600)

    write_probe_yaml(
        probe_id=probe_id,
        hardware_type=hardware_type,
        server_url=data["server_url"],
        client_key_path=client_key_path,
        report_interval_seconds=data["report_interval_seconds"],
    )
    write_wireguard_config(wg_private_key, data["wireguard"])
    clear_stale_spool()

    print(f"Wrote {CONFIG_DIR / 'probe.yaml'} and {WIREGUARD_CONFIG_PATH}")
    print(f"WireGuard tunnel IP: {data['wireguard']['tunnel_ip']}")

    server_ssh_key = data.get("server_ssh_public_key")
    if server_ssh_key and ssh_user:
        install_authorized_key(ssh_user, server_ssh_key)
    elif not server_ssh_key:
        print(
            "No server SSH public key in the response -- the server may not have "
            "SERVER_SSH_PUBLIC_KEY_HOST_PATH configured. You'll need another way "
            "in (e.g. `ssh -A` agent forwarding through the server)."
        )


def main():
    parser = argparse.ArgumentParser(description="Redeem a WeatherNet enrollment token")
    parser.add_argument("--server", required=True, help="Server public IP")
    parser.add_argument("--token", required=True, help="Enrollment token, from Django Admin")
    parser.add_argument(
        "--fingerprint",
        default=None,
        help="Server certificate SHA-256 fingerprint, for TLS pinning (recommended)",
    )
    parser.add_argument(
        "--ssh-user",
        default=None,
        help="Local user whose authorized_keys should receive the server's SSH public key",
    )
    args = parser.parse_args()
    enroll(args.server, args.token, args.fingerprint, args.ssh_user)


if __name__ == "__main__":
    main()
