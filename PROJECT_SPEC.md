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
┌────────────────────────────────────────────────────┐
│ Probe                                              │
│ (Raspberry Pi, systemd service, not containerized) │
└────────────────────────────────────────────────────┘
  │
  │  mTLS client cert over HTTPS/443
  ▼
┌────────────────────────────────────────────────────────┐
│ nginx                                                  │
│ (TLS termination, verifies client cert against the CA) │
└────────────────────────────────────────────────────────┘
  │
  │  verified cert CN forwarded as X-Client-Cert-CN
  ▼
┌───────────────────────────────────────────┐
│ django                                    │
│ (checks probe registry, writes telemetry) │
└───────────────────────────────────────────┘
  │
  │  writes
  ▼
┌─────────────────────────────────────────────┐
│ postgres + timescaledb extension            │
│ registry tables + sensor/health hypertables │
└─────────────────────────────────────────────┘
  │
  │  reads
  ▼
┌─────────┐
│ grafana │
└─────────┘

All boxes except "Probe" run inside the Server VM (public IP, no DNS).
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

**A third path, used exactly once per probe: enrollment.** A brand-new
probe has no client certificate yet, so it obviously can't use mTLS to
bootstrap itself — that's a chicken-and-egg problem every enrollment
scheme has to solve somehow. WeatherNet solves it with a short-lived,
single-use token instead of a permanent credential; the full design is in
Section 5.7. The enrollment endpoint and Django Admin share nginx's port
443 with the ingestion endpoint but, unlike ingestion, don't require a
client certificate at all — nginx is configured with `ssl_verify_client
optional_no_ca` at the server level (accept the TLS connection either way,
without hard-rejecting even a *presented*-but-invalid cert, which plain
`optional` would still do), and it's the `/api/v1/ingest` location block
specifically — not Django, and not nginx's SSL layer globally — that
explicitly checks `$ssl_client_verify` and rejects there if it isn't
`SUCCESS`, before ever proxying to Django. See Section 5.1 for exactly why
`optional_no_ca` is the correct directive here and `optional`/`on` are not,
and Section 7 for how the two endpoints differ.

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
│   │   ├── probes/                 # app: registry + enrollment
│   │   │   ├── models.py            # Probe, EnrollmentToken
│   │   │   ├── admin.py             # includes the "generate token" flow
│   │   │   ├── views.py             # POST /api/v1/enroll
│   │   │   ├── serializers.py
│   │   │   ├── ca.py                # CSR signing helpers (uses `cryptography`)
│   │   │   ├── wireguard.py         # tunnel IP allocation + peer rendering
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
│   │   ├── generate-ca.sh           # run once at server setup
│   │   └── generate-server-cert.sh  # run once at server setup
│   └── wireguard/
│       ├── generate-server-keys.sh  # run once at server setup
│       ├── sync-peers.sh            # run periodically by a host-level timer
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
│   │   └── weathernet-probe.service # systemd unit template
│   ├── requirements.txt
│   ├── scripts/
│   │   ├── setup.sh                 # takes --server / --token / --fingerprint
│   │   ├── enroll.py                # does the actual key/CSR gen + API call
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
  only reachable from `nginx` over the compose-internal network. Has the
  CA's certificate **and private key** bind-mounted read-only from
  `server/pki/` (needed to sign CSRs during enrollment — see 5.5/5.7; this
  is the one meaningful new attack-surface trade-off introduced by
  auto-enrollment, and it's called out again there deliberately).
- `grafana` — published on its own host port (e.g. `3000`) for the
  operator to view dashboards directly by IP. Pre-provisioned with a
  PostgreSQL datasource pointed at the same `postgres` service, and one
  example dashboard on first boot (see 5.4).
- `nginx` — the only service handling the mTLS ingestion path, and also the
  sole entry point on `443` for Django Admin (proxied straight through,
  without a client-certificate requirement -- Django's own login is the
  trust boundary there). Terminates TLS with the server's certificate.

  mTLS enforcement for `/api/v1/` is done explicitly in that location
  block, not by nginx's SSL layer automatically: `ssl_verify_client` must
  be `optional_no_ca`, not `optional` or `on` -- plain `optional` still
  hard-rejects (with nginx's own canned response, before any location/`if`
  logic runs) any *presented* certificate that fails verification, which
  would also break serving `/admin/` unauthenticated on the same port.
  Reject with ordinary status codes checked against `$ssl_client_verify` /
  `$ssl_client_s_dn` -- not nginx's own `495`/`496`: those are internal
  pseudo-codes tied to nginx's automatic SSL enforcement, and an explicit
  `return 495`/`496` gets silently replaced with nginx's canned `400`
  page instead of actually being sent. There is also no built-in nginx
  variable for just the CN of the client certificate's subject; extract
  it from `$ssl_client_s_dn` with a regex `map` block. On success, forward
  the verified CN in a header (e.g. `X-Client-Cert-CN`) to `django`.

All services on one Docker network, defined in `docker-compose.yml`. Use
named volumes for `postgres` and `grafana` data so `docker compose down`
doesn't destroy data (only `down -v` should).

Note: WireGuard (Section 5.8) is deliberately **not** one of these
services. Creating a network interface (`wg0`) from inside a container
means either `--privileged` or `NET_ADMIN` plus host networking mode —
extra complexity for no real benefit here. It runs directly on the VM host
using the standard `wg-quick` tooling, alongside the Docker stack rather
than inside it.

### 5.2 Django app design

Two apps:

**`probes`** — the registry, and now also enrollment.

`Probe` model fields:
- `id` — UUID, primary key, **generated by Django when a token is
  redeemed** (not chosen by the operator or the probe). This value becomes
  the client certificate's CN.
- `name` — human-readable label, set by the operator when they create the
  enrollment token (see below), copied onto the `Probe` row when it's
  created.
- `hardware_type` — choices: `raspberry_pi_3`, `raspberry_pi_4`,
  `raspberry_pi_5`, `generic_linux` (leave room to add `arduino` later
  without a migration headache — use a `TextChoices` class, not a hardcoded
  list scattered around). Also set at token-creation time; the probe's
  enrollment script auto-detects its own hardware (Section 5.7) and sends
  it along in the enroll request purely as a sanity check against what the
  operator declared — log a warning on mismatch, don't hard-fail on it.
- `location` — free text, optional.
- `notes` — free text, optional.
- `wireguard_public_key` — text, nullable. Set automatically when the
  probe's enrollment request is processed (Section 5.7) — never entered by
  hand.
- `wireguard_tunnel_ip` — nullable, unique when set (e.g.
  `GenericIPAddressField`). Allocated automatically at enrollment time
  (Section 5.7, `wireguard.py`) — the next free address in
  `WIREGUARD_SUBNET` that isn't already assigned to another probe. This is
  *not* the probe's home/public IP — that's dynamic and irrelevant here,
  since the probe always initiates the tunnel outward.
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

`EnrollmentToken` model fields:
- `token_hash` — the SHA-256 hex digest of the raw token. **Never store the
  raw token** — same principle as password storage. The raw value is shown
  to the operator exactly once, at creation time, and is unrecoverable
  after that (if lost before use, the operator just generates a new one;
  tokens are free).
- `probe_name`, `hardware_type` — what the operator wants the resulting
  probe to be called and what kind of device it's for. Set at creation
  time, copied onto the `Probe` row when the token is redeemed.
- `created_at`, `expires_at` — short default lifetime (e.g. 30 minutes,
  configurable via `ENROLLMENT_TOKEN_TTL_MINUTES` in `.env`). Long enough
  for an operator to run one command, short enough that a token nobody got
  around to using stops being a live credential quickly.
- `used_at` — nullable; null means still redeemable. Set atomically (inside
  a `select_for_update()` transaction — see Section 5.7) the moment a token
  is redeemed, so a token cannot be used twice even under a race.
- `resulting_probe` — nullable FK to `Probe`, set once redeemed, so the
  admin can see which probe came from which token.

Full design for token generation and redemption is in Section 5.7 — it
touches enough (CSR signing, WireGuard IP allocation, the admin UX) that it
gets its own section rather than being buried here.

Register `Probe` in `admin.py` with a sensible `list_display` (name,
hardware_type, is_active, last_seen_at, wireguard_tunnel_ip) and
`list_filter` on `hardware_type` / `is_active`. Register `EnrollmentToken`
too, with `used_at` and `resulting_probe` in `list_display` so the operator
can see at a glance which tokens are still live. Beyond the token-creation
behavior described in 5.7, these stay close to stock `ModelAdmin` — no
custom templates.

**`telemetry`** — the ingestion endpoint.

- `POST /api/v1/ingest` — the endpoint probes call on every reporting
  cycle. See [Section 7.2](#72-post-apiv1ingest--every-reporting-cycle-mtls-authenticated) for the payload schema.
- Certificate validity is already enforced by nginx's own `/api/v1/ingest`
  location block (Section 5.1) before the request ever reaches Django — by
  the time this view runs, a valid client cert is guaranteed to have been
  presented. The view trusts the forwarded `X-Client-Cert-CN` header
  **only** because Django is not reachable except through nginx (document
  this trust boundary clearly in a code comment at the top of the view —
  this is a security-relevant assumption resting on that nginx
  configuration, not an implementation detail Django re-derives on its
  own).
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

The root of trust still has to be bootstrapped manually somewhere — that
part doesn't change. What changes from a naive design is everything
*downstream* of the CA: per-probe certificates are now issued automatically
through the enrollment flow (Section 5.7), not via a script the operator
runs by hand for every probe.

- `pki/generate-ca.sh` — run once at server setup. Creates a self-signed
  internal CA (key + cert). This CA's only job is signing probe client
  certs and the server cert; it is never meant to be trusted by browsers.
- `pki/generate-server-cert.sh` — generates the server's TLS cert/key,
  signed by the internal CA, **with the server's public IP as a Subject
  Alternative Name** (there is no DNS name to use — the script must take
  the IP as an argument and fail loudly if it's not provided; don't assume
  `localhost` or guess). Also prints the certificate's SHA-256 fingerprint
  (`openssl x509 -noout -fingerprint -sha256 -in server.crt`) — the
  operator needs this once, to give to the enrollment script for TLS
  pinning (Section 5.7).
- Per-probe certificates no longer have a standalone script. Signing
  happens inside the `/api/v1/enroll` view (`probes/ca.py`), using the
  `cryptography` package to load the CA key/cert and sign whatever CSR the
  probe submitted, with the CN forced to the newly-generated `Probe` UUID
  regardless of what (if anything) the CSR requested — the server is
  authoritative for identity assignment here, not the probe.
- Document clearly in the README that:
  1. The CA's private key never leaves the server, but — new trade-off,
     state it plainly — it **is** now readable by the `django` container
     (read-only bind mount), because that's what signs CSRs during
     enrollment. This is a deliberate convenience/exposure trade-off, not
     an oversight; see Section 5.7 for the reasoning and Section 12 for
     what a more hardened version of this would look like.
  2. Probe private keys (both the mTLS client key and the WireGuard
     private key) are generated **on the probe** and never transmitted
     anywhere, over any channel, at any point. Only the CSR (public key)
     and the WireGuard public key travel to the server.

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
`ENROLLMENT_TOKEN_TTL_MINUTES` (default `30`), the server's own public IP
and WireGuard endpoint info (so the admin can print ready-to-use enrollment
commands — see 5.7), and Grafana admin user/password. `.env` itself must be
gitignored.

The server's own WireGuard keypair is **not** an env var — like the mTLS
CA, it's a generated file (`wireguard/server_private.key`,
`wireguard/server_public.key`), gitignored, with the private key never
leaving the host.

### 5.7 Zero-Touch Probe Enrollment

This is the mechanism that replaces manual UUID assignment, manual cert
issuance + `scp`, and manual WireGuard key exchange with a single
token-based exchange. It's the most security-sensitive piece of new
surface in the project, so read it carefully before implementing — the
shape matters more than the exact code.

**Generating a token (operator, in Django Admin):**

1. Operator opens `EnrollmentToken` in Django Admin and adds a new one,
   filling in `probe_name` and `hardware_type` (and optionally overriding
   the default expiry).
2. `admin.py` overrides `save_model()`: on create, generate a random,
   high-entropy token (`secrets.token_urlsafe(32)` or similar), store only
   its SHA-256 hash on the model, and — this is the only place the raw
   token ever exists outside the operator's terminal — surface it via
   `self.message_user(...)` on the resulting admin page, formatted as a
   ready-to-paste shell command:
   ```
   Token (shown once): 3f7a1c9e...
   Run on the probe (after `git clone`-ing this repo):
     ./probe/scripts/setup.sh \
       --server <server-public-ip> \
       --token 3f7a1c9e... \
       --fingerprint <server-cert-sha256-fingerprint>
   ```
   The server IP and fingerprint come from settings/`.env`, not typed by
   the operator each time.

**Redeeming a token (probe, one command):**

`probe/scripts/setup.sh --server ... --token ... [--fingerprint ...]`
does, via `probe/scripts/enroll.py`:

1. Installs OS packages (Python venv, `wireguard-tools`) if missing.
2. Auto-detects hardware type where possible (Raspberry Pi model is
   readable from `/proc/device-tree/model` or `/proc/cpuinfo` — use that
   to distinguish Pi 3/4/5; fall back to `generic_linux` if detection is
   inconclusive, don't guess wrong silently).
3. Generates the mTLS client keypair and a CSR **locally** (the private
   key never leaves this step). Generates the WireGuard keypair **locally**
   the same way.
4. If `--fingerprint` was given: opens a TLS connection to
   `https://<server>/`, reads the presented certificate's SHA-256
   fingerprint, and aborts loudly if it doesn't match before sending
   anything. If `--fingerprint` was omitted: proceeds without pinning, but
   prints a clear warning that it's doing so — this is a real reduction in
   the guarantee, not a silent one.
5. `POST /api/v1/enroll` with the token, the CSR (PEM), the WireGuard
   public key, and the detected hardware type. See Section 7 for the exact
   schema.
6. On success, writes everything the response contains to disk: the signed
   client cert, the CA cert, `probe.yaml` (assigned `probe_id`,
   `hardware_type`, `server_url`, cert/key paths, default
   `report_interval_seconds`), and `wg0.conf` (this probe's tunnel IP as
   `Address`, the private key just generated, one `[Peer]` block for the
   server using the server's WireGuard public key + endpoint from the
   response, `PersistentKeepalive = 25` since the probe is almost
   certainly behind a home NAT with no port forwarding).
7. Installs and starts both `weathernet-probe.service` and
   `wg-quick@wg0.service`.
8. Prints a plain confirmation: probe ID, tunnel IP, and "you should see
   this probe appear in Grafana within `report_interval_seconds`, and it
   should be SSH-reachable at `<tunnel-ip>` from the server within about a
   minute" (see the sync timer note below).

**Server-side enrollment view (`probes/views.py`, `POST /api/v1/enroll`):**

1. Hash the incoming token, look up `EnrollmentToken` by `token_hash`
   inside `transaction.atomic()` with `select_for_update()` — this makes
   the "is it unused and unexpired" check and the "mark it used" write
   atomic, so a token can't be redeemed twice via a race (e.g. the probe's
   script retries a request that actually succeeded server-side but whose
   response got lost).
2. Reject with `404` if no token matches the hash, `410` if it's expired
   or already used — distinct codes so the probe-side script can print a
   sensible message instead of a generic failure.
3. Generate a new `Probe` UUID, sign the submitted CSR via `probes/ca.py`
   (loads the CA key/cert from the mounted `pki/` files, sets the CN to the
   new UUID regardless of what the CSR requested, sets a reasonable
   validity period — years, not the token's own short lifetime, since
   there's no rotation flow yet per Section 12), and allocate the next free
   `wireguard_tunnel_ip` in `WIREGUARD_SUBNET` via `probes/wireguard.py`
   (query existing non-null tunnel IPs, pick the lowest unused one, `409`
   if the subnet is exhausted rather than silently colliding).
4. Create the `Probe` row with all of the above plus the submitted
   `wireguard_public_key`.
5. Mark the token `used_at = now()`, `resulting_probe = <the new probe>`.
6. Respond `201` with: the signed client cert (PEM), the CA cert (PEM),
   the assigned `probe_id`, the assigned `wireguard_tunnel_ip`, the
   server's WireGuard public key, and the server's WireGuard endpoint
   (`<public-ip>:<port>`). See Section 7 for the exact JSON shape.

**Applying the new WireGuard peer:** the enrollment view runs inside the
`django` container and, same constraint as always, has no host-level
network privileges to run `wg syncconf` itself. Rather than trying to
bridge that gap from an unprivileged web process (which would mean either
running Django as a more privileged process or building some kind of
privileged helper socket — real complexity for marginal benefit here), a
plain **host-level systemd timer runs `wireguard/sync-peers.sh` once a
minute**. A newly enrolled probe's peer becomes active within that window
without the operator doing anything — this also quietly closes the
"automatic peer sync" gap that a purely-manual design would have left
open, without needing to give the web app privileged host access to do it
synchronously.

**Why the CA key living in the `django` container is an acceptable
trade-off here, stated plainly:** the alternative is either (a) keep
issuance fully manual, which is the exact friction this section exists to
remove, or (b) build a separate, minimal signing service that Django calls
over some other channel — strictly better isolation, but real additional
infrastructure for a project whose entire premise is "a handful of home
weather stations." For v1, mounting the key read-only into a container
that's itself not directly internet-reachable (only nginx is) is a
reasonable point on that trade-off curve. It is still a trade-off, not a
non-issue — Section 12 revisits what a hardened version would look like.

### 5.8 Remote Access via WireGuard (Ongoing Operation)

Enrollment (5.7) is how a probe *gets* WireGuard access; this section is
about the tunnel once it exists — see the diagram and rationale in
Section 3.

**Server side, once, at server setup time (unchanged from before):**

1. `wireguard/generate-server-keys.sh` generates the server's keypair.
   The public key ends up in `.env`/settings so the enrollment response
   (5.7) and the admin's printed setup command can both reference it
   without a human copying it around.
2. `wg-quick up wg0` is brought up on the host directly (not in Docker —
   see the note in 5.1), listening on `WIREGUARD_LISTEN_PORT`, with its own
   tunnel address `10.10.0.1` (the first address in `WIREGUARD_SUBNET`).
3. The VM's firewall / cloud security group needs the WireGuard UDP port
   opened, in addition to `443` for nginx and Grafana's port — call this
   out explicitly in the setup script's summary output, since it's easy to
   forget and the failure mode (tunnel silently doesn't come up) is
   confusing to debug.
4. Install the systemd timer that runs `wireguard/sync-peers.sh` every
   minute (a small `.timer` + `.service` unit pair, or a root crontab entry
   if you'd rather skip the extra unit files — either is fine, pick
   whichever the setup script can install more simply).

**Peer configuration, maintained automatically by `sync-peers.sh`:** each
peer stanza's `AllowedIPs` is that probe's tunnel IP as a single `/32` —
not the whole subnet. WireGuard doesn't bridge peers to each other unless
you configure it to; keeping each `AllowedIPs` scoped to one `/32` means
this stays strictly hub-and-spoke (server ↔ each probe), with no probe able
to reach another probe through the server even if it wanted to. The
`generate_wireguard_peers` management command includes **inactive** probes
in the peer list too — `is_active` on `Probe` gates *telemetry ingestion*,
not SSH reachability. A probe you've deactivated because its sensors are
misbehaving is exactly the probe you most want to still be able to SSH
into. Don't wire these two flags together.

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

`probe.yaml` — written automatically by `probe/scripts/enroll.py` at the
end of the enrollment flow (Section 5.7), not hand-templated by the
operator. Contains: `probe_id` (assigned by the server), `hardware_type`,
`server_url` (built from the `--server` argument), paths to the client
cert/key/CA cert (also written by `enroll.py`), `report_interval_seconds`
(a sane default; the operator can edit it after the fact), and the list of
enabled sensor names matching entries in `sensors/registry.py` (default:
all the mock sensors, so there's something to see in Grafana immediately).

A `config/probe.example.yaml` documenting the schema is still worth
shipping for reference/manual editing later, but it is not part of the
normal setup path anymore.

### 6.7 WireGuard setup on the probe

Folded into `probe/scripts/enroll.py` as of Section 5.7 — there's no
separate manual WireGuard step anymore. For reference, what the script
does at the OS level (no new code under `weathernet_probe/`, just
`wireguard-tools` and a config file):

1. Install `wireguard-tools` via the system package manager, if missing.
2. Generate the keypair locally as part of the same step that generates
   the mTLS CSR (Section 5.7) — the private key never leaves the device,
   permissions locked down to root-readable-only.
3. Once the enrollment response comes back with the assigned tunnel IP,
   the server's WireGuard public key, and the server's endpoint, render
   `/etc/wireguard/wg0.conf`: `Address` = this probe's assigned tunnel IP,
   `PrivateKey` = the one generated in step 2, one `[Peer]` block for the
   server (`AllowedIPs` = the server's tunnel IP as a `/32`,
   `PersistentKeepalive = 25` — necessary since the probe is almost
   certainly behind a home NAT with no port forwarding; without a
   keepalive the NAT mapping will time out and the server won't be able to
   re-initiate the connection).
4. `systemctl enable --now wg-quick@wg0`.
5. As defense in depth (the tunnel is already only reachable by the
   authenticated server peer, but a compromised server or a
   misconfiguration shouldn't hand over more than necessary): configure the
   probe's local firewall to only accept SSH on the `wg0` interface, nothing
   else. A couple of `ufw` rules are enough; don't over-engineer this.

## 7. API Contract

Document this formally in `docs/api-contract.md` as well as implementing
it; keep the two in sync. Two endpoints, with different trust models —
see Section 5.1 for how nginx handles the difference at the TLS layer.

### 7.1 `POST /api/v1/enroll` — one-time, unauthenticated by cert

No client certificate involved (the probe doesn't have one yet). Trust
comes entirely from the token.

Request body:
```json
{
  "token": "3f7a1c9e-...-raw-token-value",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...",
  "wireguard_public_key": "base64-wg-pubkey==",
  "detected_hardware_type": "raspberry_pi_4"
}
```

Response `201`:
```json
{
  "probe_id": "b3f2c1a0-....",
  "client_cert_pem": "-----BEGIN CERTIFICATE-----\n...",
  "ca_cert_pem": "-----BEGIN CERTIFICATE-----\n...",
  "server_url": "https://<server-public-ip>",
  "wireguard": {
    "tunnel_ip": "10.10.0.5",
    "server_public_key": "base64-server-wg-pubkey==",
    "server_endpoint": "<server-public-ip>:51820"
  },
  "report_interval_seconds": 60
}
```

Responses: `201` as above on success; `400` malformed payload or invalid
CSR; `404` token hash doesn't match any token; `410` token expired or
already used; `409` WireGuard subnet exhausted (extremely unlikely at this
project's scale, but handle it instead of crashing).

### 7.2 `POST /api/v1/ingest` — every reporting cycle, mTLS-authenticated

By the time a request reaches this view, nginx's `/api/v1/ingest` location
block has already rejected anything without a valid client certificate
(Section 5.1) — this endpoint only ever sees pre-authenticated requests.

Request headers (set by nginx, not the probe — see Section 5.1):
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
   the CA doesn't already exist. Ensure the resulting key files have
   permissions that allow the `django` container's bind mount to read them
   (Section 5.1) without being world-readable on the host.
5. Render `nginx.conf` from the template with the public IP, including the
   `ssl_verify_client optional_no_ca` setting and the `/api/v1/ingest`
   location block's explicit `$ssl_client_verify` check, both from
   Section 5.1.
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
    `wireguard-tools` first if missing). Install the systemd timer that
    runs `wireguard/sync-peers.sh` every minute (Section 5.8). Remind the
    operator, loudly, that the WireGuard UDP port needs to be opened in
    their firewall/security group — this is easy to miss since nothing
    fails locally when it's closed, it just silently doesn't work from
    outside.
11. Print a summary: URLs for Grafana and Django Admin, and a reminder of
    the enrollment flow — "to add a probe, create an `EnrollmentToken` in
    Django Admin; it will print the exact command to run on the probe."

### 8.2 `probe/scripts/setup.sh`

Dramatically shorter than a manual-credentials design would need, by
design — see Section 5.7.

1. Parse `--server <ip>`, `--token <token>`, optional `--fingerprint
   <sha256>`. Fail with a clear usage message if `--server` or `--token`
   is missing; warn (but don't fail) if `--fingerprint` is missing, per
   Section 5.7's note on what that trade-off actually means.
2. Check Python 3 version; create a virtualenv; install
   `probe/requirements.txt`; install `wireguard-tools` via the system
   package manager if missing.
3. Run `probe/scripts/enroll.py` with the parsed arguments — this does
   everything described in Sections 5.7 and 6.7: hardware detection, local
   key/CSR generation, optional TLS fingerprint pinning, the
   `/api/v1/enroll` call, and writing `probe.yaml`, the cert files, and
   `wg0.conf` to their final locations. Abort with whatever clear error
   `enroll.py` produced (expired token, fingerprint mismatch, etc.) rather
   than swallowing it.
4. Install the systemd unit (`weathernet-probe.service`) from the template,
   substituting the actual install path and venv path.
5. `systemctl enable --now weathernet-probe` and
   `systemctl enable --now wg-quick@wg0`.
6. Print how to tail logs (`journalctl -u weathernet-probe -f`) and how to
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
6. Step-by-step probe setup: generate an enrollment token in Django Admin,
   `git clone` the repo onto the probe, run the one `setup.sh` command
   Django printed. Emphasize that this is genuinely one command on the
   probe side — don't let this section accidentally re-introduce the
   manual back-and-forth the design was meant to eliminate.
7. How to add a second probe (should be exactly the same steps as 6 —
   generate another token, run `setup.sh` again with the new token — call
   this out explicitly since it's the multi-probe story for v1 and there's
   no per-probe manual bookkeeping left to describe).
8. How to deploy an update on each side.
9. How to add a real sensor driver later (point at
   `sensors/base.py`/`registry.py`, describe the two files someone would
   need to touch — this is documentation for future work, not a task for
   this version).
10. How to reach a probe remotely for troubleshooting (SSH into the
    server, then to the probe's WireGuard tunnel IP — should be live within
    about a minute of enrollment via the sync timer, Section 5.8).
11. Known limitations of v1 (link to or restate Section 12 below).
12. Troubleshooting basics: checking probe logs, checking nginx logs for
    mTLS handshake failures, checking Django logs for rejected ingests or
    failed enrollments, and checking `wg show` on both the server and the
    probe for WireGuard handshake status.

## 11. Non-Functional Requirements

- All code, comments, docstrings, commit messages, and documentation: US
  English.
- No secrets committed to git: `.env`, the CA/server key files under
  `pki/`, and the WireGuard key files under `wireguard/` must be in
  `.gitignore` from the very first commit. There is no longer a
  `pki/issued/` directory to worry about — per-probe key material is
  generated on the probe itself and never touches the server's filesystem
  as a file (it only ever exists as a request/response body in memory
  during the enrollment call).
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
  return values in the expected shape), the Django ingestion view (accepts
  valid payloads, rejects unknown/inactive probes, rejects CN/body
  mismatches), and the enrollment view (accepts a valid unused token,
  rejects an expired one, rejects a reused one, and — worth a dedicated
  test — actually exercises the race condition the `select_for_update()`
  locking is meant to prevent, e.g. via two near-simultaneous requests in a
  test). Full test coverage is not a v1 goal.

## 12. Explicitly Out of Scope for v1

Call these out in the README's "known limitations" section too, so nobody
mistakes an intentional cut for an oversight:

- **Certificate rotation/revocation.** Enrollment (Section 5.7) solves
  *issuance*, not the full lifecycle — issued certs get a long validity
  period and there's no renewal flow, no revocation list (CRL/OCSP), and no
  way to force a probe to re-enroll short of manually deleting its `Probe`
  row (which stops ingestion but doesn't actually invalidate the cert
  cryptographically — nginx would still terminate a TLS handshake using
  it, Django would just 404 on the unknown/deleted probe when it tried to
  post data). Fine for a handful of long-lived home devices; would need
  real design work before this matters at any real scale.
- **Rate limiting / abuse protection on `/api/v1/enroll`.** The token
  itself is the only defense against someone hammering the endpoint
  guessing tokens. At 32 bytes of entropy that's not a practical brute-force
  target, but there's no request throttling, and a expired/used token
  currently just returns a normal `410`/`404` with no backoff. Worth
  adding (e.g. `django-ratelimit` on the view) before this is exposed
  beyond a small trusted set of devices.
- **A more isolated CSR-signing setup.** As discussed in Section 5.7, the
  CA private key is mounted into the `django` container so enrollment can
  sign CSRs synchronously. A more hardened version would move signing into
  a separate, minimal process with a narrower blast radius if the web app
  is ever compromised — not necessary at this project's scale, but the
  honest next step if that trade-off ever stops feeling comfortable.
- **The operator's own device as a WireGuard peer.** v1 treats the server
  as the only bastion into the WireGuard subnet; the operator reaches
  probes by SSHing into the server first. Adding the operator's laptop/
  phone as its own peer (for direct access without that hop) is a small,
  independent addition later — same registration pattern, just one more
  `[Peer]` entry that isn't tied to a `Probe` row.
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
2. PKI scripts (`generate-ca.sh`, `generate-server-cert.sh`) — test them
   standalone with `openssl` before wiring anything else to them. No more
   `generate-probe-cert.sh` in this version — per-probe issuance now lives
   in the enrollment view (step 6 below).
3. Django project + `probes` app (just the `Probe` model + Admin for now)
   + Admin, backed by Postgres, all in Docker Compose (without nginx/mTLS
   yet — expose Django directly for local testing at this stage).
4. `telemetry` app + ingestion endpoint, writing to the hypertables
   (including the migrations that create the extension, hypertables, and
   policies from Section 5.3). Test with plain `curl` and a
   manually-inserted `Probe` row before adding mTLS.
5. Add nginx in front of Django with `ssl_verify_client optional_no_ca` and
   the explicit `$ssl_client_verify` check in the `/api/v1/ingest` location
   block (Section 5.1) — test this in isolation first: confirm nginx itself
   rejects a request with no client cert, and separately that
   `X-Client-Cert-CN` arrives correctly at Django for a request with a
   valid one; re-test the ingestion flow through nginx instead of directly.
6. Enrollment: `EnrollmentToken` model + the `save_model()` admin behavior,
   `probes/ca.py` (CSR signing with `cryptography`), `probes/wireguard.py`
   (tunnel IP allocation), and the `/api/v1/enroll` view tying them
   together. Test the whole loop with `curl` and a manually-generated CSR
   before writing `enroll.py` on the probe side — you want to know the
   server half works in isolation first.
7. Grafana provisioning (datasource + example dashboard).
8. Probe: config loading, mock sensors, health metrics, then the mTLS
   transport client and spool, then the systemd-managed main loop — all of
   this can be built and tested against a manually-inserted `Probe` row
   and manually-issued test cert, independent of the enrollment flow.
9. `probe/scripts/enroll.py` and the WireGuard side of things: server-side
   keygen + `wg-quick up`, the `generate_wireguard_peers` management
   command, `sync-peers.sh` and its timer, and the probe-side key
   generation + `wg0.conf` rendering. This is the piece that finally
   connects steps 6 and 8 end-to-end — a good point to do a full real
   enrollment test against a physical Raspberry Pi.
10. Setup and deploy scripts on both sides, tested against a clean VM and a
    clean Raspberry Pi respectively.
11. README.

---

If anything in this spec is ambiguous once you're actually implementing
it, prefer the option that keeps the sensor plugin interface and the
probe/server data contract (Section 7) stable — those are the two things
most likely to be extended in a v2, and it's cheaper to get them right now
than to migrate them later.
