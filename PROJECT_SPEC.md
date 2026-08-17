# WeatherNet — Telemetry Platform for Distributed Weather Probes

## 1. Overview

WeatherNet is a small telemetry platform for a network of environmental
monitoring devices ("probes"). Version 1 targets a single Raspberry Pi probe
reporting to a single server, but the codebase must be structured so that
additional probes and hardware types can be added later without rework.

**Components:**

- **Server**: a single Docker Compose stack (Django, PostgreSQL with the
  TimescaleDB extension, Grafana, and an Nginx reverse proxy) running on a
  VM with a public IP and no DNS record. Time-series data lives as
  hypertables in the same Postgres instance Django already uses for its own
  tables — there is deliberately no separate time-series database service.
- **Probe**: a lightweight, non-containerized Python application running as
  a systemd service on resource-constrained devices (starting with a
  Raspberry Pi). It reads sensor data through a pluggable driver interface
  and reports device health metrics.
- **Transport**: probes authenticate to the server exclusively via mutual
  TLS (mTLS). There is no username/password auth on the ingestion path.

This document is the implementation brief for the first version of the
codebase. Follow it section by section; where a decision was deliberately
left open for a future version, it is called out explicitly in
[Section 12](#12-explicitly-out-of-scope-for-v1).

## 2. Goals for v1

- A working, end-to-end path: probe reads mock sensor data + real device
  health metrics → sends them over mTLS → Django validates the probe against
  its registry → data lands in TimescaleDB hypertables → visible in Grafana.
- A minimal Django Admin-based UI to register/view probes (no custom
  frontend).
- One-shot setup scripts for both the server and a probe, and simple deploy
  scripts to pull and apply codebase updates on each side.
- A hardware abstraction layer on the probe side so that swapping in real
  sensor drivers, or supporting a Raspberry Pi 4/5 instead of a 3, is a
  matter of configuration and adding a driver module — not restructuring the
  app.
- All code, comments, docstrings, commit messages, and documentation in
  US English.

## 3. Architecture

```
                          ┌───────────────────────────────────────────────────┐
                          │  Server VM (public IP, no DNS)                    │
                          │                                                    │
  ┌──────────┐  mTLS      │  ┌────────┐   verified    ┌────────┐             │
  │  Probe   │───────────▶│  │ nginx  │──cert CN hdr─▶│ django │             │
  │ (RPi,    │  HTTPS/443 │  │ (TLS + │               │        │──┐          │
  │ systemd, │            │  │ mTLS   │               └────────┘  │writes    │
  │ no       │            │  │ term.) │                            ▼          │
  │ Docker)  │            │  └────────┘               ┌────────────────────┐ │
  └──────────┘            │                           │ postgres +         │ │
                          │                           │ timescaledb ext.   │ │
                          │                           │ (registry tables + │ │
                          │                           │  sensor/health     │ │
                          │                           │  hypertables)      │ │
                          │                           └──────────┬─────────┘ │
                          │                                      │reads      │
                          │                                      ▼           │
                          │                                 ┌────────┐       │
                          │                                 │ grafana│       │
                          │                                 └────────┘       │
                          └───────────────────────────────────────────────────┘
```

**Data flow decision (do not deviate without discussion):** probes never
talk to Postgres or Grafana directly. Every telemetry payload goes
`probe → nginx (mTLS termination) → Django ingestion endpoint → TimescaleDB
hypertables`. Django is the single point where a probe's identity (from its
client certificate) is cross-checked against the probe registry (must exist
and be marked active) before anything is written. This keeps the registry
authoritative: deactivating a probe in Django Admin actually stops its data
from being accepted, not just from being displayed.

**Why TimescaleDB instead of a separate time-series database (e.g.
InfluxDB):** at this project's scale — a handful of probes reporting every
few minutes — the storage-efficiency gap between InfluxDB, TimescaleDB, and
purpose-built engines like TDengine is negligible in absolute terms; that
gap only shows up at a scale (millions of devices) this project will never
reach. What does matter for a v1 is running the fewest possible services.
Because TimescaleDB is a Postgres extension, the sensor/health hypertables
live in the exact same Postgres instance Django already needs for its own
tables — one database service to run, back up, and reason about instead of
two.

Django's own HTTP port must **not** be published outside the Docker
network — only nginx is reachable from outside the compose stack for the
ingestion path. Grafana may be published on its own port for the operator's
own dashboard access (see Section 5.4).

**A second, independent channel: WireGuard remote access.** Separate from
the telemetry path above, each probe also maintains an outbound WireGuard
tunnel to the server, so the operator can reach a probe for troubleshooting
even when it's behind a home NAT with no port forwarding:

```
  ┌──────────┐  WireGuard (UDP)      ┌────────────────────┐
  │  Probe   │───────────────────────▶│  Server: wg0        │
  │ wg0      │  probe-initiated,      │  (runs on the VM    │
  │ 10.10.0.x│  keeps itself open     │  host, not in       │
  └──────────┘  via keepalive         │  Docker — see 5.7)  │
                                       │  10.10.0.1          │
                                       └──────────┬───────────┘
                                                   │ operator SSHes into
                                                   │ the server first, then
                                                   ▼ hops to 10.10.0.x
                                          operator's own SSH session
```

This is deliberately a **separate** system from mTLS/telemetry: it exists
purely for human operator access when something's wrong, it's not part of
the data path, and it should not be conflated with probe authentication for
ingestion. A probe with a revoked mTLS cert can still (and should still) be
reachable over WireGuard so you can go fix it.

## 4. Repository Layout

```
weathernet/
├── PROJECT_SPEC.md                # this file
├── README.md
├── .gitignore
├── server/
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── nginx/
│   │   ├── nginx.conf.template     # templated with server IP at setup time
│   │   └── Dockerfile              # if custom image needed, else use official
│   ├── django_app/
│   │   ├── manage.py
│   │   ├── config/                 # Django project settings package
│   │   │   ├── settings.py
│   │   │   ├── urls.py
│   │   │   └── wsgi.py
│   │   ├── probes/                 # app: probe registry + admin
│   │   │   ├── models.py
│   │   │   ├── admin.py
│   │   │   ├── management/
│   │   │   │   └── commands/
│   │   │   │       └── generate_wireguard_peers.py
│   │   │   └── migrations/
│   │   ├── telemetry/              # app: ingestion API + hypertable models
│   │   │   ├── views.py
│   │   │   ├── serializers.py
│   │   │   ├── models.py            # SensorReading / ProbeHealth (hypertables)
│   │   │   └── urls.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── grafana/
│   │   └── provisioning/
│   │       ├── datasources/postgres.yml
│   │       └── dashboards/          # one example dashboard JSON
│   ├── scripts/
│   │   ├── setup.sh
│   │   └── deploy.sh
│   ├── pki/
│   │   ├── generate-ca.sh
│   │   ├── generate-server-cert.sh
│   │   └── generate-probe-cert.sh
│   └── wireguard/
│       ├── generate-server-keys.sh  # run once at server setup
│       ├── sync-peers.sh            # run on the host after registry changes
│       └── wg0.conf.header          # static [Interface] block template
├── probe/
│   ├── weathernet_probe/
│   │   ├── __init__.py
│   │   ├── main.py                 # daemon entrypoint / main loop
│   │   ├── config.py                # loads probe config YAML
│   │   ├── health.py                # CPU/mem/disk/temperature via psutil
│   │   ├── transport.py             # mTLS HTTP client + local buffering
│   │   ├── spool.py                 # on-disk retry queue for failed sends
│   │   └── sensors/
│   │       ├── base.py              # abstract Sensor interface
│   │       ├── registry.py          # maps config names to sensor classes
│   │       └── mock.py              # MockTemperatureSensor, MockHumiditySensor, ...
│   ├── config/
│   │   ├── probe.example.yaml
│   │   ├── weathernet-probe.service # systemd unit template
│   │   └── wg0.conf.template        # WireGuard interface config template
│   ├── requirements.txt
│   ├── scripts/
│   │   ├── setup.sh
│   │   └── deploy.sh
│   └── tests/
│       └── ...
└── docs/
    └── api-contract.md              # formal request/response schema
```

## 5. Server Component

### 5.1 Docker Compose services

- `postgres` — use the `timescale/timescaledb` image (a standard Postgres
  image with the TimescaleDB extension pre-installed; pick a tag matching
  a current Postgres major version, e.g. `timescale/timescaledb:latest-pg16`)
  instead of the plain `postgres` image. It is a drop-in replacement as far
  as Django's `django.db.backends.postgresql` is concerned — Django doesn't
  need to know the extension is there. Serves both Django's own tables
  (auth, admin, `probes`) and the `telemetry` app's hypertables. Named
  volume for persistence. Not published to the host; only `django` (for
  reads/writes) and `grafana` (for reads) need to reach it internally.
- `django` — Django app served via gunicorn. Not published to the host;
  only reachable from `nginx` over the compose-internal network.
- `grafana` — published on its own host port (e.g. `3000`) for the
  operator to view dashboards directly by IP. Pre-provisioned with a
  PostgreSQL datasource pointed at the same `postgres` service, and one
  example dashboard on first boot (see 5.4).
- `nginx` — the only service handling the mTLS ingestion path. Published on
  `443`. Terminates TLS with the server's certificate, **requires and
  verifies** the client certificate against the internal CA, and — only on
  successful verification — proxies the request to `django`, forwarding the
  verified certificate's CN in a header (e.g. `X-Client-Cert-CN`). Reject
  with `495`/`496` on missing or invalid client cert (nginx does this
  natively; no custom code needed there).

All services on one Docker network, defined in `docker-compose.yml`. Use
named volumes for `postgres` and `grafana` data so `docker compose down`
doesn't destroy data (only `down -v` should).

Note: WireGuard (Section 5.7) is deliberately **not** one of these
services. Creating a network interface (`wg0`) from inside a container
means either `--privileged` or `NET_ADMIN` plus host networking mode —
extra complexity for no real benefit here. It runs directly on the VM host
using the standard `wg-quick` tooling, alongside the Docker stack rather
than inside it.

### 5.2 Django app design

Two apps:

**`probes`** — the registry.

`Probe` model fields:
- `id` — UUID, primary key. This value **is** the client certificate's CN,
  so cert generation and probe registration must agree on it.
- `name` — human-readable label (e.g. "garden-station-01").
- `hardware_type` — choices: `raspberry_pi_3`, `raspberry_pi_4`,
  `raspberry_pi_5`, `generic_linux` (leave room to add `arduino` later
  without a migration headache — use a `TextChoices` class, not a hardcoded
  list scattered around).
- `location` — free text, optional.
- `notes` — free text, optional.
- `wireguard_public_key` — text, nullable. Filled in by the operator (copy-
  pasted from the probe's setup script output) once the probe has generated
  its own keypair — see Section 5.7. Not required at probe creation time.
- `wireguard_tunnel_ip` — nullable, unique when set (e.g.
  `GenericIPAddressField`). The probe's address inside the WireGuard subnet
  (e.g. `10.10.0.5`), manually allocated by the operator. This is *not* the
  probe's home/public IP — that's dynamic and irrelevant here, since the
  probe always initiates the tunnel outward (see Section 5.7).
- `is_active` — boolean, default `True`. Ingestion is rejected for inactive
  probes.
- `created_at` — auto.
- `last_seen_at` — nullable datetime, updated on every successful ingest.
- `last_health_summary` — nullable JSON field, updated on every successful
  ingest with the most recent health payload (cpu_temp_c, cpu_percent,
  mem_percent, disk_percent, uptime_seconds). This lets Django Admin show
  probe health at a glance with a single indexed lookup, instead of
  querying the (much larger) hypertable for the latest row every time the
  admin list page renders.

Register `Probe` in `admin.py` with a sensible `list_display` (name,
hardware_type, is_active, last_seen_at, wireguard_tunnel_ip) and
`list_filter` on `hardware_type` / `is_active`. No custom admin views or
templates for v1 — stock `ModelAdmin` is sufficient.

**`telemetry`** — the ingestion endpoint.

- `POST /api/v1/ingest` — the only endpoint probes call. See
  [Section 7](#7-ingestion-api-contract) for the payload schema.
- The view trusts the `X-Client-Cert-CN` header **only** because Django is
  not reachable except through nginx (document this trust boundary clearly
  in a code comment at the top of the view — this is a security-relevant
  assumption, not an implementation detail).
- Looks up `Probe` by the CN. 404 if it doesn't exist, 403 if `is_active` is
  `False`.
- Validates the payload shape (use a `serializers.Serializer`, not raw dict
  access).
- Writes rows via the ordinary Django ORM into the two hypertables defined
  in `telemetry/models.py` (one row per reading, one row for the health
  payload — see Section 5.3 and Section 7). No separate database client
  library is needed; it's the same Postgres connection Django already has.
- Updates `last_seen_at` and `last_health_summary` on the `Probe` row.
- Returns `201` on success with an empty body; `4xx` with a short JSON error
  reason otherwise.

Use Django REST Framework if it makes the serializer/validation code
cleaner; if you'd rather avoid the extra dependency for one endpoint, a
plain Django view with manual JSON schema validation is also acceptable —
your call, but be consistent and don't half-adopt DRF.

### 5.3 TimescaleDB

Two regular Django models, promoted to hypertables via a migration:

- `SensorReading` — fields: `time` (the partitioning column), `probe`
  (FK to `Probe`), `sensor_type` (str), `value` (float), `unit` (str,
  nullable).
- `ProbeHealth` — fields: `time`, `probe` (FK), `cpu_temp_c`, `cpu_percent`,
  `mem_percent`, `disk_percent`, `uptime_seconds`.

Implementation notes for whoever writes the migrations:

1. First migration: `CREATE EXTENSION IF NOT EXISTS timescaledb;` via
   `migrations.RunSQL`. This only needs to run once per database.
2. Let Django create the two tables normally (regular `CreateModel`
   migrations) — **do not** rely on Django's default single-column
   auto-incrementing primary key as the row identity on these tables.
   TimescaleDB hypertables partition on the time column, and a plain
   auto-increment PK works against that; either drop the PK in favor of a
   plain index strategy, or use a composite key that includes `time`. Pick
   whichever Django supports more cleanly and note the reasoning in a code
   comment — this is a real TimescaleDB-specific gotcha, not a style
   preference.
3. Follow-up migration, after the tables exist: `RunSQL` calling
   `SELECT create_hypertable('telemetry_sensorreading', 'time');` and the
   same for `telemetry_probehealth` (adjust for Django's actual generated
   table names).
4. Another `RunSQL` step enabling native compression and a compression
   policy for chunks older than a configurable age (e.g. 7 days):
   `ALTER TABLE ... SET (timescaledb.compress, timescaledb.compress_segmentby = 'probe_id');`
   followed by `SELECT add_compression_policy(...)`. This — not the choice
   of database engine — is what actually keeps disk usage in check as data
   accumulates.
5. A retention policy via `SELECT add_retention_policy(...)`, with the
   retention window configurable through `.env` (mirrors what a bucket
   retention setting would have done in a dedicated TSDB).

Indexes: at minimum, an index on `(probe_id, time DESC)` on both
hypertables to keep "latest reading per probe"-style queries (which Grafana
and the Django Admin health summary both need) fast as the tables grow.

### 5.4 Grafana

- Provision Grafana's built-in **PostgreSQL** datasource automatically via
  the `grafana/provisioning/datasources/postgres.yml` file, pointed at the
  same `postgres` service and database Django uses — the operator should
  never have to click through the UI to connect it. Grafana's PostgreSQL
  datasource understands TimescaleDB natively (it's just SQL plus the
  `$__timeFilter()` macro for the dashboard time range), no special plugin
  needed.
- Provision one example dashboard (health: last-seen timestamps and CPU
  temp per probe, queried from `telemetry_probehealth`; a placeholder panel
  for `telemetry_sensorreading` once real sensors are attached) so there's
  something to look at immediately after setup, not an empty Grafana
  instance.
- Default admin credentials come from `.env`, not hardcoded.

### 5.5 mTLS / PKI

Manual, script-assisted certificate issuance for v1 — no automated
enrollment protocol. This is a deliberate scope cut; see Section 12.

- `pki/generate-ca.sh` — run once at server setup. Creates a self-signed
  internal CA (key + cert). This CA's only job is signing probe client
  certs and the server cert; it is never meant to be trusted by browsers.
- `pki/generate-server-cert.sh` — generates the server's TLS cert/key,
  signed by the internal CA, **with the server's public IP as a Subject
  Alternative Name** (there is no DNS name to use — the script must take
  the IP as an argument and fail loudly if it's not provided; don't assume
  `localhost` or guess).
- `pki/generate-probe-cert.sh <probe-name-or-uuid>` — generates a client
  cert/key pair for one probe, signed by the internal CA, with the CN set
  to the probe's UUID. Output the cert+key to a clearly named directory
  (e.g. `pki/issued/<probe-id>/`) that is **gitignored**. Print the exact
  `scp` command the operator should run to copy the files to the probe as
  part of the script's output — don't make them guess the path.
- Document clearly in the README that:
  1. The CA's private key never leaves the server.
  2. Client cert + key are copied to the probe manually (out of band, e.g.
     `scp`) during probe setup.
  3. This is a manual process by design for v1; automating enrollment
     (e.g. a bootstrap token exchanged for a cert) is a natural v2
     improvement, not a v1 requirement.

### 5.6 Configuration

`.env.example` at `server/` covering at minimum: Postgres credentials
(shared by Django and, read-only if practical, by Grafana's datasource),
Django `SECRET_KEY` (generate a fresh one in `setup.sh`, don't ship a
default), Django `DEBUG` (must default to `False`), Django
`ALLOWED_HOSTS` (must include the server's public IP), the hypertable
retention window (e.g. `TELEMETRY_RETENTION_DAYS`, default 90) and
compression age (e.g. `TELEMETRY_COMPRESS_AFTER_DAYS`, default 7) used by
the migration that sets up the TimescaleDB policies, `WIREGUARD_SUBNET`
(default `10.10.0.0/24`) and `WIREGUARD_LISTEN_PORT` (default `51820`),
and Grafana admin user/password. `.env` itself must be gitignored.

The server's own WireGuard keypair is **not** an env var — like the mTLS
CA, it's a generated file (`wireguard/server_private.key`,
`wireguard/server_public.key`), gitignored, with the private key never
leaving the host.

### 5.7 Remote Access via WireGuard

A second, independent access path for operator troubleshooting — see the
diagram and rationale in Section 3. Manual, script-assisted setup, same
philosophy as the mTLS PKI in Section 5.5: no automatic peer enrollment,
the operator runs a script and pastes a key into Django Admin.

**Server side, once, at setup time:**

1. `wireguard/generate-server-keys.sh` generates the server's keypair and
   prints the public key (the operator needs this later for every probe's
   config, so print it clearly and also save it somewhere the setup
   summary can reference).
2. `wg-quick up wg0` is brought up on the host directly (not in Docker —
   see the note in 5.1), listening on `WIREGUARD_LISTEN_PORT`, with its own
   tunnel address `10.10.0.1` (the first address in `WIREGUARD_SUBNET`).
3. The VM's firewall / cloud security group needs the WireGuard UDP port
   opened, in addition to `443` for nginx and Grafana's port — call this
   out explicitly in the setup script's summary output, since it's easy to
   forget and the failure mode (tunnel silently doesn't come up) is
   confusing to debug.

**Registering a probe's WireGuard access, per probe:**

1. During probe setup (Section 6.7), the probe generates its own keypair
   locally and the setup script prints the public key.
2. The operator pastes that public key into the `Probe` record in Django
   Admin, along with a `wireguard_tunnel_ip` they pick (next free address
   in the subnet — no automatic allocation in v1, the operator just needs
   to not reuse one; Django Admin's `unique=True` on the field will catch a
   mistake at save time).
3. The operator runs `wireguard/sync-peers.sh` on the server host. This
   script: calls `python manage.py generate_wireguard_peers` inside the
   `django` container (a small management command that queries all
   `Probe` rows with a non-null `wireguard_public_key`, active or not —
   see the note below on why inactive probes still get a peer entry — and
   prints one `[Peer]` stanza per probe) → combines that output with the
   static `[Interface]` header (server private key, listen port,
   `wireguard/wg0.conf.header`) → writes the result to
   `/etc/wireguard/wg0.conf` on the host → runs
   `wg syncconf wg0 <(wg-quick strip /etc/wireguard/wg0.conf)` to apply the
   new peer list **without dropping existing tunnels**.
4. Each peer stanza's `AllowedIPs` is that probe's tunnel IP as a single
   `/32` — not the whole subnet. WireGuard doesn't bridge peers to each
   other unless you configure it to; keeping each `AllowedIPs` scoped to
   one `/32` means this stays strictly hub-and-spoke (server ↔ each probe),
   with no probe able to reach another probe through the server even if it
   wanted to.

**Why inactive probes still get a WireGuard peer entry:** `is_active` on
`Probe` gates *telemetry ingestion*, not SSH reachability. A probe you've
deactivated because its sensors are misbehaving is exactly the probe you
most want to still be able to SSH into. Don't wire these two flags
together.

**Reaching a probe:** the operator SSHes into the server (existing access,
unrelated to any of this), then from there `ssh <user>@10.10.0.x` to the
probe's tunnel address. The server acts as a bastion; the operator's own
laptop does not need to be a WireGuard peer for v1 (see Section 12 for the
option to add that later).

## 6. Probe Component

### 6.1 Design constraints

- No containerization. Plain Python 3, a virtualenv, and a systemd service.
  Assume Raspberry Pi OS (current stable, Debian-based) as the target OS for
  v1, but don't hardcode Raspberry Pi OS paths where a generic Linux
  assumption would do just as well — the goal is "runs on a Pi today,
  doesn't fight you on a generic Debian box tomorrow."
- Must run comfortably on a Raspberry Pi 3 — keep dependencies light.
  `psutil` for health metrics is fine; avoid pulling in heavy frameworks.
- Single long-running process managed by systemd (`Restart=on-failure`),
  not a cron/systemd-timer firing a fresh process each cycle — simpler to
  reason about for connection reuse and in-memory buffering state.

### 6.2 Pluggable sensor framework

This is the most important structural piece of the probe for future-proofing.

- `sensors/base.py` defines an abstract `Sensor` class with at least:
  `sensor_type` (str identifier, e.g. `"temperature_c"`), and a `read()`
  method returning a numeric value (or raising a well-defined exception on
  read failure — a failed sensor must not crash the whole reporting cycle).
- `sensors/mock.py` implements a small set of mock sensors (e.g.
  `MockTemperatureSensor`, `MockHumiditySensor`, `MockPressureSensor`) that
  return plausible randomized values. These stand in for the real BME680 /
  SPS30 / wind-rain drivers, which are **explicitly deferred** — see
  Section 12. Do not attempt to wire up real I2C/SPI hardware libraries in
  this version.
- `sensors/registry.py` maps a string name (as it appears in the probe's
  YAML config) to a `Sensor` subclass, so which sensors are "active" on a
  given probe is a config change, not a code change. Adding a real driver
  later means adding one module and one registry entry — nothing else in
  `main.py` or `transport.py` should need to change.
- The probe's YAML config lists which sensors to instantiate by name (see
  6.6). This is also where hardware-type-specific behavior hooks in: the
  registry/config combination is the extension point for a future Arduino
  gateway mode, not a separate code path bolted on elsewhere.

### 6.3 Health metrics (real implementation, not mocked)

Implement for real using `psutil` plus reading the Pi's thermal zone file
(`/sys/class/thermal/thermal_zone0/temp`) for CPU temperature, with a
fallback (log a warning, report `null`) if that path doesn't exist on a
non-Pi host. Report: CPU temperature (°C), CPU utilization (%), memory
utilization (%), disk utilization (%) of the root filesystem, and process
uptime (seconds since the daemon started). This is infrastructure-level
telemetry, independent of the mock/real sensor decision above — it must be
genuinely functional in v1, since it's what an operator would actually use
to notice a probe in distress.

### 6.4 Transport and local buffering

- `transport.py` builds an `https` client (Python's `requests` with
  `cert=(client_cert_path, client_key_path)` and `verify=<path to CA cert>`
  — do **not** disable certificate verification anywhere, including for the
  server's own cert; the probe must verify the server just as the server
  verifies the probe).
- On send failure (network error, non-2xx response, timeout), do not drop
  the reading: append it to a local on-disk spool
  (`spool.py` — a simple append-only JSON-lines file is sufficient, no need
  for a database on the probe side) and retry on the next cycle, oldest
  first, with a sane cap on spool size so a long outage doesn't fill the
  SD card (e.g. cap at N days worth of readings at the configured interval,
  drop oldest beyond that, and log when this happens).
- This matters: these devices are meant to run outdoors, unattended, for
  months. A flaky connection should degrade to "data delayed" not "data
  lost."

### 6.5 Main loop

`main.py`: load config → instantiate configured sensors from the registry →
loop forever at the configured interval → read all sensors (catching and
logging per-sensor failures without aborting the cycle) → read health
metrics → attempt to flush the spool, then send the current reading →
sleep. Use Python's `logging` module with a rotating file handler, not
bare `print()`.

### 6.6 Probe configuration file

`config/probe.example.yaml` — a template the setup script copies and fills
in. Must include: `probe_id` (UUID, must match the CN baked into the
client cert), `hardware_type`, `server_url` (the ingestion endpoint, built
from the public IP the operator provides at setup time), paths to the
client cert/key/CA cert, `report_interval_seconds`, and a list of enabled
sensor names matching entries in `sensors/registry.py`.

### 6.7 WireGuard remote access setup

This is OS-level configuration, not part of the Python application — no
new code under `weathernet_probe/`, just `wireguard-tools` (the `wg` /
`wg-quick` CLIs) and a config file. Handled by `probe/scripts/setup.sh`:

1. Install `wireguard-tools` via the system package manager.
2. Generate a keypair locally (`wg genkey | tee /etc/wireguard/privatekey |
   wg pubkey > /etc/wireguard/publickey`, permissions locked down to
   root-readable-only on the private key). Print the public key to the
   terminal with an explicit instruction: "paste this into the probe's
   `wireguard_public_key` field in Django Admin."
3. Prompt for: the server's WireGuard public key (from the server setup
   output — the operator has to have this on hand, same as they need the
   server's public IP for the mTLS config) and the tunnel IP the operator
   is assigning this probe (must match what they're about to enter in
   Django Admin — the script doesn't validate this against the server,
   there's no live handshake at setup time, just tell the operator plainly
   to keep the two in sync).
4. Render `wg0.conf` from `config/wg0.conf.template` into
   `/etc/wireguard/wg0.conf`: `Address` = this probe's tunnel IP,
   `PrivateKey` = the one just generated, one `[Peer]` block for the server
   (`PublicKey` = server's public key, `Endpoint` = `<server-public-ip>:
   <wireguard-port>`, `AllowedIPs` = the server's tunnel IP as a `/32`,
   `PersistentKeepalive = 25` — necessary since the probe is almost
   certainly behind a home NAT with no port forwarding; without a
   keepalive the NAT mapping will time out and the server won't be able to
   re-initiate the connection).
5. `systemctl enable --now wg-quick@wg0`.
6. As defense in depth (the tunnel is already only reachable by the
   authenticated server peer, but a compromised server or a
   misconfiguration shouldn't hand over more than necessary): configure the
   probe's local firewall to only accept SSH on the `wg0` interface, nothing
   else. A couple of `ufw` rules are enough; don't over-engineer this.

Note the ordering dependency with Section 5.7: the probe needs the
server's public key (generated during server setup) *before* it can
render its own config, and the operator needs the probe's public key
*before* they can register it in Django and run `sync-peers.sh`. The
README's probe setup walkthrough must make this back-and-forth explicit
so the operator doesn't get stuck wondering what to paste where.

## 7. Ingestion API Contract

Document this formally in `docs/api-contract.md` as well as implementing
it; keep the two in sync.

`POST /api/v1/ingest`

Request headers (the client-cert-derived one is set by nginx, not the
probe):
```
Content-Type: application/json
X-Client-Cert-CN: <set by nginx from the verified client certificate>
```

Request body:
```json
{
  "probe_id": "b3f2c1a0-....",
  "timestamp": "2026-08-17T14:32:00Z",
  "readings": [
    {"sensor_type": "temperature_c", "value": 21.4},
    {"sensor_type": "humidity_pct", "value": 55.2}
  ],
  "health": {
    "cpu_temp_c": 48.1,
    "cpu_percent": 12.5,
    "mem_percent": 34.0,
    "disk_percent": 21.0,
    "uptime_seconds": 903421
  }
}
```

`probe_id` in the body must match `X-Client-Cert-CN` — reject with `403` if
they differ (a probe must not be able to report data under another probe's
identity even if it somehow had a valid cert).

Responses: `201` empty body on success; `400` malformed payload; `403`
CN/body mismatch or inactive probe; `404` unknown probe.

## 8. Setup Scripts

### 8.1 `server/scripts/setup.sh`

Idempotent where reasonably possible. Steps:
1. Check for Docker + Docker Compose plugin; fail with a clear message if
   missing (don't try to install Docker itself — that's out of scope and
   risky to automate blindly).
2. Prompt for (or accept as script arguments) the server's public IP.
3. Generate `.env` from `.env.example` if it doesn't exist, filling in a
   freshly generated Django `SECRET_KEY` and randomized default passwords
   where the example has placeholders.
4. Run `pki/generate-ca.sh` and `pki/generate-server-cert.sh <public-ip>` if
   the CA doesn't already exist.
5. Render `nginx.conf` from the template with the public IP.
6. `docker compose build && docker compose up -d`.
7. Wait for Postgres to be ready, then run Django migrations inside the
   `django` container.
8. Prompt to create a Django superuser (interactive `createsuperuser`, or
   accept `--noinput` env-based creation for automation).
9. Migrations from step 7 already create the extension, hypertables, and
   compression/retention policies — this step is just a sanity check:
   query `timescaledb_information.hypertables` and print a confirmation
   that both hypertables exist, so a silently-failed migration doesn't go
   unnoticed.
10. Run `wireguard/generate-server-keys.sh` if the server doesn't already
    have a WireGuard keypair, then bring up `wg-quick up wg0` (installing
    `wireguard-tools` first if missing). Remind the operator, loudly, that
    the WireGuard UDP port needs to be opened in their firewall/security
    group — this is easy to miss since nothing fails locally when it's
    closed, it just silently doesn't work from outside.
11. Print a summary: URLs for Grafana and Django Admin, the server's
    WireGuard public key (the operator will need to hand this to every
    probe), and the next steps (generate a probe cert with
    `pki/generate-probe-cert.sh`, then register it in Django Admin
    including its WireGuard details once the probe side has been set up).

### 8.2 `probe/scripts/setup.sh`

1. Check Python 3 version; create a virtualenv; install
   `probe/requirements.txt`.
2. Prompt for: probe name/UUID (or generate a UUID if not provided),
   hardware type (from a fixed menu matching the Django model's choices —
   keep these two lists in sync, ideally by documenting the exact allowed
   values in both places), server public IP.
3. Copy `probe.example.yaml` to `probe.yaml` and fill in the values from
   step 2.
4. Prompt the operator to confirm they've copied the client cert/key/CA
   cert into place (generated on the server side via
   `generate-probe-cert.sh` and `scp`'d over) — check the expected files
   exist at the configured paths and fail with a clear message naming the
   missing file(s) if not.
5. Install the systemd unit (`weathernet-probe.service`) from the template,
   substituting the actual install path and venv path.
6. `systemctl enable --now weathernet-probe`.
7. Run the WireGuard setup from Section 6.7: install `wireguard-tools`,
   generate the probe's keypair, prompt for the server's WireGuard public
   key + this probe's assigned tunnel IP, render and install `wg0.conf`,
   `systemctl enable --now wg-quick@wg0`. Print the probe's public key
   clearly with the instruction to paste it into Django Admin and then run
   `wireguard/sync-peers.sh` on the server.
8. Print how to tail logs (`journalctl -u weathernet-probe -f`) and how to
   check spool status.

## 9. Deploy Scripts

### 9.1 `server/scripts/deploy.sh`

`git pull` → `docker compose build` → run any new Django migrations inside
the running `django` container → `docker compose up -d` (recreates only
changed services) → print the current git commit hash that's now deployed.

### 9.2 `probe/scripts/deploy.sh`

`git pull` → reinstall `requirements.txt` into the existing venv (pip will
no-op on unchanged deps) → `systemctl restart weathernet-probe` → tail the
last few log lines so the operator can see it came back up cleanly.

Both deploy scripts should refuse to run if there are uncommitted local
changes in the working tree that would be silently overwritten by `git
pull` — check `git status --porcelain` and abort with a clear message.

## 10. README Requirements

The top-level `README.md` must include, in this order:

1. One-paragraph project description (what this is, for someone who has
   never seen the conversation that led to it).
2. Architecture summary — can reuse/adapt the diagram from Section 3.
3. Prerequisites for the server (Docker, a VM with a public IP, open ports
   for `443` (nginx), Grafana's port, and the WireGuard UDP port).
4. Prerequisites for the probe (Raspberry Pi running Raspberry Pi OS,
   Python 3, network reachability to the server).
5. Step-by-step server setup, including generating the CA and server cert,
   generating the server's WireGuard keys, running `setup.sh`, and where to
   find the Grafana/Django Admin URLs afterward.
6. Step-by-step probe setup, including how to generate and transfer that
   probe's client certificate from the server side, the WireGuard key/IP
   back-and-forth described at the end of Section 6.7, and running the
   probe's `setup.sh`.
7. How to add a second probe (should mostly be "repeat the probe steps with
   a new name/UUID and the next free WireGuard tunnel IP" — call this out
   explicitly since it's the multi-probe story for v1).
8. How to deploy an update on each side.
9. How to add a real sensor driver later (point at
   `sensors/base.py`/`registry.py`, describe the two files someone would
   need to touch — this is documentation for future work, not a task for
   this version).
10. How to reach a probe remotely for troubleshooting (SSH into the
    server, then to the probe's WireGuard tunnel IP), and how to run
    `wireguard/sync-peers.sh` after registering or changing a probe.
11. Known limitations of v1 (link to or restate Section 12 below).
12. Troubleshooting basics: checking probe logs, checking nginx logs for
    mTLS handshake failures, checking Django logs for rejected ingests, and
    checking `wg show` on both the server and the probe for WireGuard
    handshake status.

## 11. Non-Functional Requirements

- All code, comments, docstrings, commit messages, and documentation: US
  English.
- No secrets committed to git: `.env`, generated certs/keys, and anything
  under `pki/issued/` must be in `.gitignore` from the very first commit.
- Ship `.env.example` (server) and `probe.example.yaml` (probe) with
  placeholder values, never real ones.
- Logging over `print()` on both sides.
- A README per top-level component (`server/README.md`,
  `probe/README.md`) covering component-specific detail is welcome in
  addition to the top-level one, but don't split the essential
  getting-started steps across so many files that they're hard to follow —
  the top-level README should be sufficient on its own to go from zero to
  a working probe reporting data.
- Basic tests are expected for the probe's sensor registry (mock sensors
  return values in the expected shape) and the Django ingestion view
  (accepts valid payloads, rejects unknown/inactive probes, rejects
  CN/body mismatches). Full test coverage is not a v1 goal.

## 12. Explicitly Out of Scope for v1

Call these out in the README's "known limitations" section too, so nobody
mistakes an intentional cut for an oversight:

- **Real sensor drivers** (BME680, SPS30, wind vane/anemometer via
  MCP3008, rain gauge). v1 ships a pluggable framework with mock sensors
  only. Adding real drivers is future work and should slot into the
  existing `sensors/` structure without touching the transport or
  reporting logic.
- **Arduino / non-Linux probes.** v1 targets Raspberry Pi only. The sensor
  registry and probe config format are designed so that a future Arduino
  integration (most likely as a serial-connected peripheral behind a Pi
  acting as a gateway, given Arduino can't reasonably run this mTLS client
  itself) can be added as a new sensor plugin type rather than a rewrite —
  but no Arduino-specific code exists yet.
- **Automated certificate enrollment/rotation.** Certs are generated and
  copied to probes manually via scripts, with no expiry/rotation
  automation. Fine for a handful of probes; would need real design work
  (e.g. a short-lived bootstrap token exchanged for a cert) before this
  scales past a handful of devices.
- **Automatic WireGuard peer sync.** Running `wireguard/sync-peers.sh` is a
  manual step after registering or editing a probe's WireGuard fields in
  Django Admin — there's no signal/hook that does it automatically on
  save. Wiring that up (carefully — it means an unprivileged web process
  triggering a privileged host-level network change) is reasonable v2
  work, not a v1 requirement.
- **The operator's own device as a WireGuard peer.** v1 treats the server
  as the only bastion into the WireGuard subnet; the operator reaches
  probes by SSHing into the server first. Adding the operator's laptop/
  phone as its own peer (for direct access without that hop) is a small,
  independent addition later — same registration pattern, just one more
  `[Peer]` entry that isn't a `Probe` row.
- **Alerting.** Grafana can be configured with alert rules manually by the
  operator later; none are provisioned automatically in v1.
- **HTTPS with a browser-trusted certificate for Django Admin / Grafana.**
  There's no DNS name yet, so no Let's Encrypt. These UIs are reachable
  over plain HTTP or a self-signed cert for now — treat the server as
  trusted-network-only from an admin-access standpoint until a domain
  exists. Note this plainly in the README so it isn't mistaken for
  negligence later.
- **High availability / scaling.** Single server VM, single
  Postgres/TimescaleDB instance handling both relational and time-series
  data. No clustering, no failover, no read replicas.

## 13. Suggested Build Order

A reasonable sequence for implementing this (adjust as needed, but doing
the PKI scripts before anything that depends on mTLS working will save
time):

1. Repository scaffolding + `.gitignore` + this spec committed.
2. PKI scripts (`generate-ca.sh`, `generate-server-cert.sh`,
   `generate-probe-cert.sh`) — test them standalone with `openssl` before
   wiring anything else to them.
3. Django project + `probes` app + Admin, backed by Postgres, all in Docker
   Compose (without nginx/mTLS yet — expose Django directly for local
   testing at this stage).
4. `telemetry` app + ingestion endpoint, writing to the hypertables
   (including the migrations that create the extension, hypertables, and
   policies from Section 5.3). Test with plain `curl` and a
   manually-inserted `Probe` row before adding mTLS.
5. Add nginx in front of Django with mTLS termination; confirm the
   `X-Client-Cert-CN` header arrives correctly; re-test the ingestion flow
   through nginx instead of directly.
6. Grafana provisioning (datasource + example dashboard).
7. Probe: config loading, mock sensors, health metrics, then the mTLS
   transport client and spool, then the systemd-managed main loop.
8. WireGuard: server-side keygen + `wg-quick up`, the
   `generate_wireguard_peers` management command, `sync-peers.sh`, and the
   probe-side setup steps from 6.7. Treat this as independent of and
   separable from steps 1–7 — it shares no code with the telemetry path,
   so it can slot in whenever convenient, including in parallel with other
   work.
9. Setup and deploy scripts on both sides, tested against a clean VM and a
   clean Raspberry Pi respectively.
10. README.

---

If anything in this spec is ambiguous once you're actually implementing
it, prefer the option that keeps the sensor plugin interface and the
probe/server data contract (Section 7) stable — those are the two things
most likely to be extended in a v2, and it's cheaper to get them right now
than to migrate them later.
