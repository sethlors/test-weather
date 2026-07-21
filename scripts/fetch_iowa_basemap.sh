#!/usr/bin/env bash
# Rebuild static/basemap/iowa.pmtiles -- the statewide (lower-detail) vector
# basemap used when the locator map needs to show a station outside
# station.pmtiles' tight bounding box (see fetch_basemap.sh, which cuts a
# ~65x55mi box around the home station at street-level detail).
#
# Same "cut once, serve as a static file, no third-party tile requests"
# approach as fetch_basemap.sh -- see that script's header for the full
# rationale and OSM/ODbL attribution note. This one just covers a much
# bigger area at a lower maxzoom, which is why it's a separate file instead
# of just widening station.pmtiles' bbox: doing that at the same maxzoom=12
# would put a ~60MB+ file on `main` for detail the wide view never needs.
#
# Rerun this only if the curated Iowa station list (IA_STATIONS in
# index.html) grows past this bbox, or the basemap needs refreshing.
#
# Requires: pmtiles CLI (brew install pmtiles)

set -euo pipefail

# Padded a bit past Iowa's actual extent so every curated station in
# IA_STATIONS (index.html) has margin around it, not just fits exactly.
BBOX="-96.7,40.3,-90.0,43.7"

# z10 ≈ 150 m/px -- enough to read city layout and major roads when zoomed
# into a station, without the ~60MB+ a state-sized area would cost at
# station.pmtiles' street-level z12.
MAXZOOM=10

OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/static/basemap/iowa.pmtiles"

echo "resolving latest Protomaps planet build..."
BUILD=$(curl -fsSL https://build-metadata.protomaps.dev/builds.json \
  | grep -oE '"key":"[0-9]+\.pmtiles"' | tail -1 | grep -oE '[0-9]+\.pmtiles')
echo "using build: $BUILD"

mkdir -p "$(dirname "$OUT")"

pmtiles extract "https://build.protomaps.com/$BUILD" "$OUT" \
  --bbox="$BBOX" \
  --maxzoom="$MAXZOOM"

echo
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
