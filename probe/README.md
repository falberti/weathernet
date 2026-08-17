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
- `config/probe.example.yaml` -- template copied to
  `/etc/weathernet-probe/probe.yaml` by `scripts/setup.sh`.
- `config/weathernet-probe.service` -- systemd unit template.
- `config/wg0.conf.template` -- WireGuard interface config template,
  rendered by `scripts/setup.sh` into `/etc/wireguard/wg0.conf`. This is
  a separate, independent channel from the mTLS telemetry path above,
  used only for operator troubleshooting (SSH via the server) -- see
  [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) Section 6.7. No Python code is
  involved; it's plain `wireguard-tools` + a config file.

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
journalctl -u weathernet-probe -f          # tail logs
systemctl status weathernet-probe          # current state
wc -l /var/lib/weathernet-probe/spool.jsonl  # backlog size (if any)
```

A growing spool file means the probe can't currently reach the server;
it retries the whole backlog, oldest first, every reporting cycle.
