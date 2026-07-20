#!/usr/bin/env python3
"""
One-time copy of every historical row from the existing lean weather.db into
the new Supabase `readings` table. Run this once, after applying
supabase/schema.sql, to seed history before switching the dashboard over.

Safe to re-run: uses on_conflict=ts + ignore-duplicates, so a partial/retried
run never double-inserts.

Usage:
    python3 scripts/backfill_supabase.py --db weather.db
Env:
    SUPABASE_URL          e.g. https://xxxxx.supabase.co
    SUPABASE_SERVICE_KEY  service_role key (bypasses RLS -- required to write)
"""
import argparse
import json
import os
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request

# Verified TLS everywhere. Prefer certifi's CA bundle when installed (fixes
# python.org macOS builds that ship without system CA certs); otherwise fall
# back to the platform default, which is correct on CI/Linux runners.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

BATCH = 500

COLUMNS = [
    "ts", "outsideTemp", "outsideDewPt", "windChill", "outsideHeatIndex",
    "outsideHumidity", "barometer", "windSpeed", "rainRate",
    "dailyRain", "stormRain", "monthlyRain", "totalRain",
    "windDirection", "barTrend",
]


def post_batch(url, key, rows):
    body = json.dumps(rows).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/readings?on_conflict=ts",
        data=body,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="weather.db")
    args = ap.parse_args()

    try:
        url = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_SERVICE_KEY"]
    except KeyError as e:
        print(f"ERROR: missing env var {e}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    print(f"backfilling {total} rows from {args.db}...")

    cur = conn.execute(f"SELECT {', '.join(f'\"{c}\"' for c in COLUMNS)} FROM readings ORDER BY ts")
    sent = 0
    batch = []
    for row in cur:
        batch.append({c: row[c] for c in COLUMNS})
        if len(batch) >= BATCH:
            post_batch(url, key, batch)
            sent += len(batch)
            print(f"  {sent}/{total}")
            batch.clear()
    if batch:
        post_batch(url, key, batch)
        sent += len(batch)

    conn.close()
    print(f"done: sent {sent} rows")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"ERROR {e.code}: {e.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
