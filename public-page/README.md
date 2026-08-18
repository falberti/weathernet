# WeatherNet public page

A self-contained PHP page for external, PHP-only hosting (e.g.
falberti.it) that shows current readings, a map, and recent trend
charts publicly. It does **not** run on the WeatherNet VM.

## Architecture

Two scripts with very different jobs:

- **`sync.php`** -- run every 10 minutes by your hosting's cron. The
  only thing here that ever talks to the VM: fetches
  `/api/v1/public/summary` and `/api/v1/public/history` (see
  [`probes/views.py`](../server/django_app/probes/views.py)
  `PublicSummaryView` / `PublicHistoryView` and
  [`docs/api-contract.md`](../docs/api-contract.md)) and upserts the
  result into a local MySQL table, `probe_cache` (`schema.sql`).
- **`index.php`** -- what visitors see. Reads only from that MySQL
  table, never from the VM. A burst of visitors here never becomes a
  burst of requests against the VM -- the cron interval is the only
  thing that talks to it, at a fixed, predictable rate.

This also solves the certificate problem cleanly: the VM's own TLS
certificate is signed by this project's private CA (needed for probe
mTLS), so it's not publicly trusted -- a browser landing on the VM
directly would show a warning. Only `sync.php` (server-side, PHP to
Django, verified against the private CA explicitly -- see below) ever
makes that connection; visitors only ever see this page's own
(regularly issued) certificate.

`probe_cache` holds one row per probe, overwritten on every `sync.php`
run -- it's a cache of the latest fetch, not an accumulating history.
A real local history (e.g. one row per reading per run, kept instead
of overwritten) is a natural extension once that's actually wanted;
not built now on purpose, per **`in futuro potremo avere uno storico
dei dati sul MySQL, ma per ora teniamo solo il dato più fresco`**.

## What's published, and what isn't

Per probe: name, hardware type, latest temperature/humidity/pressure/
gas-resistance readings, the heuristic air quality score, a 24h trend
per reading, and **coordinates rounded to
`PUBLIC_LOCATION_PRECISION_DECIMALS`** (2 by default, ~1km precision,
set server-side) -- enough to place a probe in its general area (and
plot it on the map) without revealing which building it's in.

Never published: exact address, owner email, owner phone. Those never
leave the VM's `/api/v1/public/*` responses in the first place (see
the Django views), so there's nothing to accidentally leak here even
if this page's template changes later.

## Telegram daily digest

A banner at the top of the page (only shown if `TELEGRAM_BOT_USERNAME`
is set in `.env`) links to `t.me/<username>` -- opening a chat with
the bot and pressing Start. Everything after that happens entirely
inside Telegram, not on this page: the bot asks for a place name,
geocodes it (Nominatim, open, no API key), and subscribes that chat to
a daily summary if a probe is close enough. See the VM-side
`server/django_app/subscriptions/` app (`bot.py` for the conversation,
`management/commands/send_daily_digest.py` for the once-a-day send) --
none of that logic lives here, this page only carries the link.

## Layout

- `index.php` -- the page. Reads `probe_cache` via `db.php`, renders
  the current-reading cards, an OpenStreetMap/Leaflet map (marker per
  probe, colorable by temperature/humidity/pressure/air quality), and
  a Chart.js line chart per reading type per probe.
- `sync.php` -- the cron entrypoint. Fetches both VM endpoints and
  upserts `probe_cache`. Refuses to run outside a CLI context (i.e. a
  browser hitting its URL directly gets a 403) -- `.htaccess` also
  blocks it as a second layer.
- `db.php` -- a few lines wrapping `PDO` construction, shared by both
  scripts above.
- `env.php` -- a ~15-line dependency-free `.env` parser (`KEY=VALUE`
  per line). Not a secrets file itself, just the loader.
- `.env` (you create this, gitignored) -- `VM_HOST`/`API_KEY`
  (`sync.php` only), `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASS` (both
  scripts), and `TELEGRAM_BOT_USERNAME` (`index.php` only, just to
  build the `t.me/` link -- not sensitive, kept here anyway for a
  single place to change it).
- `schema.sql` -- the one table this needs. Run it once.
- `.htaccess` -- blocks direct URL access to `.env`, `*.pem`, and
  `sync.php`. `.env` in particular holds real credentials now (the API
  key and the MySQL password), so this one actually matters.

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

3. In your hosting control panel, create a MySQL database and user,
   then run `schema.sql` against it (phpMyAdmin's "Import" tab, or the
   `mysql` CLI if you have shell access).

4. Copy `.env.example` to `.env` and fill in `VM_HOST`, `API_KEY`
   (from step 1), and the MySQL credentials from step 3. If the
   Telegram bot (see below) is set up, also set `TELEGRAM_BOT_USERNAME`
   to show the subscribe banner -- leave it blank to hide it.

5. Upload `index.php`, `sync.php`, `db.php`, `env.php`, `.env`,
   `weathernet-ca.pem`, and `.htaccess` (all in the same directory) to
   your PHP hosting.

6. Add a cron job for `sync.php`, type **PHP** (not HTTP/HTTPS -- this
   runs it via the CLI, which is also what makes `sync.php`'s own
   "refuse to run outside CLI" guard let it through). Command: just
   `sync.php` (the UI already prefixes `./`). Frequency: manual
   configuration, `*/10` in **Minuto**, `*` everywhere else -- your
   panel's manual mode has a 10-minute floor, matching the "every 5 or
   10 minutes" ask.

7. Trigger it once by hand if your panel allows a manual "run now", or
   just wait up to 10 minutes, then open the page. If it still shows
   "Dati momentaneamente non disponibili": check your host's cron
   execution log or PHP error log for what `sync.php` printed (it logs
   the HTTP status and curl error on a failed VM fetch, and the MySQL
   error on a failed connection); or query the VM directly to rule
   that leg out:
   ```bash
   curl -s -H "X-Api-Key: <key>" https://<vm-ip>/api/v1/public/summary \
     --cacert server/pki/ca/ca.cert.pem
   ```
   If that returns data but the page still doesn't, the problem is
   between `sync.php` and MySQL -- double-check the `DB_*` values in
   `.env` and that `schema.sql` was actually run.

## The map

Leaflet.js + OpenStreetMap tiles, loaded from their public CDNs (no
API key needed, unlike Google Maps). One marker per probe today; the
radio controls above the map recolor markers by whichever parameter is
selected, using the same data already on the page -- no extra request.
With a single probe this is a colored point, not a heatmap; the
multi-probe case (Section 12 of `PROJECT_SPEC.md`) reads from this
exact same per-probe coordinate/reading data, so a real interpolated
heatmap (e.g. the `leaflet.heat` plugin) can be added later without
changing what's fetched, just how it's drawn.

## The charts

Chart.js, also from its public CDN. One line chart per reading type
(temperature/humidity/pressure/gas resistance) per probe, over the
last `HISTORY_WINDOW_HOURS` (24 by default, change the constant near
the top of `index.php` -- and the matching one in `sync.php`, they're
independent constants on purpose since one controls what's fetched and
the other just what's displayed). No air quality index chart -- it's a
score relative to a baseline computed as of *now* (see `probes/aqi.py`
on the VM), so there isn't a meaningful historical value to plot for
it without a lot more query complexity server-side.
