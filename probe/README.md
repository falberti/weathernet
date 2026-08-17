# WeatherNet Probe

A plain-Python (no containers) daemon that runs as a systemd service:
reads configured sensors and device health metrics on an interval, and
reports them to the server over mutual TLS.

See the top-level [`README.md`](../README.md) for the setup walkthrough
and [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) for the full design.

## Layout

- `weathernet_probe/main.py` -- daemon entrypoint / main loop.
- `weathernet_probe/config.py` -- loads and validates `probe.yaml`.
- `weathernet_probe/health.py` -- real CPU/mem/disk/temperature metrics
  via `psutil` and the Pi's thermal zone file.
- `weathernet_probe/transport.py` -- the mTLS HTTPS client.
- `weathernet_probe/spool.py` -- on-disk retry queue for failed sends.
- `weathernet_probe/sensors/` -- the pluggable sensor framework:
  `base.py` (the `Sensor` interface), `registry.py` (config name →
  driver class), `mock.py` (the only drivers implemented in v1).
- `config/probe.example.yaml` -- documents `probe.yaml`'s schema for
  reference/manual editing later. Not part of the normal setup path --
  `scripts/enroll.py` writes the real one automatically.
- `config/weathernet-probe.service` -- systemd unit template.
- `scripts/setup.sh` -- the one command an operator runs, per
  [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) Section 5.7/8.2: installs
  dependencies (including `wireguard-tools`), calls `enroll.py`,
  installs and starts both systemd services.
- `scripts/enroll.py` -- does the actual enrollment: detects hardware
  type, generates the mTLS keypair + CSR and the WireGuard keypair
  locally via `openssl`/`wg` (private keys never leave this device),
  optionally pins the server's TLS certificate by SHA-256 fingerprint,
  calls `POST /api/v1/enroll`, and writes `probe.yaml` and
  `/etc/wireguard/wg0.conf` from the response. If the response includes
  the server's SSH public key, also appends it to `authorized_keys` for
  the user named by `--ssh-user` (resolved via `pwd.getpwnam()`, since
  this script itself runs under `sudo` and can't trust `$HOME`) -- see
  `PROJECT_SPEC.md` Section 5.7 step 7. No new code under
  `weathernet_probe/` -- this is a standalone script, not part of the
  daemon package, and doesn't add a `cryptography` dependency to the
  probe's runtime requirements (it shells out to `openssl`/`wg`
  instead, consistent with keeping this side of things light -- see
  Design constraints in the spec).

## Running the tests

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Covers the sensor registry (mock sensors return values in the expected
shape, unknown names are rejected) and the spool (append/pop ordering,
cap enforcement, corrupt-line handling).

## Adding a real sensor driver

1. Add a module under `weathernet_probe/sensors/` implementing the
   `Sensor` interface from `sensors/base.py`.
2. Add one entry to `SENSOR_REGISTRY` in `sensors/registry.py`.
3. Reference the new name in a probe's `sensors:` list in
   `probe.yaml`.

Nothing in `main.py` or `transport.py` needs to change -- that's the
whole point of the registry indirection.

## Operating a running probe

```bash
journalctl -u weathernet-probe -f            # tail logs
systemctl status weathernet-probe            # current state
wc -l /var/lib/weathernet-probe/spool.jsonl  # backlog size (if any)
sudo wg show wg0                             # WireGuard tunnel status
```

A growing spool file means the probe can't currently reach the server;
it retries the whole backlog, oldest first, every reporting cycle. The
WireGuard tunnel (`wg-quick@wg0.service`) is a separate, independent
systemd service from `weathernet-probe` -- used only for operator SSH
access via the server, unrelated to telemetry reporting (see
[`PROJECT_SPEC.md`](../PROJECT_SPEC.md) Section 5.8).
