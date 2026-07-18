#!/usr/bin/env python3
"""
Build the lean, web-optimized weather.db used by the dashboard from a full
weather.db (the 80-column database produced by build_weather_db.py in the
Davis_Weather repo).

The lean db keeps only the metrics the dashboard charts, plus a timestamp,
stored so the whole file is small and fast to load in the browser via sql.js.

Timestamps
----------
The source `commit_date` is local wall-clock time with a timezone offset
(e.g. "2026-07-17T18:07:26-05:00"). We store `ts` as the *wall-clock*
instant treated as UTC (calendar.timegm of the naive fields). Combined with
the dashboard rendering its x-axis in UTC, this makes displayed times and
daily min/max bucket boundaries line up with local midnight for everyone,
independent of the viewer's browser timezone.

Usage:
    python3 scripts/make_lean_db.py --src ../Davis_Weather/weather.db --out weather.db
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weather_ts import epoch_from_iso, format_utc  # noqa: E402

METRICS = [
    "outsideTemp", "outsideDewPt", "windChill", "outsideHeatIndex",
    "outsideHumidity", "barometer", "windSpeed", "rainRate",
    "dailyRain", "stormRain", "monthlyRain", "totalRain",
]

# Non-numeric fields carried through verbatim, as (source column, lean column).
# They aren't charted — they feed the dashboard's compass and barometer-trend
# readout — and neither can be derived from the numeric series, so the station's
# own reported value is the only source. Kept as TEXT because that's what the
# station emits: 16-point compass strings ("SSW") and trend phrases ("Steady").
TEXT_FIELDS = [
    ("windDirection", "windDirection"),
    ("BarTrend", "barTrend"),
]

# What the station writes when it has no reading. Stored as NULL, so the
# dashboard's existing "is it null?" checks are the only test a consumer needs.
NO_VALUE = "---"

PAGE_SIZE = 8192


def clean_text(v):
    """Normalize a station text field to a value or None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or v == NO_VALUE:
        return None
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to the full weather.db")
    ap.add_argument("--out", default="weather.db", help="path to write the lean db")
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.src}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    if os.path.exists(args.out):
        os.remove(args.out)
    dst = sqlite3.connect(args.out)
    dst.execute(f"PRAGMA page_size={PAGE_SIZE}")
    dst.execute("PRAGMA journal_mode=DELETE")
    cols = ", ".join(
        [f'"{m}" REAL' for m in METRICS]
        + [f'"{c}" TEXT' for _, c in TEXT_FIELDS]
    )
    dst.execute(f"CREATE TABLE readings (ts INTEGER PRIMARY KEY, {cols})")

    sel_cols = ", ".join(
        [f'"{m}"' for m in METRICS] + [f'"{s}"' for s, _ in TEXT_FIELDS]
    )
    rows = src.execute(
        f"SELECT commit_date, {sel_cols} FROM readings ORDER BY commit_date"
    ).fetchall()

    seen = set()
    batch = []
    for r in rows:
        ts = epoch_from_iso(r["commit_date"])
        if ts in seen:
            continue
        seen.add(ts)
        batch.append((
            ts,
            *[r[m] for m in METRICS],
            *[clean_text(r[s]) for s, _ in TEXT_FIELDS],
        ))

    ph = ",".join("?" * (len(METRICS) + len(TEXT_FIELDS) + 1))
    dst.executemany(f"INSERT INTO readings VALUES ({ph})", batch)
    dst.commit()
    dst.execute("VACUUM")
    dst.commit()

    n = dst.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    lo, hi = dst.execute("SELECT MIN(ts), MAX(ts) FROM readings").fetchone()
    dst.close()
    src.close()
    print(f"wrote {n} rows to {args.out} ({os.path.getsize(args.out)/1e6:.2f} MB)")
    print("range:", format_utc(lo), "->", format_utc(hi), "(wall clock)")


if __name__ == "__main__":
    main()
