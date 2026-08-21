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
                          ┌──────────────────────────────────────────────────┐
                          │  Server VM (public IP, no DNS)                   │
                          │                                                  │
  ┌──────────┐  mTLS      │  ┌────────┐   verified    ┌────────┐             │
  │  Probe   │───────────▶│  │ nginx  │──cert CN hdr─▶│ django │             │
  │ (RPi,    │  HTTPS/443 │  │ (TLS + │               │        │──┐          │
  │ systemd, │            │  │ mTLS   │               └────────┘  │writes    │
  │ no       │            │  │ term.) │                           ▼          │
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
                          └──────────────────────────────────────────────────┘
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
[Section 5.8](PROJECT_SPEC.md#58-remote-access-via-wireguard-ongoing-operation)
of the spec.

A third path exists for exactly one moment per probe: **enrollment**. A
brand-new probe has no mTLS certificate yet, so it can't use mTLS to
bootstrap itself -- a single-use, short-lived token (generated in
Django Admin) stands in for that first credential exchange instead.
See [Section 5.7](PROJECT_SPEC.md#57-zero-touch-probe-enrollment) of
the spec for the full design.

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
   URLs and installs a systemd timer that keeps WireGuard peers in sync
   automatically -- no manual step needed there going forward.

## Probe setup

This is genuinely one command on the probe side -- zero-touch
enrollment (see above) means there's no manual certificate, UUID, or
WireGuard key exchange to do by hand.

1. In Django Admin, add an **Enrollment token**: fill in the probe's
   name and hardware type, save. The resulting page prints a one-time
   token and the *exact* command to run on the probe, including the
   server's IP and its certificate fingerprint (for TLS pinning) --
   nothing to copy from anywhere else.
2. On the probe device, clone this repo and `cd probe/`.
3. Run the command Django printed:
   ```bash
   ./scripts/setup.sh --server <server-public-ip> --token <token> --fingerprint <sha256-fingerprint>
   ```
   This creates a virtualenv, generates the probe's mTLS and WireGuard
   keypairs locally (private keys never leave the device), verifies the
   server's certificate against the given fingerprint before sending
   anything, exchanges the token for a signed certificate and an
   assigned WireGuard tunnel IP, writes `probe.yaml` and `wg0.conf`, and
   installs + starts both the `weathernet-probe` and `wg-quick@wg0`
   systemd services.
4. That's it. The probe should appear in Grafana within its report
   interval, and become SSH-reachable at its WireGuard tunnel IP from
   the server within about a minute (the timer installed during server
   setup picks it up automatically).

`--fingerprint` is optional but recommended -- without it, the probe
still enrolls, but has no way to confirm it's actually talking to your
server for that one bootstrap request (it prints a clear warning when
you skip it, rather than silently reducing the guarantee).

Once enrolled, open that `Probe` in Django Admin and optionally fill in
its location (a free-text description plus GPS coordinates) and owner
contact info (email, and an optional phone number) -- useful once you
have more than one or two probes and need to remember where a given one
physically is or who to ask about it. None of this affects enrollment or
ingestion; it's operator reference data only.

## Adding a second probe

Repeat the "Probe setup" steps above -- generate another token, run
`setup.sh` again with it. That's the entire multi-probe story for v1;
nothing on the server side needs to change, and there's no per-probe
bookkeeping (UUIDs, tunnel IPs, cert files) left to track by hand.

## Deploying updates

**Server**: `./server/scripts/deploy.sh` -- pulls, rebuilds images, runs
any new migrations, recreates changed containers, prints the deployed
commit.

**Probe**: `./probe/scripts/deploy.sh` -- pulls, reinstalls dependencies
into the existing virtualenv, restarts the systemd service, tails
recent logs.

Both refuse to run if the working tree has uncommitted changes.

## Adding a real sensor driver

v1 ships mostly mock sensors (see "Known limitations" below). To add a
real one:

1. Add a module under `probe/weathernet_probe/sensors/` implementing
   the `Sensor` interface from `sensors/base.py` (a `sensor_type` and a
   `read()` method).
2. Add one entry mapping a config name to your new class in
   `sensors/registry.py`.
3. Add that name to the `sensors:` list in a probe's `probe.yaml`.

Nothing in `main.py` or `transport.py` needs to change.

### BME680 wiring (I2C)

The included `bme680_temperature`/`bme680_humidity`/`bme680_pressure`/
`bme680_gas` drivers (`weathernet_probe/sensors/bme680.py`) expect the
sensor wired for I2C. Most inexpensive BME680 breakout boards expose 6
pins:

| Sensor pin | Pi pin (40-pin header) | Notes |
|---|---|---|
| VCC  | Pin 1 (3.3V) | **Not** 5V (pins 2/4) -- the BME680 is a 3.3V part |
| GND  | Pin 6 or 9   | |
| SCL  | Pin 5 (GPIO3, SCL1) | |
| SDA  | Pin 3 (GPIO2, SDA1) | |
| SDO  | GND | Sets the I2C address to `0x76`, which the driver assumes |
| CS   | VCC (3.3V) | Forces I2C mode -- grounded or floating selects SPI instead |

Then enable I2C and confirm the sensor answers:

```bash
sudo raspi-config nonint do_i2c 0
sudo apt-get install -y i2c-tools
i2cdetect -y 1   # expect to see a device at address 76
```

To actually use it, edit `/etc/weathernet-probe/probe.yaml`'s `sensors:`
list (replace or add to the mock entries), then:

```bash
cd ~/weathernet/probe
source venv/bin/activate && pip install -r requirements.txt && deactivate
sudo systemctl restart weathernet-probe
```

`bme680`/`smbus2` (the driver's dependencies) are Linux/I2C-specific and
only actually get exercised once a `bme680_*` sensor is configured --
importing the driver module without them installed, or without the
sensor wired up, is harmless (see the module's docstring), but reading
one without the package installed raises a clear, per-sensor
`SensorReadError` rather than crashing the daemon.

### BMP280, HTU21D-F, SPS30 wiring (I2C)

The included `bmp280_*` (`weathernet_probe/sensors/bmp280.py`),
`htu21d_*` (`weathernet_probe/sensors/htu21d.py`) and `sps30_*`
(`weathernet_probe/sensors/sps30.py`) drivers all expect I2C, and all
three chips can share the same two-wire bus (Pi pins 3/SDA and 5/SCL)
since each answers to a different address -- no separate bus needed for
a breadboard bring-up with all three wired at once.

| Chip     | I2C address | VCC pin                | Notes |
|----------|-------------|-------------------------|-------|
| BMP280   | `0x77`      | Pin 1 (3.3V)             | See below -- this board's pin names and default address differ from a typical generic breakout. |
| HTU21D-F | `0x40`      | Pin 1 (3.3V)             | Fixed address, no SDO/address pin on this chip. **Not** 5V. |
| SPS30    | `0x69`      | **Pin 2 or 4 (5V)**      | See below -- this one is different from the other two. |

Common to all three: `GND` to Pin 6/9, `SCL` to Pin 5 (GPIO3/SCL1), `SDA`
to Pin 3 (GPIO2/SDA1).

BMP280 specifics -- this project's board is
[Adafruit #2651](https://www.adafruit.com/product/2651), which supports
both I2C and SPI off the same header, so its silkscreen doesn't say
`SDA`/`SCL` at all:

| Board pin | Function | Pi pin |
|---|---|---|
| `Vin` | Power (board has its own regulator, 3-5V in) | Pin 1 (3.3V) |
| `GND` | Ground | Pin 6 or 9 |
| `SCK` | I2C clock (doubles as SPI clock) | Pin 5 (GPIO3, SCL1) |
| `SDI` | I2C data (doubles as SPI MOSI) -- **this is your SDA** | Pin 3 (GPIO2, SDA1) |
| `SDO` | SPI-only (MISO) | Leave disconnected |
| `CS` | SPI-only (chip select) | Leave disconnected |

Leaving `SDO` disconnected matters beyond just "it's unused": this
board pulls `SDO` up to 3.3V through an onboard 10k resistor, so the
I2C address defaults to `0x77`, not the `0x76` a lot of cheaper generic
BMP280/BME280 breakouts default to (those instead pull `SDO`/`SDO`-
equivalent low unless you tie it to VCC). The driver
(`weathernet_probe/sensors/bmp280.py`) is set up for `0x77` accordingly
-- if you ever swap in a different breakout that defaults to `0x76`
instead, that's the one line to change. `3Vo` (the board's own
regulated 3.3V output) isn't used here -- it's meant for powering
*other* 3.3V-logic devices from this board, not an input.

SPS30 specifics -- it ships with a 5-pin JST ZHR connector, not breakout
header pins, and its pin assignment is `1=VDD, 2=SDA, 3=SCL, 4=SEL,
5=GND` (per Sensirion's datasheet Table 4 -- easy to get wrong since
several third-party wiring guides transcribe this table incorrectly).
Pin 1 is the pin closest to the sensor's body, pin 5 is closest to the
free end of the connector (Figure 1 in Sensirion's datasheet).

**Wire colors are not standardized across cables/vendors** -- don't
trust a generic color guide found online; verify against the datasheet
figure for your own cable. For the pigtail that shipped with this
project's sensor, counted from the sensor body outward, the colors are
black-red-white-yellow-orange:

| Pin | Wire color (this project's cable) | Pi pin | Notes |
|---|---|---|---|
| 1 (VDD) | black | Pin 2 or 4 (**5V**) | The sensor needs 5V to run its fan -- **not** 3.3V, unlike BMP280/HTU21D-F above. Unusual that black is the power wire here, not ground -- verified against the connector's physical pin 1/pin 5 positions, not assumed from color. |
| 2 (SDA) | red | Pin 3 (GPIO2, SDA1) | Shared with the other two chips' SDA. |
| 3 (SCL) | white | Pin 5 (GPIO3, SCL1) | Shared with the other two chips' SCL. |
| 4 (SEL) | yellow | GND | Selects I2C mode. Leave floating instead to select UART (not what this driver uses). |
| 5 (GND) | orange | Pin 6 or 9 | |

If you ever swap in a different SPS30 cable, re-derive this table from
the connector's physical pin 1/pin 5 position (per Sensirion's Figure
1) rather than assuming these same colors -- they're specific to this
cable, not a general SPS30 convention.

Running SPS30's I2C lines (SDA/SCL) at the Pi's 3.3V logic level despite
the sensor's own 5V supply is safe and doesn't need a level shifter --
Sensirion's datasheet documents the interface pins as "LVTTL 3.3V
compatible" regardless of `VDD`, unlike the supply pin itself.

Then, same as BME680:

```bash
sudo raspi-config nonint do_i2c 0
sudo apt-get install -y i2c-tools
i2cdetect -y 1   # expect to see devices at 40, 69, and 77
```

```bash
cd ~/weathernet/probe
source venv/bin/activate && pip install -r requirements.txt && deactivate
sudo systemctl restart weathernet-probe
```

A few things worth knowing about how these three differ from BME680:

- **BMP280 and HTU21D-F both report temperature.** Their `sensor_type`
  values are prefixed with the chip name (`bmp280_temperature_c`,
  `htu21d_temperature_c`) specifically so both can be wired and active
  at once -- e.g. to sanity-check one against the other during
  bring-up -- without colliding in storage or on a Grafana panel the
  way two `temperature_c` series would. BME680's own sensors keep their
  original generic names (`temperature_c`, etc.) for backward
  compatibility with already-stored data and the existing dashboard
  panels; this is a deliberate inconsistency, not an oversight.
- **SPS30 needs ~10 seconds after startup before its first real
  reading** -- the fan has to physically spin up and the airflow has to
  stabilize before a mass-concentration reading means anything. The
  driver returns a clear `SensorReadError` ("still warming up") during
  that window rather than a garbage value; this is expected on every
  daemon (re)start, not a fault.
- **SPS30's four `sps30_pm*` sensors share one physical device and one
  cached reading per cycle** (like BME680's four sensors do), since the
  chip only produces a new sample about once a second regardless of how
  many of the four are configured.
- **SPS30 self-recovers from a silent reset.** This driver only calls
  `start_measurement()` once, at process start -- if the chip's
  internal state ever gets reset without dropping off I2C long enough
  to notice (e.g. a brief brownout that wasn't quite enough to fail
  `i2cdetect`, but was enough to bounce the chip back to Idle-Mode), it
  would otherwise report "not ready" forever, since Idle-Mode never
  produces new samples on its own. If no fresh data has arrived for
  over 30s, the driver re-issues `start_measurement()` automatically
  and logs `SPS30 had no fresh data for over 30s -- it may have
  silently reset to Idle-Mode; re-issued start_measurement()`. Seeing
  this occasionally (e.g. around a power event) is the recovery
  working as intended, not a fault to chase; seeing it constantly
  points back to the power-supply checks in the SPS30 wiring notes
  above.

## Reaching a probe remotely

SSH into the server first (your existing access to it, unrelated to
WireGuard), then hop to the probe's tunnel IP:

```bash
ssh <server-user>@<server-public-ip>
ssh <probe-user>@10.10.0.x
```

If the server's `.env` has `SERVER_SSH_PUBLIC_KEY_HOST_PATH` pointing at
a real key (the default is `/home/ubuntu/.ssh/id_ed25519.pub`), the
second hop needs **no key setup at all** -- enrollment installs that
public key into the probe's `authorized_keys` automatically. If it
wasn't configured, or the probe was enrolled before that server setting
existed, the fallback is SSH agent forwarding from your own laptop
(so the private key never has to live on the server):

```bash
ssh-add -l                          # confirm your key is loaded locally
ssh -A <server-user>@<server-public-ip>
ssh <probe-user>@10.10.0.x          # uses the forwarded agent
```

The server is the only bastion into the WireGuard subnet for v1 -- your
own laptop isn't a peer. This works even for a probe you've deactivated
in Django Admin (`is_active` only gates telemetry ingestion, not
WireGuard reachability) and even if its mTLS certificate has been
revoked. A systemd timer installed during server setup re-runs
`wireguard/sync-peers.sh` every minute, so a newly enrolled probe (or
one whose WireGuard fields you edited by hand in Django Admin) becomes
reachable on its own -- run that script manually only if you don't want
to wait for the next tick.

## Known limitations of v1

- **Mostly mock sensors.** Real drivers exist for BME680 (temperature,
  humidity, pressure, gas resistance), BMP280 (temperature, pressure),
  HTU21D-F (temperature, humidity), and SPS30 (PM1.0/2.5/4.0/10
  particulate mass concentration) -- see "Adding a real sensor driver"
  above and `weathernet_probe/sensors/`. A wind vane/anemometer and a
  rain gauge are still not implemented.
- **Raspberry Pi only.** No Arduino / non-Linux probe support yet.
- **No certificate rotation or revocation.** Enrollment solves
  *issuance*, not the full lifecycle -- issued certs are long-lived,
  there's no renewal flow, and no CRL/OCSP. Deleting a `Probe` row stops
  ingestion (Django 404s it) but doesn't cryptographically invalidate
  the certificate -- nginx would still complete a TLS handshake with it.
- **No rate limiting on `/api/v1/enroll`.** The token itself (32 bytes
  of entropy) is the only defense against guessing; there's no request
  throttling on top of it.
- **The CA private key is readable by the `django` container.**
  Zero-touch enrollment needs it to sign CSRs synchronously -- a
  deliberate trade-off (see `PROJECT_SPEC.md` Section 5.7), not an
  oversight. A more isolated design would move signing into a separate,
  minimal process.
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
  the server, for a request to `/api/v1/ingest`. A `401` means no
  client certificate was presented at all; `403` means one was
  presented but failed verification against the internal CA.
- **Rejected ingests**: `docker compose logs django` on the server. A
  `404` means the probe's UUID isn't registered; `403` here means it's
  either inactive or the certificate CN doesn't match the payload's
  `probe_id` (same status code as the nginx-layer rejection above, but
  a different log to check).
- **Failed enrollment**: `docker compose logs django` on the server, for
  a request to `/api/v1/enroll`. `404` means the token doesn't match
  any `EnrollmentToken` (typo, or copied from the wrong Admin page);
  `410` means it's expired or was already used -- generate a new one.
  `400` usually means a CSR problem; `409` means `WIREGUARD_SUBNET` is
  exhausted. On the probe side, `enroll.py` prints the server's error
  detail directly, so the probe's own terminal output is often the
  fastest place to look first.
- **WireGuard tunnel not connecting**: run `sudo wg show wg0` on both
  the server and the probe. If the keys match on both sides but the
  server's peer entry shows no `endpoint`/`latest handshake` while the
  probe shows bytes sent but none received, the server is never
  actually seeing the probe's packets -- almost always the WireGuard UDP
  port not actually open. Confirm with
  `sudo tcpdump -i any udp port 51820 -n` on the server while the probe
  is running (it retries every 25s): no packets at all means the block
  is upstream (the cloud provider's security group); packets arriving
  but no handshake means something local is dropping them after
  arrival. **Check the actual firewall in use, not just `ufw`** -- some
  providers manage a local `iptables`/`nftables` ruleset automatically
  from their console's security group (`sudo iptables -L INPUT -n -v`
  or `sudo nft list ruleset`), and a rule added directly on the host may
  not survive a reboot or the next security-group sync -- the durable
  fix is opening the port in the provider's own console, not just
  locally. On the server, a missing peer entry entirely (not just a
  stuck handshake) means `wireguard/sync-peers.sh` hasn't been run since
  that probe's keys/IP were saved in Django Admin.

See [`server/README.md`](server/README.md) and
[`probe/README.md`](probe/README.md) for component-specific detail.
