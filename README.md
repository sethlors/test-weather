# Weather History Dashboard

An interactive, **fully static** dashboard for browsing historical readings
from a Davis weather station — hosted on GitHub Pages with **no backend**.

The SQLite database is queried **directly in the browser** using
[sql.js](https://github.com/sql-js/sql.js) (SQLite compiled to WebAssembly).
The page fetches `weather.db` once, loads it into memory, and runs SQL against
it as you pan/zoom — so any static file host (GitHub Pages included) can serve
the whole thing.

## Where the data comes from

The weather station's Windows software (Davis WeatherLink) renders its readings
into `Detail-All.htm` from a template (`Detail-All.htx`) every ~5 minutes, and a
`.bat` script commits that file to git. That means **git history already holds a
5-minute-resolution time series** going back months — the numbers are sitting in
the HTML as plain text (not just in the gauge images).

`scripts/build_weather_db.py` walks that git history, extracts every reading by
matching the template's placeholders, and writes a full (~80-column) SQLite db.
`scripts/make_lean_db.py` then slims it to just the charted metrics for the web.

```
Detail-All.htx (template)  ─┐
                            ├─► build_weather_db.py ─► full weather.db ─► make_lean_db.py ─► weather.db (~4.5 MB, lives on the weather-data branch)
git history of Detail-All.htm ┘
```

## Files

| Path | What it is |
|------|-----------|
| `index.html` | The dashboard (loads sql.js + uPlot, queries `weather.db`) |
| `weather.db` | Lean SQLite db (`ts` + 12 metrics, ~56k rows) — **not tracked on `main`**; it's maintained on the `weather-data` branch and baked into the Pages artifact at deploy |
| `static/` | Vendored `sql.js` (wasm), `uPlot`, `Leaflet` + `protomaps-leaflet` — all self-hosted |
| `static/basemap/station.pmtiles` | Vector basemap for the locator map (~3.5 MB), cut from the Protomaps planet build |
| `scripts/build_weather_db.py` | Full extractor from `Detail-All.htm` git history (one-time build) |
| `scripts/make_lean_db.py` | Full db → lean web db |
| `scripts/sync_from_remote.py` | Pulls new readings from the live repo and appends them |
| `scripts/weather_ts.py` | The one canonical wall-clock→epoch rule, shared by the builders and the sync |
| `scripts/fetch_basemap.sh` | Regenerates the basemap tileset (only needed if the station moves) |
| `.github/workflows/sync.yml` | Scheduled Action that syncs new readings and deploys the site |

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

### Adding a column

The published db is restored from the `weather-data` branch and appended to in
place — it is never rebuilt from the full db — so a new column reaches
production only through `ensure_schema()` in `sync_from_remote.py`, which
`ALTER`s it in on the next run. Add it in **both** `make_lean_db.py` and
`sync_from_remote.py` or a rebuilt db and a synced db will diverge. Rows written
before the column existed keep `NULL`; backfilling would mean refetching every
historical commit from the API.

## Two views

The header toggles between them, and the choice is remembered in
`localStorage`:

- **History** — the metric picker, range presets, chart, stats and locator map.
- **Dashboard** — a glanceable, tablet-sized read of the newest sample only:
  wind compass, hero temperature, barometer + trend, wind chill / humidity /
  heat index, and the four rainfall totals. Because it's meant to be left
  running on a wall-mounted tablet, it refetches the db every 15 minutes (the
  sync cadence) while it's the active view and the page isn't backgrounded.
  Every tile is a link into that metric's history.

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

A normal map fetches tiles from a third-party server on every page load, which
would undo the property this project otherwise maintains: the published page
makes **no third-party requests**. So instead a small region is cut out of the
[Protomaps](https://protomaps.com) planet build once (`scripts/fetch_basemap.sh`)
and committed as a single `.pmtiles` archive, read via HTTP range requests —
the same "one file on static storage" shape as `weather.db`.

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

The db isn't tracked on `main`, so grab the current one from the `weather-data`
branch first:

```bash
git fetch origin weather-data
git show origin/weather-data:weather.db > weather.db   # gitignored; local dev only

npx http-server . -p 8123 -c-1
# open http://localhost:8123
```

> Use a server that supports **HTTP range requests**. `weather.db` is loaded up
> front and doesn't need them, but the `.pmtiles` basemap is read a few byte
> ranges at a time — and Python's `http.server` answers `Range:` with the whole
> file, so the map silently renders blank under `python3 -m http.server`.
> GitHub Pages does serve ranges (`206 Partial Content`), so this only bites in
> local dev.

## Deploying to GitHub Pages

The site is built and published by the **GitHub Actions Pages pipeline** —
there's no hosting branch to manage. Enable it once:

1. Repo **Settings → Pages**
2. **Source: GitHub Actions**

The site publishes at `https://<user>.github.io/test-weather/`.
The `.nojekyll` file ensures `static/` and the `.wasm` are served as-is.

`main` holds only source (code + vendored assets) — **not** the database. The
accumulating `weather.db` lives on a dedicated **`weather-data`** branch that is
force-pushed as a single commit, so it never bloats `main`'s history and isn't a
hosting branch either. The deploy job assembles the site (`index.html` +
`static/` + the current `weather.db`) into a Pages artifact at publish time.

> First-time setup only: the `weather-data` branch must be seeded once with an
> initial `weather.db` (build one with `build_weather_db.py` → `make_lean_db.py`,
> or copy an existing lean db), e.g.
> `git switch --orphan weather-data && git add weather.db && git commit && git push -u origin weather-data`.
> Every run after that maintains it automatically.

## Keeping it updated automatically

`weather.db` is a static file, so something has to refresh it when new readings
arrive. This repo does that entirely on its own — no access to the weather
machine or the source repo's settings required.

`.github/workflows/sync.yml` runs on a ~15-minute schedule (and on every push to
`main`, and on demand via **Run workflow**). Each run:

1. restores the accumulated db from the `weather-data` branch,
2. reads the newest timestamp in it,
3. asks the GitHub API for commits of `Detail-All.htm` in the live repo
   (`tvLors/Davis_Weather`) newer than that,
4. fetches each of those commits' raw `Detail-All.htm`, parses the metrics, and
   appends the rows (`INSERT OR IGNORE`),
5. **only if the data actually changed** (or it's a manual/code push):
   force-pushes the updated db back to `weather-data` and deploys a fresh site
   artifact to Pages.

Because it captures **every commit** since the last run — not just whatever the
"current" reading happens to be — no 5-minute sample is lost even when GitHub's
scheduler delays, coalesces, or skips runs. **Cadence only affects freshness of
the latest point, never completeness of the history.** The change-gated deploy
means idle scheduled runs are true no-ops, keeping us well under Pages
deployment rate limits. The sync step writes a freshness summary (newest reading
+ gap closed) to the run's **Summary** tab for at-a-glance observability.

### A note on scheduler reliability

GitHub's `schedule:` cron is **best-effort**: runs are commonly delayed under
load and are silently disabled after ~60 days of repo inactivity. The backfill
design makes this a *freshness*, never a *completeness*, problem — a late run
still ingests everything it missed. Any push or a manual **Run workflow** also
re-arms a disabled schedule. If you ever need hard freshness guarantees, point
an external scheduler (cron-job.org, UptimeRobot, a Cloudflare Worker cron, …)
at the workflow's `workflow_dispatch` API endpoint; the workflow is already
idempotent and safe to trigger as often as you like.
