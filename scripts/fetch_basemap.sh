#!/usr/bin/env bash
# Rebuild static/basemap/station.pmtiles — the self-hosted vector basemap behind
# the station locator map.
#
# Why this exists at all: the dashboard vendors every dependency (sql.js, uPlot)
# so the published page makes no third-party requests. A map would normally
# break that by fetching tiles from someone else's server on every page load.
# Instead we cut a small region out of the Protomaps planet build once and serve
# it ourselves, the same way weather.db is served — a single file on static
# storage, read via HTTP range requests.
#
# Licensing note: the Protomaps basemap is a Produced Work of OpenStreetMap
# under ODbL, which *permits* this redistribution provided the map visibly
# attributes "© OpenStreetMap" (index.html does, in the map's corner). This is
# specifically why we don't scrape tiles from OSM's standard tile server —
# its usage policy prohibits pre-seeding areas and zoom levels the way we do
# here. See https://operations.osmfoundation.org/policies/tiles/
#
# Rerun this only if the station moves or the basemap needs refreshing; the
# output is committed, so a normal deploy never touches the network for it.
#
# Requires: pmtiles CLI (brew install pmtiles)

set -euo pipefail

# Station coordinates, deliberately rounded to ~1 km. Full precision would
# publish a home address on a public site, and buys nothing: at the map's
# maximum zoom (12) a finer fix isn't even distinguishable, and sunrise/sunset
# shift by well under a second. Keep these in sync with STATION in index.html.
LAT=41.59
LON=-93.83

# Region to cut out, as min_lon,min_lat,max_lon,max_lat. Padded well beyond the
# visible map so panning to the edge never exposes blank tiles.
BBOX="-94.43,41.19,-93.23,41.99"

# z12 ≈ 100 m/px, which is as much detail as ~1 km-rounded coordinates can
# honestly justify. Each additional zoom level roughly doubles the archive.
MAXZOOM=12

OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/static/basemap/station.pmtiles"

# Protomaps publishes a dated planet build daily; resolve the newest one. Their
# docs discourage hotlinking these URLs from a live site, which is exactly why
# we extract to our own copy here instead of pointing the page at them.
echo "resolving latest Protomaps planet build..."
BUILD=$(curl -fsSL https://build-metadata.protomaps.dev/builds.json \
  | grep -oE '"key":"[0-9]+\.pmtiles"' | tail -1 | grep -oE '[0-9]+\.pmtiles')
echo "using build: $BUILD"

mkdir -p "$(dirname "$OUT")"

# extract pulls only the byte ranges covering BBOX out of the ~136 GB planet
# file over HTTP — it never downloads the whole thing.
pmtiles extract "https://build.protomaps.com/$BUILD" "$OUT" \
  --bbox="$BBOX" \
  --maxzoom="$MAXZOOM"

echo
echo "wrote $OUT ($(du -h "$OUT" | cut -f1)) centered on $LAT,$LON"
