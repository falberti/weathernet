# Ingestion API Contract

This is the formal schema for the only endpoint a probe ever calls. Keep
this document and `server/django_app/telemetry/serializers.py` /
`server/django_app/telemetry/views.py` in sync.

## `POST /api/v1/ingest`

Reachable only through nginx's mTLS-terminating proxy on port 443; not
reachable directly (Django is not published to the host).

### Request headers

```
Content-Type: application/json
X-Client-Cert-CN: <set by nginx from the verified client certificate's CN>
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

1. `X-Client-Cert-CN` must be present -- `403` otherwise.
2. Body must match the schema above -- `400` with a JSON error body otherwise.
3. `probe_id` must equal `X-Client-Cert-CN` -- `403` otherwise. A probe
   must never be able to report data under another probe's identity,
   even if it somehow held a valid certificate.
4. A `Probe` with `id == probe_id` must exist -- `404` otherwise.
5. That `Probe` must have `is_active = true` -- `403` otherwise.

### Responses

| Status | Meaning                                         | Body                |
|--------|--------------------------------------------------|----------------------|
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
