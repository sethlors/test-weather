# Weather History Dashboard

An interactive dashboard for browsing historical and **live** readings from a
Davis weather station — a static page (GitHub Pages, no backend of its own)
that reads from a [Supabase](https://supabase.com) Postgres database.

The browser queries Supabase directly over its REST API (aggregation happens
server-side in Postgres RPC functions — see `supabase/schema.sql`) and
subscribes to realtime `INSERT`s on the `readings` table, so a new reading
appears in the UI within moments of the station posting it — no polling, no
scheduled sync.

## Where the data comes from

The weather station's Windows software (Davis WeatherLink) renders its readings
into `Detail-All.htm` from a template (`Detail-All.htx`) every ~5 minutes. In
the `Davis_Weather` repo, `scripts/push_reading.py` parses that file the moment
it's written and pushes the reading straight to Supabase — see
`PushWeatherData2Github.bat`, which calls it before the (still-kept, for
archival) git commit/push of the station's own files.

```
Detail-All.htx (template) ─┐
                           ├─► push_reading.py ─► Supabase `readings` table ─► index.html (REST + realtime)
Detail-All.htm (station output) ─┘
```

Historical data (collected by the previous git-polling pipeline) was backfilled
once with `scripts/backfill_supabase.py`. `scripts/build_weather_db.py` /
`make_lean_db.py` / `sync_from_remote.py` are that retired pipeline, kept only
because `backfill_supabase.py` reads the lean db shape they produced.

## Files

| Path | What it is |
|------|-----------|
| `index.html` | The dashboard (loads `supabase-js` + uPlot, queries Supabase) |
| `static/` | Vendored `supabase-js`, `uPlot`, `Leaflet` + `protomaps-leaflet` — all self-hosted |
| `static/basemap/station.pmtiles` | Street-level vector basemap around the home station (~3.5 MB), cut from the Protomaps planet build |
| `static/basemap/iowa.pmtiles` | State-wide, lower-detail basemap (~11 MB) used when the station picker selects somewhere outside `station.pmtiles`' bounds |
| `supabase/schema.sql` | `readings` table, RLS policies, and the RPC functions the dashboard calls (bounds/latest/raw/bucketed series) |
| `scripts/backfill_supabase.py` | One-time copy of a lean `weather.db`'s rows into Supabase |
| `scripts/build_weather_db.py`, `make_lean_db.py`, `sync_from_remote.py`, `weather_ts.py` | The retired git-polling pipeline; kept for reference and because `backfill_supabase.py` depends on the lean db shape they defined |
| `scripts/fetch_basemap.sh` | Regenerates `station.pmtiles` (only needed if the home station moves) |
| `scripts/fetch_iowa_basemap.sh` | Regenerates `iowa.pmtiles` (only needed if `IA_STATIONS` in `index.html` grows past its bounding box) |
| `.github/workflows/sync.yml` | Deploys the static site to Pages on push to `main` (no data sync — that's live now) |

## Metrics included

Outside temperature, dew point, wind chill, heat index, outside humidity,
barometer, wind speed, rain rate, and day/storm/month/year rain totals.

Plus two **text** fields, which aren't charted and exist for the dashboard view:
`windDirection` (16-point compass string, e.g. `SSW`) and `barTrend` (the
station's own phrase, e.g. `Falling Slowly`). Neither is derivable from the
numeric series, so the station's reported value is the only source. The station
writes `---` when it has no reading; both builders normalize that to `NULL`.

(Inside temp/humidity aren't available — the source page only embeds those as
images, with no text value to extract.)

## Other Iowa stations

The **Station** picker lets you view a curated list of ~19 Iowa airport
ASOS/AWOS stations instead of the home station, sourced from
[api.weather.gov](https://www.weather.gov/documentation/services-web-api) --
no API key, CORS-open, queried straight from the browser (no backend
involved). Two real limits, both surfaced in the UI when picking one:

- **~7 days of history only** (`NWS_HISTORY_DAYS` in `index.html`) -- it's a
  rolling observation cache, not an archive. Longer range presets are
  disabled, not hidden, so switching stations doesn't shift the layout.
- **No rain data** -- ASOS doesn't report cumulative rain totals in a
  comparable shape, so the four rain metrics are only offered for the home
  station.

Picking a station also updates the locator map: most of `IA_STATIONS` fall
outside `station.pmtiles`' tight bounding box (it's cut close around the home
station on purpose, for street-level detail), so `setMapStation()` swaps to
the wider `iowa.pmtiles` basemap, widens the pannable bounds/zoom range, and
drops a second (static, non-pulsing) pin for the picked station -- reverting
to the tight home view when you switch back to "Davis Weather".

`api.weather.gov`'s `/observations` endpoint caps each response at 500
entries and hands back a `pagination.next` cursor for the rest --
`fetchNwsObservations()` follows it, since 500 only covers ~1.7 days at the
~5-minute cadence these stations report at, well short of the 7-day window
above.

The home station's `readings.ts` is wall-clock time stored *as* UTC (see
Timestamps below); NWS timestamps are genuine UTC. `realInstantToWallEpoch()`
/ `wallEpochToRealInstant()` in `index.html` convert between the two (via
`STATION.tz`, so it's DST-aware) so both sources land on the same x-axis.

### Adding a column

Add the column to the Postgres `readings` table (`ALTER TABLE ... ADD COLUMN`,
mirrored in `supabase/schema.sql` for anyone re-provisioning), then add it in
**both** `Davis_Weather/scripts/push_reading.py` (so new readings populate it)
and any RPC function in `supabase/schema.sql` that needs to expose it. Rows
written before the column existed keep `NULL`.

## Two views

The header toggles between them, and the choice is remembered in
`localStorage`:

- **History** — the metric picker, range presets, chart, stats and locator map.
- **Dashboard** — a glanceable, tablet-sized read of the newest sample only:
  wind compass, hero temperature, barometer + trend, wind chill / humidity /
  heat index, and the four rainfall totals. New readings arrive live via the
  Supabase realtime subscription while it's the active view; a 5-minute poll
  (`DASH_REFRESH_MS`) is a fallback only, in case the websocket drops
  silently. Every tile is a link into that metric's history.

## Timestamps

Readings are stored as their local wall-clock time treated as UTC, and the chart
renders its x-axis in UTC. This keeps displayed times and daily min/max day
boundaries aligned to local midnight for every viewer, regardless of their
browser's timezone.

The conversion lives in exactly one place — `scripts/weather_ts.py` — so the
historical bootstrap (which reads git author dates) and the incremental sync
(which reads the reading time out of each commit message) can't drift apart and
produce off-by-a-few-seconds duplicate rows. Both feed the same
`epoch_from_*` helpers, which discard the timezone offset and keep only the
wall-clock fields.

## Station location & day/night shading

The dashboard shows where the station is, and uses that position for something
beyond decoration: **sunrise and sunset are computed from the coordinates**
(NOAA's sunrise equation, ~30 lines of arithmetic, no dependency), and the hours
between sunset and sunrise are shaded behind the chart. The daily temperature
swing then reads as an obvious consequence of the sun rather than an
unexplained wave.

Shading is suppressed on ranges longer than 16 days, where the bands would
compress into a grey smear, and on the daily-total rain bars, where "night"
has no meaning.

**Coordinates are deliberately rounded to ~1 km** (2 decimal places) in both
`index.html` and `scripts/fetch_basemap.sh`. This is a public site and the
station is at a residence; full precision would publish an address while buying
nothing — at the map's maximum zoom a finer fix isn't distinguishable, and sun
times shift by well under a second.

### Why the basemap is a local file

A normal map fetches tiles from a third-party server on every page load. This
project avoids that for the map specifically (the weather data itself is now a
third-party request, to Supabase) by cutting a small region out of the
[Protomaps](https://protomaps.com) planet build once (`scripts/fetch_basemap.sh`)
and committing it as a single `.pmtiles` archive, read via HTTP range requests.

This is also why the tiles aren't scraped from OpenStreetMap's standard tile
server: [its usage policy](https://operations.osmfoundation.org/policies/tiles/)
prohibits pre-seeding areas and zoom levels. The Protomaps basemap is a Produced
Work of OSM data under **ODbL**, which permits this redistribution provided the
map visibly attributes *© OpenStreetMap* — it does, in the corner of the map.

Range requests mean the archive's size costs visitors almost nothing: the
browser fetches only the handful of tiles on screen (~200 KB), not the whole
3.5 MB.

> **Version pinning matters here.** `protomaps-leaflet` **5.x** speaks the v4
> tile schema (bare `kind` attributes) but ships no built-in themes, so the paint
> rules in `index.html` are hand-written against the page's design tokens.
> **4.x** has themes but only understands the older `pmap:`-prefixed v2 schema —
> pairing it with this v4 tileset renders a near-empty map with no error. The
> library version and the tileset schema have to move together.

## Running locally

Fill in `SUPABASE_URL` / `SUPABASE_ANON_KEY` near the top of `index.html`
(see "Supabase setup" below), then:

```bash
npx http-server . -p 8123 -c-1
# open http://localhost:8123
```

> Use a server that supports **HTTP range requests**. The `.pmtiles` basemap is
> read a few byte ranges at a time, and Python's `http.server` answers `Range:`
> with the whole file, so the map silently renders blank under
> `python3 -m http.server`. GitHub Pages does serve ranges (`206 Partial
> Content`), so this only bites in local dev.

## Deploying to GitHub Pages

The site is built and published by the **GitHub Actions Pages pipeline**.
Enable it once:

1. Repo **Settings → Pages**
2. **Source: GitHub Actions**

The site publishes at `https://<user>.github.io/test-weather/`.
`.github/workflows/sync.yml` just assembles `index.html` + `static/` and
deploys on every push to `main` — there's no data sync step, since the
dashboard reads Supabase live at page-load time.

## Supabase setup

1. Create a project at [supabase.com](https://supabase.com) (free tier).
2. Run `supabase/schema.sql` in the SQL editor — creates the `readings`
   table, RLS policies, and the RPC functions the dashboard calls.
3. Project Settings → API: copy the **Project URL** and **`anon` public key**
   into `SUPABASE_URL` / `SUPABASE_ANON_KEY` in `index.html`. Safe to publish —
   RLS restricts that key to `SELECT`.
4. Copy the **`service_role` key** (secret — never commit it) and set it, plus
   the project URL, as **Windows environment variables on the station PC**
   (`setx SUPABASE_URL "..."` / `setx SUPABASE_SERVICE_KEY "..."`). That's what
   `Davis_Weather/scripts/push_reading.py` writes with.
5. One-time backfill of existing history — grab the lean db the old pipeline
   built (it lives on the `weather-data` branch) and push it in:
   ```bash
   git fetch origin weather-data
   git show origin/weather-data:weather.db > weather.db   # gitignored; local only
   SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python3 scripts/backfill_supabase.py --db weather.db
   ```
