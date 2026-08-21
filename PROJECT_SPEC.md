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

**A fourth path: a public, read-only API for an external page.**
Unlike the three paths above, this one is meant to be consumed from
*outside* the mTLS/admin trust boundary entirely — by the server-side
code of a page hosted on a completely different domain (e.g. a
PHP-only host), so that visitors never connect to the VM directly and
never see its self-signed certificate (Section 12 notes why the VM
itself has no browser-trusted cert in v1). `GET /api/v1/public/summary`
(current readings) and `GET /api/v1/public/history` (recent per-probe
time series, for charts) share nginx's port with `/api/v1/enroll` and
`/admin/` under a `/api/v1/public/` prefix location — no client
certificate required — but since neither has a per-request credential
of its own (a probe's enrollment token, an admin's session), both are
gated by the same static, shared API key (`PUBLIC_SUMMARY_API_KEY`,
checked in each view) and rate-limited at the nginx layer, since the
URL is otherwise reachable by anyone who finds it. `summary` returns
coordinates rounded to a coarser precision than what's stored
(`PUBLIC_LOCATION_PRECISION_DECIMALS`) and neither endpoint ever
includes `location_address` or owner contact fields — see Section 7.3/7.4.

The reference external page lives in `public-page/` at the repo root
(not part of the server or probe components — it does not run on the
VM at all, and is the only thing in this repo written in PHP rather
than Python). It does not call the VM directly: `public-page/sync.php`,
triggered by the *external host's* own cron (not this project's
`server`/`probe` components) every 10 minutes, is the only thing that
calls `/api/v1/public/summary` and `/api/v1/public/history` — it
upserts the result into a small MySQL table on that same external
host. `public-page/index.php`, what visitors actually load, reads only
from that MySQL table. This decouples visitor traffic from VM load
entirely: however many people load the page in a burst, the VM only
ever sees one request pair per cron interval, at a fixed and
predictable rate — the nginx rate limit above is a backstop, not the
thing actually controlling load in the common case. See
`public-page/README.md` for the full design and deploy steps.

`public-page/`'s own secrets (the API key above, MySQL credentials)
live in `config.php`, a `.php` file that `return`s an array — not a
plain-text `.env`. This matters specifically because `public-page/`
runs on arbitrary external PHP hosting this project doesn't control:
a `.env`'s confidentiality depends on the web server being Apache with
`AllowOverride` enabled for that directory and an `.htaccess` rule
having actually been uploaded correctly, none of which is guaranteed
on generic/budget hosting. A `.php` file needs none of that — any host
serving this page at all is, by definition, executing `.php` files
rather than serving their source, so a direct request to `config.php`
returns a blank page (the `return` ends the script before anything is
echoed), never the secrets. `.htaccess` still denies it directly
(`public-page/.htaccess`), but as defense in depth, not the thing
actually protecting it.

`public-page/index.php` is localized (Italian, English, French,
German — `public-page/i18n.php`). Locale priority: an explicit choice
from the flag dropdown in the page's top-right corner (a plain GET
`<form>`, `?lang=xx`, works with JavaScript disabled via a `<noscript>`
submit button) beats a remembered previous choice (`weathernet_lang`
cookie, written only when a request carries a fresh `?lang=` — never
on a plain page load) beats the visitor's `Accept-Language` header
beats English as the last-resort default. That cookie is the one
exception to `public-page/` otherwise setting no cookies at all — it's
first-party, stores nothing but the language the visitor just
explicitly picked, and exists for no purpose beyond honoring that
pick, which is the textbook "strictly necessary" exemption under
ePrivacy/GDPR (storage a user's own explicit action requested, not
tracking): it doesn't require cookie consent any more than the rest of
this page does. Translated strings needed by the map's marker popups
are passed from PHP into the page's `<script>` block the same way
`probes` itself is (`json_encode()`), so there's exactly one source of
truth (`i18n.php`) for every user-facing string, PHP-rendered or not.

**A fifth path: a Telegram bot for a daily weather digest.** Not part
of the API contract at all — this is a `subscriptions` Django app plus
a `telegram-bot` service that long-polls Telegram's Bot API
(`getUpdates`), so it needs no inbound connection to the VM whatsoever
(no webhook, and therefore no need for Telegram's servers to accept
the VM's self-signed certificate — see Section 12). Subscribing has no
web form either: a visitor opens `t.me/<bot>` (linked from
`public-page/`), presses Start, and the rest of the conversation —
giving a place name, `/list`/`/remove`/`/stop` — happens entirely
inside Telegram. Free text that isn't a command is geocoded via
Nominatim (OpenStreetMap's open geocoder, same ecosystem as the
project's map tiles — no API key) into coordinates, compared against
every active `Probe`'s location by great-circle distance, and saved as
a `WeatherSubscription` regardless of whether a probe is currently
close enough — Section 5.9 covers why, and what happens once one is.
A separate `send_daily_digest` management command, run once a day by a
systemd timer (same host-level pattern as WireGuard peer-sync,
Section 5.8), sends each qualifying subscription a summary of the
previous day's readings from whichever active probe is nearest. See
Section 5.9 for the full design.

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
│   │   │   ├── views.py             # POST /api/v1/enroll, GET /api/v1/public/summary
│   │   │   ├── serializers.py
│   │   │   ├── ca.py                # CSR signing helpers (uses `cryptography`)
│   │   │   ├── wireguard.py         # tunnel IP allocation + peer rendering
│   │   │   ├── aqi.py               # heuristic air quality score (shared with the Grafana panel's formula)
│   │   │   ├── management/
│   │   │   │   └── commands/
│   │   │   │       └── generate_wireguard_peers.py
│   │   │   └── migrations/
│   │   ├── telemetry/              # app: ingestion API + hypertable models
│   │   │   ├── views.py
│   │   │   ├── serializers.py
│   │   │   ├── models.py            # SensorReading / ProbeHealth (hypertables)
│   │   │   └── urls.py
│   │   ├── subscriptions/          # app: Telegram daily-digest bot (see 5.9)
│   │   │   ├── models.py            # WeatherSubscription
│   │   │   ├── bot.py               # command/conversation dispatch
│   │   │   ├── geocoding.py         # Nominatim wrapper
│   │   │   ├── matching.py          # nearest-active-probe lookup
│   │   │   ├── telegram_api.py      # thin Bot API wrapper (getUpdates/sendMessage)
│   │   │   ├── management/commands/
│   │   │   │   ├── telegram_bot_poll.py   # long-running: telegram-bot service's command
│   │   │   │   └── send_daily_digest.py   # run once a day by a systemd timer
│   │   │   └── migrations/
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── grafana/
│   │   └── provisioning/
│   │       ├── datasources/postgres.yml
│   │       └── dashboards/          # one example dashboard JSON
│   ├── scripts/
│   │   ├── setup.sh
│   │   ├── deploy.sh
│   │   ├── send-daily-digest.sh                    # docker compose exec wrapper -- see 5.9
│   │   └── weathernet-daily-digest.service/.timer  # installed by setup.sh once TELEGRAM_BOT_TOKEN is set
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
├── public-page/                      # NOT part of server/ or probe/, doesn't run on the VM --
│   ├── index.php                     # visitor-facing page -- reads MySQL only, never calls the VM
│   ├── i18n.php                      # it/en/fr/de strings, detect_locale(), see below
│   ├── sync.php                      # cron entrypoint -- the only thing here that calls the VM (see 7.3/7.4)
│   ├── db.php                        # tiny PDO connection helper, shared by the two scripts above
│   ├── config.php.example            # copy to config.php -- a .php file, not a .env, see below
│   ├── schema.sql                    # the one MySQL table this needs (probe_cache)
│   ├── .htaccess                     # blocks direct URL access to config.php / *.pem / sync.php
│   └── README.md
├── 3d-printing/                       # 3D-printable enclosure/mount designs -- see 11 for licensing
│   ├── README.md                     # file-format + per-part licensing convention
│   └── sensors_enclosure/            # MIT (inspired by, not derived from, a third-party CC design)
│       ├── README.md
│       ├── case.FCStd, case.step, case.stl, case.3mf
│       └── cover.FCStd, cover.step, cover.stl, cover.3mf
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
  auto-enrollment, and it's called out again there deliberately). Also has
  the VM's own SSH public key bind-mounted read-only, host path
  configurable via `.env` (see 5.6/5.7) — handed to a probe during
  enrollment so the server can SSH into it without a manual key exchange.

  Implementation note on the Dockerfile: `COPY . .` runs as root, before
  the image switches to a non-root user for the final `USER` directive.
  Without an explicit `chown` of the app directory to that user in
  between, the container can't write `staticfiles/` (`collectstatic`
  fails) or anything else at runtime — this bit a real deployment, budget
  for it explicitly rather than discovering it from a permission error.
- `telegram-bot` — same image as `django`, different command
  (`manage.py telegram_bot_poll`, see 5.9). Its own service rather than
  folded into `django`'s container: it needs to run forever independently
  of gunicorn's request/response lifecycle, and a crash/restart here
  shouldn't affect the API. No PKI/SSH bind mounts (unlike `django`) --
  it only ever calls out to Telegram's API and Nominatim, and reads/
  writes its own app's table. Not published to the host either
  direction: nothing calls in, it only calls out.
- `grafana` — published on its own host port (e.g. `3000`) for the
  operator to view dashboards directly by IP. Pre-provisioned with a
  PostgreSQL datasource pointed at the same `postgres` service, and one
  example dashboard on first boot (see 5.4).
- `nginx` — the only service handling the mTLS ingestion path, and also the
  sole entry point on `443` for Django Admin (proxied straight through,
  without a client-certificate requirement -- Django's own login is the
  trust boundary there). Terminates TLS with the server's certificate.

  mTLS enforcement for `/api/v1/ingest` is done explicitly in that
  location block — an **exact match** (`location = /api/v1/ingest`), not
  a prefix. This matters: `/api/v1/enroll` must fall through to the
  unauthenticated location instead (a brand-new probe has no certificate
  yet), and a prefix match on `/api/v1/` would incorrectly catch it too.
  Enforcement is not done by nginx's SSL layer automatically: `ssl_verify_client` must
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

  `GET /api/v1/public/summary` and `GET /api/v1/public/history`
  (Section 7.3/7.4) share one `location /api/v1/public/` **prefix**
  block — a prefix, not an exact match like `/api/v1/ingest`, on
  purpose: it's what lets a new `public/*` view get the same handling
  automatically, with no nginx change, the moment its Django route
  exists. Unauthenticated like `/api/v1/enroll` (Django's own API key
  check is the trust boundary there, not nginx) but additionally behind
  an nginx `limit_req_zone` — unlike every other unauthenticated path
  here, this one has no per-request credential of its own and is
  reachable by anyone who finds the URL, so the rate limit is a real
  second layer, not just defense-in-depth theater. In practice it's a
  backstop, not the main defense against a traffic burst: the reference
  external page (`public-page/`) only ever calls this from its own
  cron job, not per visitor — see Section 3's "fourth path".

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
- `location_address` — free text, optional. A human-readable description
  of where the probe physically is (e.g. "Garden, north side of the
  house"), not a structured postal address — free text is deliberately
  good enough here, this is for an operator's own reference, not
  geocoding.
- `location_latitude`, `location_longitude` — `DecimalField(max_digits=9,
  decimal_places=6)`, both nullable/optional, entered by the operator
  (not derived from anything the probe reports — the probe itself never
  knows its own GPS position in v1). Six decimal places is sub-meter
  precision, more than this project needs but a harmless, conventional
  choice; reject out-of-range values (`-90..90` / `-180..180`) at the
  form level rather than trusting free input.
- `owner_email`, `owner_phone` — free text (an `EmailField` for the
  former), both optional at the database level so this can be added
  without breaking already-enrolled probes, but treat `owner_email` as
  the primary contact and `owner_phone` as a secondary, genuinely
  optional one when designing the Admin form (e.g. field ordering,
  helper text) — who to actually contact about a probe that's gone
  quiet or is physically in someone's way.
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
  **Must** generate it with explicit `-addext "basicConstraints=critical,CA:true"`
  and `-addext "keyUsage=critical,keyCertSign,cRLSign"`, not a bare
  `openssl req -x509`. A CA cert without an explicit `keyUsage`
  extension verifies fine with some TLS clients (plain `openssl
  s_client`, a bare `ssl.SSLContext`) and fails outright with others
  (`requests`/urllib3 -- which is what the probe uses, see
  `transport.py`) with `certificate verify failed: CA cert does not
  include key usage extension`. This was hit for real: it looked
  correct in ad hoc verification and only failed once an actual probe
  tried to authenticate. Reproduced and confirmed fixed with a bare
  `requests.get(..., verify=ca_cert)` against a throwaway TLS server
  before trusting the fix.
- `pki/generate-server-cert.sh` — generates the server's TLS cert/key,
  signed by the internal CA, **with the server's public IP as a Subject
  Alternative Name** (there is no DNS name to use — the script must take
  the IP as an argument and fail loudly if it's not provided; don't assume
  `localhost` or guess), and with an explicit `keyUsage = critical,
  digitalSignature,keyEncipherment` extension alongside
  `extendedKeyUsage = serverAuth` (for the same reason as the CA's
  `keyUsage` above -- the probe-issued client certs, signed via
  `probes/ca.py`'s `cryptography`-based signing, already set this
  correctly; only the bash+openssl-generated CA and server certs were
  missing it). Also prints the certificate's SHA-256 fingerprint
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
commands — see 5.7), `SERVER_SSH_PUBLIC_KEY_HOST_PATH` (default
`/home/ubuntu/.ssh/id_ed25519.pub` — the VM's own SSH public key, bind-
mounted read-only into `django` and handed to every probe during
enrollment so the server can SSH into it without a manual step; see 5.7),
Grafana admin user/password, `PUBLIC_SUMMARY_API_KEY` (generated fresh in
`setup.sh` like the Django secret key; empty disables `GET
/api/v1/public/summary` entirely rather than leaving it open — see 7.3),
and `PUBLIC_LOCATION_PRECISION_DECIMALS` (default `2`, roughly
neighborhood-level precision — how coarsely that endpoint rounds
probe coordinates before publishing them). Also `TELEGRAM_BOT_TOKEN`
(from @BotFather, not generated by this project — empty means the
`telegram-bot` service and `send_daily_digest` both fail loudly rather
than silently doing nothing), `TELEGRAM_BOT_USERNAME` (just for
building the `t.me/<username>` link shown on `public-page/`),
`SUBSCRIPTION_MAX_DISTANCE_KM` (default `15`, see 5.9), and
`NOMINATIM_USER_AGENT` (identifies this application to Nominatim per
its usage policy — must be a real contact/project URL, not a generic
placeholder). `.env` itself must be gitignored.

The server's own WireGuard keypair is **not** an env var — like the mTLS
CA, it's a generated file (`wireguard/server_private.key`,
`wireguard/server_public.key`), gitignored, with the private key never
leaving the host.

**Django settings requirements beyond `.env`**, both discovered the hard
way in an earlier pass and worth stating explicitly so they aren't lost
in a rewrite:
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` is
  required, not optional, given nginx terminates TLS and proxies to
  Django over plain HTTP internally. Without it, `request.is_secure()` is
  always `False`, and Django's CSRF `Origin` check — which compares the
  browser's real `https://...` Origin against what Django itself believes
  the scheme is — rejects every POST, including Admin login, as
  cross-origin. `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE = True` should
  follow once this is set, since the admin is genuinely HTTPS-only.
- An explicit `LOGGING` setting that sends everything to a console
  handler **unconditionally**, not gated on `DEBUG`. Django's own default
  logging config only sends request-handling exceptions (i.e. anything
  that becomes a `500`) to the console when `DEBUG=True`; with
  `DEBUG=False` (required, see above) the only handler left is
  `mail_admins`, which does nothing without `ADMINS` configured. Left at
  the default, `docker compose logs django` shows gunicorn's boot
  messages and nothing else, even across a real unhandled exception —
  exactly the situation where an operator most needs to see a traceback.

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
7. If the response includes the server's SSH public key (it's optional
   server-side — see below), append it to the enrolling user's own
   `~/.ssh/authorized_keys` (create `~/.ssh` with `0700` if it doesn't
   exist, the file itself `0600`, skip appending if that exact key line
   is already present). `setup.sh` invokes `enroll.py` with
   `--ssh-user <the user running setup.sh>` so it knows whose home
   directory to write into even though it runs elevated (it needs root to
   write `/etc/weathernet-probe` and `/etc/wireguard`; resolve the
   target user's home directory server-side via `pwd.getpwnam()`, don't
   assume `$HOME`, which under `sudo` would resolve to root's). This is
   what lets the server SSH straight into a newly enrolled probe with no
   manual key copying at all — see 5.8/6.7.
8. Installs and starts both `weathernet-probe.service` and
   `wg-quick@wg0.service`.
9. Prints a plain confirmation: probe ID, tunnel IP, and "you should see
   this probe appear in Grafana within `report_interval_seconds`, and it
   should be SSH-reachable at `<tunnel-ip>` from the server within about a
   minute" (see the sync timer note below) — and, if the server's SSH key
   was installed, "the server can already SSH in directly, no key setup
   needed."

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
   server's WireGuard public key, the server's WireGuard endpoint
   (`<public-ip>:<port>`), the server's own WireGuard *tunnel* address
   (e.g. `10.10.0.1` — not the same thing as the public endpoint above;
   this is what the probe's own `[Peer]` block's `AllowedIPs` must be),
   and the contents of the server's SSH public key from
   `SERVER_SSH_PUBLIC_KEY_HOST_PATH` if that file is readable (`null` if
   not — this is a best-effort convenience, not something that should
   ever fail enrollment). See Section 7 for the exact JSON shape.

**Ordering within the view, stated as a rule, not just an example:**
gather and validate *everything* the response needs — signing the CSR,
allocating the tunnel IP, reading the CA cert and the SSH public key file
back off disk — before the `Probe`/`EnrollmentToken` writes inside the
`transaction.atomic()` block, not just before the writes that obviously
depend on that data. A failure raised after those writes commit but
before the response is returned leaves a burned token and an orphaned
`Probe` row with no certificate ever delivered to the device — a real
failure mode hit during implementation (a file-permissions slip on the
WireGuard public key), not a hypothetical one. If a future revision adds
another field to the response, the same ordering applies to it too.

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
   without a human copying it around. The private key needs restrictive
   permissions (`umask 077` before generating it is fine) — but don't let
   that umask also restrict the **public** key file: it's not sensitive,
   it's read by the `django` container (a different host UID than
   whichever user ran this script) and handed to every probe, so it needs
   to end up world-readable (`chmod 644` after generating it), not
   inheriting the private key's `600`.
2. `wg-quick up wg0` is brought up on the host directly (not in Docker —
   see the note in 5.1), listening on `WIREGUARD_LISTEN_PORT`, with its own
   tunnel address `10.10.0.1` (the first address in `WIREGUARD_SUBNET`).
3. The VM's firewall / cloud security group needs the WireGuard UDP port
   opened, in addition to `443` for nginx and Grafana's port — call this
   out explicitly in the setup script's summary output, since it's easy to
   forget and the failure mode (tunnel silently doesn't come up) is
   confusing to debug. When troubleshooting this, check the *actual*
   firewall tool in use (`sudo iptables -L INPUT -n -v` /
   `sudo nft list ruleset`), not just `ufw` — it may not even be
   installed. Several cloud providers sync a local `iptables`/`nftables`
   ruleset automatically from the provider's own console/API (a security
   group), in which case a rule added directly on the host is only a
   test, not a durable fix; the persistent one is opening the port in
   that console. `sudo tcpdump -i any udp port $WIREGUARD_LISTEN_PORT -n`
   on the server while a probe's keepalive is retrying is the fastest way
   to tell "packets never arrive" (upstream block) from "packets arrive
   but no handshake" (local firewall dropping them after arrival).
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

**Implementation trap in `sync-peers.sh` worth flagging explicitly:**
applying the freshly-rendered config to a live interface needs
`wg syncconf`, which needs its input already reduced to what `wg-quick
strip` produces. The natural-looking way to chain this —
`sudo wg syncconf wg0 <(sudo wg-quick strip wg0.conf)` — routinely fails
with `fopen: No such file or directory`. Piping a process-substitution
`/dev/fd/N` path through a *second*, outer `sudo` breaks because sudo
commonly closes inherited file descriptors above stderr before exec'ing
its target, as a security default. Write to a real temp file instead
(`sudo wg-quick strip wg0.conf > tmpfile` as one step, `sudo wg syncconf
wg0 tmpfile` as the next) — a real path has no fd-inheritance edge case.
Since this script runs under `set -euo pipefail` and is called
unconditionally from `setup.sh`'s WireGuard step, a failure here doesn't
just skip the peer sync — it aborts the rest of `setup.sh` too, including
whatever comes after (in v1's ordering, that's the systemd timer
installation). A script dying partway through under `set -e` can look
like an unrelated later step "was never implemented" when it was actually
just never reached — worth remembering when diagnosing which of two
symptoms is the real cause.

**Reaching a probe:** the operator SSHes into the server (existing access,
unrelated to any of this), then from there `ssh <user>@10.10.0.x` to the
probe's tunnel address. The server acts as a bastion; the operator's own
laptop does not need to be a WireGuard peer for v1 (see Section 12 for the
option to add that later). Since enrollment (5.7) installs the server's
own SSH public key into the probe's `authorized_keys` automatically, this
should just work with no key setup on the operator's part — the earlier
design (before that automatic install existed) required either copying
the server's key to the probe by hand, or forwarding the operator's own
laptop-side agent through the server with `ssh -A` for the hop. Agent
forwarding is still the right fallback if `SERVER_SSH_PUBLIC_KEY_HOST_PATH`
wasn't configured, doesn't resolve, or the probe was enrolled before this
feature existed.

### 5.9 Telegram Daily Digest (`subscriptions` app)

A `WeatherSubscription` (chat id + a geocoded place + coordinates) lets
someone receive a once-a-day summary of the previous day's readings
from whichever active probe is nearest to a place they name — entirely
through a Telegram bot, with no web form (Section 3's "fifth path").
Why a bot conversation instead of a form asking for a phone number,
which is how this was first proposed: Telegram's Bot API has no way to
message an arbitrary phone number — a bot can only message a chat that
already exists, i.e. one where a user pressed Start. There is no
number to type at all in the working design; the chat itself, once
started, is the identity.

**Bot process (`telegram-bot` service, `manage.py telegram_bot_poll`,
5.1).** Long-polls Telegram's `getUpdates` (25s per call) rather than
running a webhook — a webhook would need Telegram's servers to accept
an inbound HTTPS connection to the VM, and the VM's certificate is
self-signed (Section 12), not something to route around just for this.
Runs forever as its own container process, not a cron job, so replies
feel immediate rather than up-to-a-timer-interval delayed. Dispatch
(`subscriptions/bot.py`) is deliberately separate from the poll loop
itself so it's unit-testable without a live network connection (mock
`send_message`, call the dispatch function directly).

**Conversation, driven entirely by free text plus four commands:**
- `/start` / `/help` — welcome/instructions.
- Any other text — treated as a place name (rejected outright above
  `MAX_QUERY_LENGTH`, 200 chars — see "Abuse resistance" below).
  Geocoded via Nominatim (OpenStreetMap's open geocoder — no API key).
  Doesn't need to be precise, only close enough to compare against
  probe locations. A repeat query resolving within ~1km of an existing
  subscription for the same chat is treated as a duplicate, not a new
  one, to avoid silent pile-up from re-sending roughly the same place
  name.
- `/list` — every subscription for this chat, each annotated with its
  current nearest-probe distance (or "none close enough yet").
- `/remove <n>` — remove the nth subscription (the number shown by
  `/list`).
- `/stop` — remove every subscription for this chat.

A chat may have more than one subscription (e.g. home and a holiday
house), each tracked and matched independently.

**Matching (`subscriptions/matching.py`).** Great-circle (haversine)
distance from a subscription's coordinates to every active `Probe`
with coordinates set; the nearest one, regardless of distance, is
returned, and callers compare it against `SUBSCRIPTION_MAX_DISTANCE_KM`
(default 15) themselves. No spatial index — a linear scan over a
handful of probes is fine at this project's scale. Recomputed on every
`/list` and every digest run rather than pinned to a probe at
subscribe time: a closer probe enrolled later, or the current one
deactivated, should change the answer without the subscriber doing
anything.

If a subscription's coordinates aren't within range of any active
probe at subscribe time, it's saved anyway, with the bot saying so
explicitly — not silently dropped, and not treated as an error. Once a
probe *is* within range (either at subscribe time, or in a later
`send_daily_digest` run — see below), `WeatherSubscription.probe_ever_found`
flips permanently to `True`.

**Daily digest (`send_daily_digest`, run once a day by
`weathernet-daily-digest.timer`/`.service`, same host-level
systemd-timer-runs-`docker compose exec` pattern as WireGuard
peer-sync, Section 5.8/8.1).** For every subscription with an active
probe within range: query `SensorReading` for that probe over
*yesterday*, computed as a calendar day in the server's local timezone
(`settings.TIME_ZONE`, Section 5.6) rather than a UTC day boundary, so
"yesterday" means what a reader intuitively expects. Aggregate
min/max/avg temperature, avg humidity, min/max/avg pressure, and the
same heuristic air quality score used elsewhere (`probes/aqi.py`,
Section 7.3) from avg gas resistance against a 7-day rolling baseline.
Missing data for any one field renders as "n/d" in the message rather
than failing the whole send. The **first** time a subscription's
`probe_ever_found` flips to `True` during a digest run, the message
gets an extra "a probe is now in range" line ahead of the regular
summary — the alert the original feature request specifically asked
for, not just a silent start. A subscription with no probe in range is
skipped entirely for that day: not an error, just not ready yet.

**Abuse resistance.** Long-polling already means there's no inbound
network endpoint to attack (Section 12) — the only surface is Telegram
messages, which anyone can send to a public bot at any rate Telegram
itself allows, accidentally or as a deliberate DoS attempt. Two
concrete failure modes that come from that, both mitigated in
`subscriptions/geocoding.py` and `bot.py` rather than left as a
documented-but-unenforced policy:
- **Getting this server's IP blocked by Nominatim.** Its usage policy
  caps requests at 1/second; a message flood would blow past that in
  a real client. `geocoding.py` enforces the same cap itself, in
  process (`_throttle()`, a module-level last-request timestamp) —
  every call to `geocode_place()` blocks until at least 1.1s have
  passed since the previous one, regardless of how fast messages
  arrive. Getting Nominatim to block this server would break geocoding
  for every legitimate user, not just whoever triggered it, which is
  why this is enforced rather than just documented as a policy to
  respect.
- **Unbounded growth from one chat.** The Nominatim throttle slows a
  flood down but doesn't cap how many subscriptions eventually land
  from a patient sender. `MAX_SUBSCRIPTIONS_PER_CHAT` (10) and
  `MAX_QUERY_LENGTH` (200 chars) in `bot.py` bound both the table's
  growth and the number of digest messages one chat can generate per
  day — both checked *before* geocoding, so a rejection never costs a
  wasted (throttled) Nominatim call.

Both limits are conservative, hardcoded constants rather than `.env`
settings — there was no reason to expose a knob for numbers this
generous relative to legitimate use. A determined, sustained attack
(many different Telegram accounts, each under both limits) is not
defended against beyond what's above; not worth more machinery at this
project's scale, but worth being honest that these two limits raise
the bar rather than eliminate the risk entirely.

## 6. Probe Component

### 6.1 Design constraints

- No containerization. Plain Python 3, a virtualenv, and a systemd service.
  Assume Raspberry Pi OS (current stable, Debian-based) as the target OS for
  v1, but don't hardcode Raspberry Pi OS paths where a generic Linux
  assumption would do just as well — the goal is "runs on a Pi today,
  doesn't fight you on a generic Debian box tomorrow."
- Must run comfortably on a Raspberry Pi 3 — keep dependencies light.
  `psutil` for health metrics is fine; avoid pulling in heavy frameworks.
  In practice `psutil` almost always has no prebuilt wheel for a Pi's
  exact Python-version/architecture combination and compiles its C
  extension from source on install — `probe/scripts/setup.sh` must
  install `python3-dev` and `build-essential` (via the system package
  manager) before `pip install`, or that build fails outright with a
  missing-`Python.h` error. This isn't optional/defensive, it's the
  expected path on real Pi hardware, not an edge case.
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

**`enroll.py` must also clear the spool (`spool_path`, default
`/var/lib/weathernet-probe/spool.jsonl`) as part of every enrollment,
including a re-enrollment of an already-set-up probe.** Every spooled
entry carries its own `probe_id`, fixed at the moment it was queued. A
(re-)enrollment always assigns a *new* `probe_id`, so any entry already
in the spool becomes permanently unsendable the instant enrollment
finishes -- the server 403s a `probe_id`/certificate mismatch (Section
7.2), and there is no way to make it match again, the certificate that
could have matched it was just overwritten. This is worse than it
sounds: `transport.py` sends the spool oldest-first and stops at the
first failure (Section 6.4), so leaving stale entries in place doesn't
just waste retries on those entries -- it permanently blocks *every*
subsequent reading too, since they can never get past the stuck stale
entry at the front of the queue. Hit for real during a routine
re-enrollment: the probe looked healthy (valid cert, WireGuard up) but
`last_seen_at` never updated, and the actual cause (spool blocked on
stale entries) was one layer behind the first error the logs showed.

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
5. If the enrollment response included the server's SSH public key,
   append it to the enrolling user's `~/.ssh/authorized_keys` (see
   5.7 step 7 for the exact mechanics, including why `enroll.py` needs
   an explicit `--ssh-user` rather than trusting `$HOME` while running
   elevated). This is what makes the server able to SSH straight into
   the probe with zero manual key setup once the tunnel is up.
6. As defense in depth (the tunnel is already only reachable by the
   authenticated server peer, but a compromised server or a
   misconfiguration shouldn't hand over more than necessary): configure the
   probe's local firewall to only accept SSH on the `wg0` interface, nothing
   else. A couple of `ufw` rules are enough; don't over-engineer this.

## 7. API Contract

Document this formally in `docs/api-contract.md` as well as implementing
it; keep the two in sync. Four endpoints, with different trust models —
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
    "server_endpoint": "<server-public-ip>:51820",
    "server_tunnel_ip": "10.10.0.1"
  },
  "server_ssh_public_key": "ssh-ed25519 AAAA... ubuntu@weather",
  "report_interval_seconds": 60
}
```

`wireguard.server_endpoint` (public IP:port, for `Endpoint =`) and
`wireguard.server_tunnel_ip` (this server's own WireGuard address, for
this probe's `AllowedIPs =`) are two different addresses — a response
missing one of them because it looked redundant with the other is a real
mistake this project's own implementation made once already. `server_ssh_public_key`
is `null` when `SERVER_SSH_PUBLIC_KEY_HOST_PATH` isn't configured or
isn't readable — a missing SSH key must never fail enrollment itself,
it's a convenience layered on top, not a requirement of it.

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

### 7.3 `GET /api/v1/public/summary` — unauthenticated by cert, gated by a shared API key

For an external, public-facing page's server-side code (Section 3's
"fourth path"), not for probes. Request header:
```
X-Api-Key: <PUBLIC_SUMMARY_API_KEY>
```
A missing, wrong, or — when `PUBLIC_SUMMARY_API_KEY` is unset
server-side — *any* key returns `401`; an unset key means the endpoint
is disabled, not open to an empty/omitted header.

Response `200`:
```json
{
  "generated_at": "2026-08-18T09:00:00Z",
  "probes": [
    {
      "name": "weather-000",
      "hardware_type": "raspberry_pi_3",
      "latitude": 45.46,
      "longitude": 9.19,
      "last_seen_at": "2026-08-18T08:55:12Z",
      "readings": {
        "temperature_c": 24.3,
        "humidity_pct": 51.2,
        "pressure_hpa": 1012.4,
        "gas_resistance_ohm": 82345.0,
        "air_quality_index": 78
      }
    }
  ]
}
```

Only `Probe` rows with `is_active = true` and both coordinate fields set
are included. `latitude`/`longitude` are rounded to
`PUBLIC_LOCATION_PRECISION_DECIMALS` — coarser than what's actually
stored, so the exact property is never exposed publicly, while still
placing a probe in its general area (and, with more than one probe,
supporting a future geographic heatmap without an API change — the
response shape is already a list per probe for exactly that reason,
even though v1 ships with one probe). `air_quality_index` is a
heuristic 0–100 score (`probes/aqi.py`): 75% weight on the latest gas
resistance relative to its 7-day rolling maximum, 25% weight on
humidity comfort (best near 40%) — **not an official/certified AQI**,
a real calibrated IAQ needs Bosch's proprietary BSEC library, out of
scope (see Section 12 and `probe/weathernet_probe/sensors/bme680.py`).
`null` for any reading/score not yet available for a probe.

**Never included, by design, however the response shape evolves**:
`location_address`, `owner_email`, `owner_phone`. These exist on
`Probe` for internal/admin use only.

Responses: `200` as above (`probes` may be an empty list); `401`
missing/wrong key or endpoint disabled; `429` rate limit exceeded
(nginx, not Django — Section 5.1).

### 7.4 `GET /api/v1/public/history` — same trust model as 7.3

For the external page's charts. A separate endpoint from `summary`
rather than folded into it: most calls (i.e. most cron runs) only need
current values, so fetching history every time would be strictly
heavier for no benefit to that common case.

Query param `hours` (optional, default `24`, clamped to `[1, 168]`;
an unparseable value falls back to the default rather than erroring).

Response `200`:
```json
{
  "generated_at": "2026-08-18T09:00:00Z",
  "window_hours": 24,
  "probes": [
    {
      "name": "weather-000",
      "series": {
        "temperature_c": [{"time": "2026-08-18T08:00:00Z", "value": 23.1}],
        "humidity_pct": [],
        "pressure_hpa": [],
        "gas_resistance_ohm": []
      }
    }
  ]
}
```

Same probe eligibility as 7.3. Every sensor type is always present in
`series`, as `[]` if there's no reading of that type in the window —
callers shouldn't need to guess which keys might be missing. Points
ordered oldest to newest. No `air_quality_index` here: it's relative
to a baseline computed as of "now" (`probes/aqi.py`), so there's no
single historically-accurate value to attach to each past point
without real added query complexity (a rolling-max-as-of-each-timestamp
query) for what's fundamentally meant to be a raw-sensor-trend chart.

Responses: `200` as above (`probes` may be an empty list); `401`
missing/wrong key or endpoint disabled; `429` rate limit exceeded
(nginx, not Django — Section 5.1).

## 8. Setup Scripts

### 8.1 `server/scripts/setup.sh`

Idempotent where reasonably possible. Steps:
1. Check for Docker + Docker Compose plugin; fail with a clear message if
   missing (don't try to install Docker itself — that's out of scope and
   risky to automate blindly).
2. Prompt for (or accept as script arguments) the server's public IP.
3. Generate `.env` from `.env.example` if it doesn't exist, filling in a
   freshly generated Django `SECRET_KEY` and randomized default passwords
   where the example has placeholders. If `.env` **already** exists (a
   redeploy, not a first run), instead backfill: for every key present in
   `.env.example` but absent from `.env`, append it with its example
   value. This isn't optional politeness — `docker compose` substitutes
   an *empty string* for a referenced variable that's undefined in `.env`
   (not "leave it unset" in the container), so a new config key added to
   `.env.example` in a later revision of this project silently becomes
   `""` inside the container on redeploy rather than falling back to
   whatever default the application code has, which can crash Django
   outright (e.g. `int("")`) depending on what reads it.
4. Run `pki/generate-ca.sh` and `pki/generate-server-cert.sh <public-ip>` if
   the CA doesn't already exist. Ensure the resulting key files have
   permissions that allow the `django` container's bind mount to read them
   (Section 5.1) without being world-readable on the host — grant access
   to the container's fixed UID (see the Dockerfile note in 5.1)
   specifically, e.g. `chown` the CA key to that UID, rather than making
   it world-readable.

   Also generate the server's WireGuard keypair here (`wireguard/
   generate-server-keys.sh`) if it doesn't already exist — **before**
   `docker compose up`, not after, even though bringing the tunnel itself
   up happens later (step 10). The public key is bind-mounted read-only
   into `django` (5.1/5.7); if that file doesn't exist yet when the
   container first starts, Docker creates an empty directory in its place
   instead of failing loudly, and every enrollment fails obscurely
   afterward. Install `wireguard-tools` first if the `wg` CLI isn't
   present.
5. Render `nginx.conf` from the template with the public IP, including the
   `ssl_verify_client optional_no_ca` setting and the `/api/v1/ingest`
   location block's explicit `$ssl_client_verify` check, both from
   Section 5.1.
6. `docker compose build && docker compose up -d`, followed by an
   explicit `docker compose restart nginx`. The restart is not redundant:
   `nginx.conf` is bind-mounted, not baked into the image, and `docker
   compose up -d` only recreates a container when the *service
   definition* in `docker-compose.yml` changed — it has no visibility
   into a bind-mounted file's contents. On a redeploy where only the
   rendered `nginx.conf` changed, an already-running nginx container
   silently keeps serving whatever config it read at its last actual
   start; this script looking like it succeeded is not evidence the new
   config took effect. The same principle applies to any other script
   that regenerates a bind-mounted config file for an already-running
   service.
7. Wait for Postgres to be ready, then run Django migrations inside the
   `django` container, then `collectstatic --noinput` (Admin's CSS/JS are
   served by WhiteNoise, not nginx — see 5.1's Dockerfile note), then an
   explicit `docker compose restart django`. The restart here is not
   optional either, and for a different reason than nginx's: WhiteNoise
   indexes `STATIC_ROOT` once, at process (gunicorn worker) startup —
   production mode has no autorefresh. `collectstatic` just wrote files
   into a directory the already-running workers scanned *before* those
   files existed; every static asset 404s until something restarts the
   process. Skipping this restart is exactly the kind of thing that looks
   fine (the command exits `0`) and then produces a completely unstyled
   Admin login page.
8. Prompt to create a Django superuser (interactive `createsuperuser`, or
   accept `--noinput` env-based creation for automation).
9. Migrations from step 7 already create the extension, hypertables, and
   compression/retention policies — this step is just a sanity check:
   query `timescaledb_information.hypertables` and print a confirmation
   that both hypertables exist, so a silently-failed migration doesn't go
   unnoticed.
10. Bring the tunnel up: run `wireguard/sync-peers.sh` (this both renders
    `wg0.conf` from the current, possibly-empty probe registry and brings
    up `wg-quick@wg0`, since the script detects there's no active
    interface yet — see Section 5.8 for exactly how). Then install the
    systemd timer that runs `wireguard/sync-peers.sh` every minute
    (Section 5.8), so future enrollments don't need a manual step. Remind
    the operator, loudly, that the WireGuard UDP port needs to be opened in
    their firewall/security group — this is easy to miss since nothing
    fails locally when it's closed, it just silently doesn't work from
    outside (see 5.8 for how to actually diagnose that when it happens).
11. If `TELEGRAM_BOT_TOKEN` is set (5.9), install the
    `weathernet-daily-digest` systemd service/timer the same way step 10
    installs WireGuard peer-sync's. If it's blank, skip this with a
    message explaining how to come back to it (set the token, bring up
    the `telegram-bot` service, re-run this script) rather than failing
    the whole run over an optional feature — a fresh install shouldn't be
    blocked on having a Telegram bot token in hand yet.
12. Print a summary: URLs for Grafana and Django Admin, and a reminder of
    the enrollment flow — "to add a probe, create an `EnrollmentToken` in
    Django Admin; it will print the exact command to run on the probe."

### 8.2 `probe/scripts/setup.sh`

Dramatically shorter than a manual-credentials design would need, by
design — see Section 5.7.

1. Parse `--server <ip>`, `--token <token>`, optional `--fingerprint
   <sha256>`. Fail with a clear usage message if `--server` or `--token`
   is missing; warn (but don't fail) if `--fingerprint` is missing, per
   Section 5.7's note on what that trade-off actually means.
2. Check Python 3 version; create a virtualenv; install `python3-dev` and
   `build-essential` via the system package manager first (see 6.1 for
   why this has to happen before the next sub-step, not just "if pip
   complains"), then `probe/requirements.txt`; install `wireguard-tools`
   via the system package manager if missing.
3. Run `probe/scripts/enroll.py` with the parsed arguments, plus
   `--ssh-user <the user running this script>` (so `enroll.py` — which
   runs under `sudo` to write `/etc/weathernet-probe` and
   `/etc/wireguard` — knows whose `~/.ssh/authorized_keys` to install the
   server's SSH key into; see 5.7 step 7). This does everything described
   in Sections 5.7 and 6.7: hardware detection, local key/CSR generation,
   optional TLS fingerprint pinning, the `/api/v1/enroll` call, writing
   `probe.yaml`, the cert files, and `wg0.conf` to their final locations,
   and installing the server's SSH key if the response included one.
   Abort with whatever clear error `enroll.py` produced (expired token,
   fingerprint mismatch, etc.) rather than swallowing it.
4. Install the systemd unit (`weathernet-probe.service`) from the template,
   substituting the actual install path and venv path.
5. `systemctl enable weathernet-probe wg-quick@wg0`, then
   `systemctl restart weathernet-probe wg-quick@wg0` -- **not**
   `enable --now`. This script is meant to be safely re-runnable to
   re-enroll an already-set-up probe (see the top-level README), and on
   a re-run both services are typically already active; `enable --now`
   is a no-op on an already-running unit. That leaves the *old* process
   running with the *old* `probe.yaml`/certs/WireGuard keys held in
   memory, silently ignoring whatever `enroll.py` just wrote to disk --
   a real failure mode hit in practice: the probe kept presenting its
   previous WireGuard public key indefinitely after a re-enrollment,
   because `wg-quick@wg0` was already up and never got told to reload.
   `restart` always applies the current on-disk config, whether this is
   the first run (restarting an inactive unit is just a start) or not.
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
    about a minute of enrollment via the sync timer, Section 5.8). Note
    that this should need no key setup at all if
    `SERVER_SSH_PUBLIC_KEY_HOST_PATH` is configured (5.6/5.7); mention
    `ssh -A` agent forwarding only as the fallback for when it isn't.
11. Known limitations of v1 (link to or restate Section 12 below).
12. Troubleshooting basics: checking probe logs, checking nginx logs for
    mTLS handshake failures, checking Django logs for rejected ingests or
    failed enrollments (`docker compose logs django` — requires the
    `LOGGING` config from 5.6 to actually show anything, see that section),
    and diagnosing a WireGuard tunnel that won't come up: compare `wg show`
    on both the server and the probe first (matching keys but no
    `endpoint`/handshake on the server side means packets aren't arriving
    at all, not a config problem), then see 5.8 for the `tcpdump` +
    `iptables`/`nftables` sequence that actually finds a blocked port.

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
- **Licensing is per-directory, not automatically repo-wide, under
  `3d-printing/`.** The top-level `LICENSE` (MIT) covers `server/`,
  `probe/`, `public-page/`, and, as it happens, every `3d-printing/`
  subdirectory too today — but not automatically or by default.
  3D-printable designs there can easily start from someone else's
  Creative-Commons-licensed model; whether that's a licensing
  obligation depends on whether the result was actually *derived from*
  that model's own files (importing/tracing/modifying its mesh) or
  just *inspired by* it (rebuilt from scratch — copyright protects the
  specific expression of a design, not the general idea, nor
  dimensions a physical component dictates). `3d-printing/sensors_enclosure/`
  is the latter: it started from a CC BY-NC-SA 4.0 design, but that
  design didn't fit the project's actual BME680 board, so it was
  rebuilt from scratch in FreeCAD with no geometry imported from the
  original — an independent work, not a derivative one, hence MIT
  rather than carrying the CC license forward. Before publishing any
  new design under `3d-printing/` that started from someone else's work:
  if it's an actual modification of their files, add a `LICENSE` file
  to that part's own subdirectory carrying their license forward
  (most CC "ShareAlike" variants and the GPL family require exactly
  that) — never assume MIT applies there by default in that case. See
  `3d-printing/README.md`.

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
- **SSH host key verification for the server → probe direction.**
  Enrollment (5.7) installs the server's SSH *public key* onto the probe
  so the server can authenticate itself as a client, but does nothing
  about the reverse — the probe's own SSH host key is still
  trust-on-first-use (the operator sees the usual "authenticity of this
  host can't be established" prompt the first time). Pinning the probe's
  host key automatically (e.g. reading it back during enrollment over the
  already-authenticated WireGuard tunnel and writing it into the server's
  `known_hosts`) would close that gap; not done in v1 because it adds a
  chicken-and-egg ordering problem (the tunnel needs to be up first) for
  a risk that's low at this project's scale (an attacker would need to be
  on-path for the *first* connection specifically).
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
  negligence later. `public-page/` (Section 7.3) works around this
  narrowly for *public read access to telemetry* specifically — an
  external page on a domain with its own real certificate is the only
  thing that ever reaches visitors, and only that page's own cron job
  (`sync.php`) ever calls the VM, so visitors never touch the VM's
  self-signed cert at all — but this does not extend to Admin or
  Grafana themselves, which remain trusted-network-only as above. The
  Telegram bot (Section 5.9) sidesteps the same limitation a different
  way: long-polling `getUpdates` instead of a webhook, since a webhook
  would need Telegram's own servers to accept the VM's self-signed
  certificate on an inbound connection.
- **Multi-probe geographic heatmap.** `public-page/` already renders a
  Leaflet/OpenStreetMap map with a marker per probe, colorable by
  temperature/humidity/pressure/air quality, reading the same
  coordinate/reading data `GET /api/v1/public/summary` (Section 7.3)
  returns for every probe — specifically so a real interpolated
  heatmap doesn't need an API change later, just a different renderer
  (e.g. the `leaflet.heat` plugin) for the same data. Not built now
  because v1 ships with a single probe, so one colored point is all
  there is to show; future work once there's more than one probe to
  interpolate between.
- **High availability / scaling.** Single server VM, single
  Postgres/TimescaleDB instance handling both relational and time-series
  data. No clustering, no failover, no read replicas.
- **Per-subscriber digest time/timezone (Section 5.9).** One fixed send
  time for everyone (`weathernet-daily-digest.timer`'s `OnCalendar`,
  the VM's system clock), and "yesterday" is always a calendar day in
  the server's own `settings.TIME_ZONE`. Fine while subscribers are
  all roughly in the same timezone as the server; would need a
  per-`WeatherSubscription` send-time field and a timer granular
  enough to act on it (or a move to an actual task scheduler) to
  support subscribers who genuinely want a different local send time.
