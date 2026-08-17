# WeatherNet Server

Docker Compose stack: `postgres` (TimescaleDB extension), `django`
(ingestion API + Admin, via gunicorn), `nginx` (mTLS termination,
published on `443`), `grafana` (published on `3000`). Only `nginx` and
`grafana` are reachable from outside the compose network -- see the
comments in `docker-compose.yml`.

See the top-level [`README.md`](../README.md) for the setup walkthrough
and [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) for the full design.

## Layout

- `pki/` -- CA and certificate generation scripts (`generate-ca.sh`,
  `generate-server-cert.sh`, `generate-probe-cert.sh`). Generated key
  material lands under `pki/ca/`, `pki/server/`, and `pki/issued/`, all
  gitignored.
- `wireguard/` -- the operator-troubleshooting WireGuard tunnel, a
  channel entirely separate from mTLS/telemetry (see
  [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) Section 5.7).
  `generate-server-keys.sh` creates the server's keypair (gitignored,
  private key never leaves the host); `sync-peers.sh` regenerates
  `/etc/wireguard/wg0.conf` from every `Probe` with WireGuard fields set
  (via the `generate_wireguard_peers` management command) and applies
  it live. Runs on the VM host directly, not inside Docker.
- `django_app/` -- the Django project. `probes/` is the registry app;
  `telemetry/` is the ingestion endpoint and the two TimescaleDB
  hypertable models (`SensorReading`, `ProbeHealth`).
- `nginx/nginx.conf.template` -- rendered into `nginx.conf` (gitignored)
  by `scripts/setup.sh`, which substitutes the server's public IP.
- `grafana/provisioning/` -- datasource and one example dashboard,
  loaded automatically on Grafana's first boot.
- `scripts/setup.sh` / `scripts/deploy.sh` -- see the top-level README.

## Running Django management commands directly

```bash
docker compose exec django python manage.py <command>
```

## Running the Django test suite

```bash
docker compose exec django python manage.py test
```

The tests exercise the ingestion view (`telemetry/tests.py`): valid
payloads are accepted, unknown/inactive probes and CN/body mismatches
are rejected.

## TimescaleDB notes

The `telemetry` app's migrations (`0001`-`0005`) enable the extension,
create the tables, convert them to hypertables, and set up compression
(`TELEMETRY_COMPRESS_AFTER_DAYS`, default 7) and retention
(`TELEMETRY_RETENTION_DAYS`, default 90) policies from `.env`. See the
comments in `telemetry/migrations/0003_hypertables.py` for why the
default Django primary key had to be replaced with a composite one.
