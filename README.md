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
                            ├─► build_weather_db.py ─► full weather.db ─► make_lean_db.py ─► weather.db (this repo, ~4.5 MB)
git history of Detail-All.htm ┘
```

## Files

| Path | What it is |
|------|-----------|
| `index.html` | The dashboard (loads sql.js + uPlot, queries `weather.db`) |
| `weather.db` | Lean SQLite db: `ts` + 12 metrics, ~56k rows |
| `static/` | Vendored `sql.js` (wasm) and `uPlot` — all self-hosted |
| `scripts/build_weather_db.py` | Full extractor from `Detail-All.htm` git history (one-time build) |
| `scripts/make_lean_db.py` | Full db → lean web db |
| `scripts/sync_from_remote.py` | Pulls new readings from the live repo and appends them |
| `.github/workflows/sync.yml` | Scheduled Action that runs the sync every ~5 min |

## Metrics included

Outside temperature, dew point, wind chill, heat index, outside humidity,
barometer, wind speed, rain rate, and day/storm/month/year rain totals.

(Inside temp/humidity aren't available — the source page only embeds those as
images, with no text value to extract.)

## Timestamps

Readings are stored as their local wall-clock time treated as UTC, and the chart
renders its x-axis in UTC. This keeps displayed times and daily min/max day
boundaries aligned to local midnight for every viewer, regardless of their
browser's timezone.

## Running locally

Because the whole db is loaded up front (no HTTP range requests needed), a plain
static server works:

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploying to GitHub Pages

The dashboard is published to a **`gh-pages`** branch (not `main`) — see the
next section for why. Enable Pages once:

1. Repo **Settings → Pages**
2. **Source: Deploy from a branch**
3. Branch: **`gh-pages`**, folder: **`/ (root)`**, Save

The site publishes at `https://<user>.github.io/test-weather/`.
The `.nojekyll` file ensures `static/` and the `.wasm` are served as-is.

`main` holds the source (code + a `weather.db` snapshot used to bootstrap and
for local dev); the live, continuously-updated db lives on `gh-pages`.

## Keeping it updated automatically

`weather.db` is a static file, so something has to refresh it when new readings
arrive. This repo does that entirely on its own — no access to the weather
machine or the source repo's settings required.

`.github/workflows/sync.yml` runs `scripts/sync_from_remote.py` on a ~15-minute
schedule. Each run:

1. restores the current db from the `gh-pages` branch,
2. reads the newest timestamp in it,
3. asks the GitHub API for commits of `Detail-All.htm` in the live repo
   (`tvLors/Davis_Weather`) newer than that,
4. fetches each of those commits' raw `Detail-All.htm`, parses the metrics, and
   appends the rows (`INSERT OR IGNORE`),
5. force-pushes the site + updated db to `gh-pages` as a single commit.

Because it captures **every commit** since the last run — not just whatever the
"current" reading happens to be — no 5-minute sample is lost even when GitHub's
scheduler delays, coalesces, or skips runs. **Cadence only affects freshness of
the latest point, never completeness of the history**, which is why a 15-minute
schedule is fine (and keeps well under the Pages build-rate soft limit).

### Why a force-pushed `gh-pages` branch?

`weather.db` (~4.5 MB) changes on every reading. Committing it to `main` every
few minutes would balloon the git history by hundreds of MB/day (binaries don't
delta well) and blow past GitHub Pages' ~10-builds-per-hour soft limit. Instead
the workflow rewrites `gh-pages` as a **single commit** each run — so the
published branch never accumulates history — while `main` keeps normal,
lightweight source history.

The row timestamp is the commit's **local wall-clock time**, read from the
commit message (`Weather update: Fri 07/17/2026 20:57:27`) and stored as UTC —
matching how the historical rows were built, so re-processing a commit is a
harmless no-op.

> Note: GitHub disables scheduled workflows on a repo after ~60 days of no
> activity in the repo. Any push (or a manual **Run workflow**) re-arms it.
