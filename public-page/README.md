# WeatherNet public page

A self-contained PHP page for external, PHP-only hosting (e.g.
falberti.it) that shows current readings and a map publicly. It does
**not** run on the WeatherNet VM.

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
`sync.php` still fetches and stores a 24h history window per probe
(`history_json`) even though `index.php` doesn't currently render it
(a chart view was tried and removed) -- the data's already there if
that comes back later.

## What's published, and what isn't

Per probe: name, hardware type, latest temperature/humidity/pressure/
gas-resistance readings, the air quality index (the real EPA standard
when the probe has SPS30 PM data, otherwise the older BME680-only
heuristic -- `index.php`'s `aqi_label()` renders the two differently,
see its docblock), and
**coordinates rounded to `PUBLIC_LOCATION_PRECISION_DECIMALS`** (2 by
default, ~1km precision, set server-side) -- enough to place a probe
in its general area (and plot it on the map) without revealing which
building it's in.

Never published: exact address, owner email, owner phone. Those never
leave the VM's `/api/v1/public/*` responses in the first place (see
the Django views), so there's nothing to accidentally leak here even
if this page's template changes later.

## Telegram daily digest

A link in the footer (only shown if `TELEGRAM_BOT_USERNAME` is set)
opens `t.me/<username>` -- pressing Start there is the entire sign-up
flow. Everything after that happens inside Telegram, not on this page:
the bot asks for a place name, geocodes it (Nominatim, open, no API
key), and subscribes that chat to a daily summary if a probe is close
enough. See the VM-side `server/django_app/subscriptions/` app
(`bot.py` for the conversation, `management/commands/send_daily_digest.py`
for the once-a-day send) -- none of that logic lives here, this page
only carries the link.

## Layout

- `index.php` -- the page. Reads `probe_cache` via `db.php`, renders
  the current-reading cards and an OpenStreetMap/Leaflet map (marker
  per probe, colorable by temperature/humidity/pressure/air quality).
- `i18n.php` -- Italian/English/French/German strings plus
  `detect_locale()`. Priority: an explicit choice from the flag
  dropdown (`?lang=xx`, top-right on `index.php`) beats a remembered
  previous choice (`weathernet_lang` cookie, set only when a request
  carries a fresh `?lang=`) beats the browser's `Accept-Language`
  header beats English as the last-resort default. The cookie is
  first-party, stores only the language the visitor just explicitly
  picked, and is never set on a plain page load -- see the comment at
  the top of `i18n.php` for why that's exempt from cookie consent
  under ePrivacy/GDPR the same way the rest of this otherwise
  cookie-free site is.
- `sync.php` -- the cron entrypoint. Fetches both VM endpoints and
  upserts `probe_cache`. Refuses to run outside a CLI context (i.e. a
  browser hitting its URL directly gets a 403) -- `.htaccess` also
  blocks it as a second layer.
- `db.php` -- a few lines wrapping `PDO` construction, shared by both
  scripts above.
- `config.php` (you create this, gitignored) -- `VM_HOST`/`API_KEY`
  (`sync.php` only), `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASS` (both
  scripts), and `TELEGRAM_BOT_USERNAME` (`index.php` only). A `.php`
  file that `return`s an array, not a plain-text `.env` -- see the
  comment at the top of `config.php.example` for why that's the safer
  choice on generic PHP hosting: any PHP host executes `.php` files
  rather than serving their source, so this needs no `.htaccess` (or
  equivalent) rule to stay confidential, unlike a text file whose
  protection depends on the web server being Apache with
  `AllowOverride` enabled for this directory and the rule file having
  actually been uploaded correctly.
- `schema.sql` -- the one table this needs. Run it once.
- `.htaccess` -- blocks direct URL access to `config.php` (defense in
  depth, not the load-bearing protection -- see above), `*.pem`, and
  `sync.php`.

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

4. Copy `config.php.example` to `config.php` and fill in `VM_HOST`,
   `API_KEY` (from step 1), and the MySQL credentials from step 3. If
   the Telegram bot (see above) is set up, also set
   `TELEGRAM_BOT_USERNAME` to show the link -- leave it blank to hide it.

5. Upload `index.php`, `i18n.php`, `sync.php`, `db.php`, `config.php`,
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
   `config.php` and that `schema.sql` was actually run.

## The map

Leaflet.js + OpenStreetMap tiles, loaded from their public CDN (no API
key needed, unlike Google Maps). One marker per probe today; the radio
controls above the map recolor markers by whichever parameter is
selected, using the same data already on the page -- no extra request.
With a single probe this is a colored point, not a heatmap; the
multi-probe case (Section 12 of `PROJECT_SPEC.md`) reads from this
exact same per-probe coordinate/reading data, so a real interpolated
heatmap (e.g. the `leaflet.heat` plugin) can be added later without
changing what's fetched, just how it's drawn.
