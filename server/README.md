# WeatherNet Server

Docker Compose stack: `postgres` (TimescaleDB extension), `django`
(ingestion + enrollment API + Admin, via gunicorn), `nginx` (mTLS
termination, published on `443`), `grafana` (published on `3000`). Only
`nginx` and `grafana` are reachable from outside the compose network --
see the comments in `docker-compose.yml`.

See the top-level [`README.md`](../README.md) for the setup walkthrough
and [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) for the full design.

## Layout

- `pki/` -- `generate-ca.sh` and `generate-server-cert.sh`, run once at
  server setup. Generated key material lands under `pki/ca/` and
  `pki/server/`, gitignored. There's no per-probe certificate script
  anymore -- issuance happens synchronously inside the `/api/v1/enroll`
  view (`django_app/probes/ca.py`), which is why `pki/ca/ca.key.pem` is
  also bind-mounted (read-only) into the `django` container.
- `wireguard/` -- the operator-troubleshooting WireGuard tunnel, a
  channel entirely separate from mTLS/telemetry (see
  [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) Section 5.8).
  `generate-server-keys.sh` creates the server's keypair (gitignored,
  private key never leaves the host); `sync-peers.sh` regenerates
  `/etc/wireguard/wg0.conf` from every `Probe` with WireGuard fields set
  (via the `generate_wireguard_peers` management command) and applies
  it live. `sync-peers.service`/`sync-peers.timer` run it automatically
  every minute (installed by `scripts/setup.sh`). All of this runs on
  the VM host directly, not inside Docker.
- `django_app/` -- the Django project.
  - `probes/` -- the registry, plus enrollment: `models.py` (`Probe`,
    with optional location/coordinates and owner contact fields for
    operator reference, plus `EnrollmentToken`), `admin.py` (generates a
    token and prints the exact probe-side command when you add one),
    `views.py` (`POST /api/v1/enroll`), `ca.py` (CSR signing via
    `cryptography`), `wireguard.py` (tunnel IP allocation + peer
    rendering), `ssh.py` (best-effort read of the VM's own SSH public
    key, handed to a probe during enrollment -- see
    `SERVER_SSH_PUBLIC_KEY_HOST_PATH` in `.env.example`).
  - `telemetry/` -- the ingestion endpoint and the two TimescaleDB
    hypertable models (`SensorReading`, `ProbeHealth`).
- `nginx/nginx.conf.template` -- rendered into `nginx.conf` (gitignored)
  by `scripts/setup.sh` -- re-run it (safe/idempotent) after pulling a
  change to this file, `deploy.sh` alone does not re-render it.
  `/api/v1/ingest` is the only mTLS-gated location; `/api/v1/enroll`,
  `/api/v1/public/summary` (rate-limited instead, see
  `probes/views.py` `PublicSummaryView`), and `/admin/` share the rest
  of the port without requiring a client certificate.
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

Covers the ingestion view (valid payloads accepted, unknown/inactive
probes and CN/body mismatches rejected -- `telemetry/tests.py`) and the
enrollment view (`probes/tests.py`): a valid token enrolls a probe with
the certificate CN forced to the assigned UUID; unknown/expired/reused
tokens are rejected with the right status codes; a malformed CSR or an
exhausted WireGuard subnet leaves the token unconsumed; and a dedicated
`TransactionTestCase` fires several genuinely concurrent redemption
requests for the same token at real threads to prove the
`select_for_update()` locking in `views.EnrollView` actually prevents
double-redemption under a race, not just in the easy sequential case.

## TimescaleDB notes

The `telemetry` app's migrations (`0001`-`0005`) enable the extension,
create the tables, convert them to hypertables, and set up compression
(`TELEMETRY_COMPRESS_AFTER_DAYS`, default 7) and retention
(`TELEMETRY_RETENTION_DAYS`, default 90) policies from `.env`. See the
comments in `telemetry/migrations/0003_hypertables.py` for why the
default Django primary key had to be replaced with a composite one.
