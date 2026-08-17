# WeatherNet

WeatherNet is a small telemetry platform for a network of DIY
environmental monitoring devices ("probes"). A probe reads sensor data
and device health metrics and reports them over mutual TLS to a server,
which validates the probe against a registry, stores the data in
TimescaleDB, and makes it visible in Grafana. Version 1 targets a
single Raspberry Pi probe and a single server, but the probe's sensor
framework and the server's registry are both built to add more probes
and hardware types later without a rewrite.

## Architecture

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

Every telemetry payload goes `probe → nginx (mTLS termination) → Django
ingestion endpoint → TimescaleDB hypertables`. Probes never talk to
Postgres or Grafana directly, and Django cross-checks every probe's
identity (from its client certificate) against the registry before
writing anything -- deactivating a probe in Django Admin actually stops
its data, not just hides it. See [`PROJECT_SPEC.md`](PROJECT_SPEC.md)
for the full design rationale and [`docs/api-contract.md`](docs/api-contract.md)
for the exact ingestion payload schema.

Separately, each probe also maintains an outbound **WireGuard** tunnel
to the server, purely for operator troubleshooting (e.g. SSHing into a
probe sitting behind a home NAT). It's an independent channel from the
mTLS telemetry path above -- unrelated to probe authentication for
ingestion, and not gated by a probe's `is_active` flag. See
[Section 3](PROJECT_SPEC.md#3-architecture) and
[Section 5.7](PROJECT_SPEC.md#57-remote-access-via-wireguard) of the spec.

## Prerequisites

**Server**: a VM with Docker and the Docker Compose plugin, a public
IP, and inbound access open on `443` (nginx/ingestion), Grafana's port
(`3000` by default), and the WireGuard UDP port (`51820` by default).

**Probe**: a Raspberry Pi (3 or newer) running a current Raspberry Pi
OS, Python 3, and network reachability to the server on `443`.

## Server setup

1. On the server, clone this repo and `cd server/`.
2. Generate the internal CA and server certificate (skip this if you'd
   rather let `scripts/setup.sh` do it for you -- it calls these
   automatically):
   ```bash
   ./pki/generate-ca.sh
   ./pki/generate-server-cert.sh <server-public-ip>
   ```
3. Run setup:
   ```bash
   ./scripts/setup.sh <server-public-ip>
   ```
   This generates `.env` (with a fresh `SECRET_KEY` and random
   passwords) if it doesn't already exist, builds and starts the Docker
   Compose stack, runs migrations (which also create the TimescaleDB
   hypertables and their compression/retention policies), prompts you
   to create a Django Admin superuser, and generates the server's
   WireGuard keypair and brings up its `wg0` interface.
4. When it finishes, the script prints the Grafana and Django Admin
   URLs and the server's WireGuard public key -- you'll need that key
   for every probe's setup.

## Probe setup

1. On the server, generate a client certificate for the new probe --
   pick a UUID (or let the probe's own `setup.sh` generate one for you
   and use it here):
   ```bash
   ./server/pki/generate-probe-cert.sh <probe-uuid>
   ```
   This prints the exact `scp` command to copy the resulting
   `client.cert.pem`, `client.key.pem`, and `ca.cert.pem` to the probe.
2. In Django Admin, add a `Probe` with `id` set to that same UUID.
3. On the probe device, clone this repo and `cd probe/`.
4. Copy the three files from step 1 into
   `/etc/weathernet-probe/certs/` on the probe (the printed `scp`
   command does this).
5. Run setup:
   ```bash
   ./scripts/setup.sh
   ```
   It creates a virtualenv, prompts for the probe UUID (must match step
   2), hardware type, and the server's public IP, writes
   `/etc/weathernet-probe/probe.yaml`, verifies the certificate files
   are in place, and installs + starts the `weathernet-probe` systemd
   service.
6. The same script then sets up the WireGuard tunnel: it generates the
   probe's own WireGuard keypair and prints its public key, then prompts
   you for the **server's** WireGuard public key (from step 4 of "Server
   setup") and a tunnel IP to assign this probe (e.g. `10.10.0.5` -- the
   next free address; the script doesn't check for collisions itself).
7. Back on the server, open the `Probe` you created in step 2, in Django
   Admin, and fill in `wireguard_public_key` (printed in step 6) and
   `wireguard_tunnel_ip` (the address you picked in step 6). Then run:
   ```bash
   ./server/wireguard/sync-peers.sh
   ```
   so the server picks up this probe as a peer without dropping any
   existing tunnels.

## Adding a second probe

Repeat the "Probe setup" steps above with a new UUID, name, and the
next free WireGuard tunnel IP -- that's the entire multi-probe story
for v1. Nothing on the server side needs to change beyond running
`sync-peers.sh` again; the registry and ingestion endpoint already
support any number of probes.

## Deploying updates

**Server**: `./server/scripts/deploy.sh` -- pulls, rebuilds images, runs
any new migrations, recreates changed containers, prints the deployed
commit.

**Probe**: `./probe/scripts/deploy.sh` -- pulls, reinstalls dependencies
into the existing virtualenv, restarts the systemd service, tails
recent logs.

Both refuse to run if the working tree has uncommitted changes.

## Adding a real sensor driver

v1 ships mock sensors only (see "Known limitations" below). To add a
real one:

1. Add a module under `probe/weathernet_probe/sensors/` implementing
   the `Sensor` interface from `sensors/base.py` (a `sensor_type` and a
   `read()` method).
2. Add one entry mapping a config name to your new class in
   `sensors/registry.py`.
3. Add that name to the `sensors:` list in a probe's `probe.yaml`.

Nothing in `main.py` or `transport.py` needs to change.

## Reaching a probe remotely

SSH into the server first (your existing access to it, unrelated to
WireGuard), then hop to the probe's tunnel IP:

```bash
ssh <server-user>@<server-public-ip>
ssh <probe-user>@10.10.0.x
```

The server is the only bastion into the WireGuard subnet for v1 -- your
own laptop isn't a peer. This works even for a probe you've deactivated
in Django Admin (`is_active` only gates telemetry ingestion, not
WireGuard reachability) and even if its mTLS certificate has been
revoked. After registering or editing any probe's WireGuard fields in
Django Admin, run `./server/wireguard/sync-peers.sh` on the server so
the change takes effect.

## Known limitations of v1

- **Mock sensors only.** Real drivers (BME680, SPS30, wind vane/rain
  gauge) are not implemented; see "Adding a real sensor driver" above.
- **Raspberry Pi only.** No Arduino / non-Linux probe support yet.
- **Manual certificate issuance.** No enrollment protocol or rotation
  automation -- certs are generated and copied by hand via the `pki/`
  scripts.
- **No automatic WireGuard peer sync.** `wireguard/sync-peers.sh` is a
  manual step after registering or editing a probe's WireGuard fields
  in Django Admin -- there's no save hook that runs it for you.
- **No WireGuard access for the operator's own device.** The server is
  the only bastion into the WireGuard subnet; reaching a probe means
  SSHing into the server first, then hopping to the probe's tunnel IP.
- **No alerting.** Grafana alert rules can be added manually; none are
  provisioned.
- **No browser-trusted HTTPS for Django Admin / Grafana.** There's no
  DNS name yet, so their TLS is either self-signed or plain HTTP.
  Treat the server as trusted-network-only for admin access.
- **Single server, single database.** No HA, clustering, or failover.

## Troubleshooting

- **Probe logs**: `journalctl -u weathernet-probe -f` on the probe.
  Check `/var/lib/weathernet-probe/spool.jsonl` -- if it's growing, the
  probe can't reach the server (spooled readings are retried every
  cycle).
- **nginx / mTLS handshake failures**: `docker compose logs nginx` on
  the server. A `495` response means the client cert failed
  verification; `496` means no client cert was presented at all.
- **Rejected ingests**: `docker compose logs django` on the server. A
  `404` means the probe's UUID isn't registered; `403` means it's
  either inactive or the certificate CN doesn't match the payload's
  `probe_id`.
- **WireGuard tunnel not connecting**: run `sudo wg show wg0` on both
  the server and the probe. `latest handshake: (none)` on the probe
  usually means the WireGuard UDP port isn't actually open on the
  server's firewall/security group; on the server, a missing peer entry
  means `wireguard/sync-peers.sh` hasn't been run since that probe's
  keys/IP were saved in Django Admin.

See [`server/README.md`](server/README.md) and
[`probe/README.md`](probe/README.md) for component-specific detail.
