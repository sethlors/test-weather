#!/usr/bin/env python3
"""
Keep the lean dashboard db (weather.db) up to date by pulling new readings
straight from the live GitHub repo that the weather station pushes to.

For each run it:
  1. reads the newest timestamp already stored,
  2. asks the GitHub API for commits of Detail-All.htm newer than that,
  3. fetches each of those commits' raw Detail-All.htm,
  4. parses the 12 metrics and appends a row (INSERT OR IGNORE).

Because every commit is captured (not just whatever is "current" right now),
the full 5-minute series is preserved no matter how irregularly this runs.

Timestamps: the row's `ts` is the commit's LOCAL wall-clock time, taken from
the commit message ("Weather update: Fri 07/17/2026 20:57:27") and stored as
UTC — identical to how the historical rows were built, so dedup lines up.

Usage:
    python3 scripts/sync_from_remote.py --db weather.db
Env:
    GITHUB_TOKEN  optional; raises the API rate limit (used automatically in CI)
"""
import argparse
import datetime
import json
import os
import sqlite3
import ssl
import sys
import urllib.request
from pathlib import Path

# Verified TLS everywhere. Prefer certifi's CA bundle when installed (fixes
# python.org macOS builds that ship without system CA certs); otherwise fall
# back to the platform default, which is correct on CI/Linux runners.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_weather_db import build_pattern, clean_value  # noqa: E402
from weather_ts import epoch_from_message, format_utc  # noqa: E402

OWNER = "tvLors"
REPO = "Davis_Weather"
DATA_FILE = "Detail-All.htm"
TEMPLATE_FILE = "Detail-All.htx"

METRICS = [
    "outsideTemp", "outsideDewPt", "windChill", "outsideHeatIndex",
    "outsideHumidity", "barometer", "windSpeed", "rainRate",
    "dailyRain", "stormRain", "monthlyRain", "totalRain",
]

def http_get(url, token=None, raw=False):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "davis-weather-sync")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if not raw:
        req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
        return r.read()


def list_new_commits(max_ts, token):
    """Yield (ts, sha) for commits of DATA_FILE with wall-clock ts > max_ts,
    oldest first."""
    # `since` filters by UTC commit time; our max_ts is local-as-UTC, i.e.
    # already several hours behind the real UTC commit time, so this is a
    # safe backward buffer that never drops a genuinely-new commit.
    since = datetime.datetime.fromtimestamp(max_ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    page = 1
    while True:
        url = (f"https://api.github.com/repos/{OWNER}/{REPO}/commits"
               f"?path={DATA_FILE}&since={since}&per_page=100&page={page}")
        commits = json.loads(http_get(url, token))
        if not commits:
            break
        for c in commits:
            ts = epoch_from_message(c["commit"]["message"])
            if ts is not None and ts > max_ts:
                out.append((ts, c["sha"]))
        if len(commits) < 100:
            break
        page += 1
    out.sort()  # oldest first
    return out


def fetch_blob(sha):
    url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{sha}/{DATA_FILE}"
    return http_get(url, raw=True).decode("ascii", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="weather.db")
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or None

    conn = sqlite3.connect(args.db)
    row = conn.execute("SELECT MAX(ts) FROM readings").fetchone()
    max_ts = row[0] if row and row[0] is not None else 0
    if max_ts:
        print(f"newest stored reading: {format_utc(max_ts)} (wall clock)")

    # current template -> extraction regex
    htx = http_get(
        f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/{TEMPLATE_FILE}",
        raw=True,
    ).decode("ascii", errors="replace")
    pattern, field_order = build_pattern(htx)

    commits = list_new_commits(max_ts, token)
    if not commits:
        print("up to date; no new commits")
        conn.close()
        return

    ph = ",".join("?" * (len(METRICS) + 1))
    insert = f"INSERT OR IGNORE INTO readings VALUES ({ph})"
    appended = 0
    for ts, sha in commits:
        try:
            text = fetch_blob(sha)
        except Exception as e:
            print(f"  warn: could not fetch {sha[:8]}: {e}", file=sys.stderr)
            continue
        m = pattern.match(text)
        if not m:
            print(f"  warn: template did not match {sha[:8]}", file=sys.stderr)
            continue
        gvals = m.groupdict()
        field_vals = {}
        for group, field in field_order:
            if field not in field_vals:
                field_vals[field] = clean_value(gvals[group])
        vals = [field_vals.get(k) for k in METRICS]
        vals = [v if isinstance(v, (int, float)) else None for v in vals]
        cur = conn.execute(insert, (ts, *vals))
        appended += cur.rowcount

    conn.commit()
    conn.close()
    # After backfilling, our newest reading == the newest upstream commit we
    # saw this run, so we are caught up by construction. `ts` is wall-clock
    # treated as UTC, so it can't be compared to real time without the
    # station's tz offset; report the upstream span we closed instead.
    newest = commits[-1][0]
    span_min = (newest - commits[0][0]) / 60.0
    print(
        f"appended {appended} new row(s), closing a {span_min:.0f} min gap "
        f"across {len(commits)} upstream commit(s); "
        f"newest reading now {format_utc(newest)} (wall clock)"
    )


if __name__ == "__main__":
    main()
