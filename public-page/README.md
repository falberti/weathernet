# WeatherNet public page

A single self-contained PHP page for external, PHP-only hosting (e.g.
falberti.it) that shows current readings publicly. It does **not**
run on the WeatherNet VM -- it's a separate, small server-side client
of the VM's `/api/v1/public/summary` endpoint (see
[`probes/views.py`](../server/django_app/probes/views.py)
`PublicSummaryView` and
[`docs/api-contract.md`](../docs/api-contract.md)).

Why this instead of exposing Grafana or moving ports around: the VM's
own TLS certificate is signed by this project's private CA (needed for
probe mTLS), so it's not publicly trusted -- any browser landing on the
VM directly would show a certificate warning. This page's *own*
domain has its own regularly-issued certificate, so visitors never see
the VM at all; the one connection to the VM (server-side, PHP to
Django) is verified against the private CA explicitly, not left
unverified.

## What's published, and what isn't

Per probe: name, hardware type, latest temperature/humidity/pressure/
gas-resistance readings, the heuristic air quality score, and
**coordinates rounded to `PUBLIC_LOCATION_PRECISION_DECIMALS`**
(2 by default, ~1km precision) -- enough to place a probe in its
general area without revealing which building it's in.

Never published: exact address, owner email, owner phone. Those never
leave the `/api/v1/public/summary` response in the first place (see
the view), so there's nothing to accidentally leak here even if this
page's template changes later.

## Deploy

1. On the VM, generate an API key and put it in `server/.env`:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Set `PUBLIC_SUMMARY_API_KEY=<that value>` in `server/.env`, then
   `docker compose restart django`. (A fresh install's `setup.sh`
   generates this automatically -- this manual step is only needed
   because the endpoint was added after your server was already set up.)

2. Copy the VM's CA certificate (public, not secret) to your machine:
   ```bash
   scp <vm-user>@<vm-ip>:~/weathernet/server/pki/ca/ca.cert.pem ./weathernet-ca.pem
   ```

3. Edit `index.php`'s configuration constants near the top:
   - `VM_HOST` -- the VM's public IP (or hostname).
   - `API_KEY` -- the value from step 1.

4. Upload `index.php` and `weathernet-ca.pem` (same directory) to your
   PHP hosting. `summary-cache.json` is created automatically next to
   them on first request -- make sure the directory is writable by
   PHP, or the page still works, it just re-fetches every request
   instead of caching for `CACHE_TTL_SECONDS`.

5. Open the page. If it shows "Dati momentaneamente non disponibili",
   check your host's PHP error log -- `fetch_summary()` logs the HTTP
   status and curl error for every failed attempt (wrong API key,
   VM unreachable, CA file missing/wrong path, etc.).

## Multi-probe heatmap (later)

The endpoint already returns a `probes` array with `latitude`/
`longitude`/readings per probe, specifically so that adding more
probes later doesn't require an API change -- just this page (or a
follow-up one) plotting more than one card, e.g. with Leaflet.js
reading the same JSON. Out of scope for now with a single probe.
