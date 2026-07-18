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
| `scripts/build_weather_db.py` | Full extractor from `Detail-All.htm` git history |
| `scripts/make_lean_db.py` | Full db → lean web db |
| `scripts/update_weather_db.py` | **Incremental** append of new readings |
| `scripts/update-db.yml.example` | GitHub Actions workflow to auto-update the db |

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

This repo is already a static site at its root. Enable Pages once:

1. Repo **Settings → Pages**
2. **Source: Deploy from a branch**
3. Branch: **`main`**, folder: **`/ (root)`**, Save

The site publishes at `https://<user>.github.io/test-weather/`.
The `.nojekyll` file ensures `static/` and the `.wasm` are served as-is.

## Keeping it updated automatically

`weather.db` is a static file, so something has to regenerate it when new
readings arrive. The intended setup (for the repo that actually receives the
5-minute weather pushes, e.g. `Davis_Weather`):

1. Copy `scripts/*.py` and `scripts/update-db.yml.example` →
   `.github/workflows/update-db.yml` into that repo.
2. On each weather push, the workflow runs `update_weather_db.py` to append the
   new reading(s) to `weather.db` and commits it back (tagged `[skip ci]` so it
   doesn't loop).
3. GitHub Pages serves the refreshed db within about a minute.

`update_weather_db.py` only parses commits newer than the newest row already in
the db, so a normal run (one new commit) finishes in a fraction of a second
rather than re-walking all ~56k commits.
