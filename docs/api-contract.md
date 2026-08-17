# API Contract

Two endpoints, with different trust models -- see `server/nginx/nginx.conf.template`
for how nginx handles the difference at the TLS layer (only `/api/v1/ingest`
requires a client certificate; `/api/v1/enroll` and `/admin/` share the
same port without one).

Keep this document in sync with `server/django_app/probes/serializers.py`
/ `views.py` and `server/django_app/telemetry/serializers.py` / `views.py`.

## `POST /api/v1/enroll`

One-time, **not authenticated by client certificate** -- a brand-new
probe doesn't have one yet, that's the problem this endpoint exists to
solve (see `PROJECT_SPEC.md` Section 5.7). Trust comes entirely from a
short-lived, single-use token created in Django Admin.

### Request body

```json
{
  "token": "3f7a1c9e-...-raw-token-value",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...",
  "wireguard_public_key": "base64-wg-pubkey==",
  "detected_hardware_type": "raspberry_pi_4"
}
```

| Field                     | Type   | Notes                                                    |
|---------------------------|--------|-----------------------------------------------------------|
| `token`                   | string | The raw token, as printed by Django Admin. Never stored raw server-side -- only its SHA-256 hash is. |
| `csr_pem`                 | string | PEM-encoded PKCS#10 CSR. Its Subject is ignored -- the issued certificate's CN is always forced to the server-assigned probe UUID. |
| `wireguard_public_key`    | string | This probe's freshly generated WireGuard public key.    |
| `detected_hardware_type`  | string, optional | One of the `Probe.HardwareType` values. Purely a sanity check against what the operator declared when creating the token -- a mismatch is logged, never rejected. |

### Response `201`

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
  "report_interval_seconds": 300
}
```

`server_url` is a base URL (no path) -- the probe appends `/api/v1/ingest`
itself when writing `probe.yaml`. `wireguard.server_endpoint` (public
IP:port, for `Endpoint =`) and `wireguard.server_tunnel_ip` (this
server's own WireGuard address, for this probe's `AllowedIPs =`) are
two different addresses -- don't conflate them when rendering `wg0.conf`.

### Responses

| Status | Meaning                                                      | Body |
|--------|----------------------------------------------------------------|------|
| `201`  | Enrolled -- see above.                                         | as above |
| `400`  | Malformed payload, or `csr_pem` doesn't parse / isn't validly self-signed. | `{"detail": "..."}` or serializer errors |
| `404`  | No token matches the given value's hash.                       | `{"detail": "..."}` |
| `410`  | Token exists but is expired or already used.                   | `{"detail": "..."}` |
| `409`  | Every address in `WIREGUARD_SUBNET` is already assigned (extremely unlikely at this project's scale). | `{"detail": "..."}` |

### Side effects on success

- A new `Probe` row: `id` = a freshly generated UUID (also the issued certificate's CN), `name`/`hardware_type` copied from the token, `wireguard_public_key` from the request, `wireguard_tunnel_ip` = the next free address in `WIREGUARD_SUBNET`.
- The `EnrollmentToken` row is marked `used_at = now()`, `resulting_probe` = the new `Probe`. This happens atomically with the check above (`select_for_update()`), so the same token redeemed concurrently twice succeeds exactly once.
- Nothing is written to disk on the server for the new probe -- the signed cert only ever exists as this response body.

## `POST /api/v1/ingest`

Every reporting cycle, **mTLS-authenticated**. By the time a request
reaches this view, nginx's `/api/v1/ingest` location block has already
rejected anything without a valid client certificate (`401` no cert,
`403` invalid cert -- see `server/nginx/nginx.conf.template`); this
endpoint only ever sees pre-authenticated requests.

### Request headers

```
Content-Type: application/json
X-Client-Cert-CN: <set by nginx from the verified client certificate>
```

`X-Client-Cert-CN` is set by nginx after successful mTLS client
certificate verification -- probes never set this header themselves,
and nginx overwrites any client-supplied value for it.

### Request body

```json
{
  "probe_id": "b3f2c1a0-1234-4a5b-8c9d-0123456789ab",
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

| Field                  | Type                 | Notes                                          |
|-------------------------|----------------------|-------------------------------------------------|
| `probe_id`              | UUID string          | Must equal `X-Client-Cert-CN`.                  |
| `timestamp`              | ISO 8601 datetime (UTC, `Z` suffix) | Applied to every reading and the health row in this request. |
| `readings`               | array                | May be empty (e.g. every sensor failed this cycle). |
| `readings[].sensor_type` | string                | Free-form identifier, e.g. `temperature_c`.     |
| `readings[].value`       | float                 |                                                  |
| `health.cpu_temp_c`      | float or `null`       | `null` when the device has no thermal zone file. |
| `health.cpu_percent`     | float                 |                                                  |
| `health.mem_percent`     | float                 |                                                  |
| `health.disk_percent`    | float                 | Root filesystem.                                |
| `health.uptime_seconds`  | integer               | Seconds since the probe daemon started (not OS uptime). |

### Validation and identity checks (in order)

1. `X-Client-Cert-CN` must be present -- `403` otherwise (defense in depth; nginx already guarantees this in practice).
2. Body must match the schema above -- `400` with a JSON error body otherwise.
3. `probe_id` must equal `X-Client-Cert-CN` -- `403` otherwise. A probe
   must never be able to report data under another probe's identity,
   even if it somehow held a valid certificate.
4. A `Probe` with `id == probe_id` must exist -- `404` otherwise.
5. That `Probe` must have `is_active = true` -- `403` otherwise.

### Responses

| Status | Meaning                                         | Body                |
|--------|----------------------------------------------------|----------------------|
| `201`  | Accepted and written.                            | empty                |
| `400`  | Malformed payload.                               | `{"field": ["error"]}` style, from the serializer |
| `403`  | Missing/mismatched identity, or inactive probe.  | `{"detail": "..."}`  |
| `404`  | Unknown probe.                                   | `{"detail": "..."}`  |

### Side effects on success

- One `SensorReading` row per entry in `readings`.
- One `ProbeHealth` row for `health`.
- The `Probe` row's `last_seen_at` is set to the current server time and
  `last_health_summary` is replaced with the `health` object from this
  request.
