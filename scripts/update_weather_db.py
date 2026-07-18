#!/usr/bin/env python3
"""
Incrementally append new readings to the lean dashboard db (weather.db).

Instead of rebuilding from all ~56k commits, this looks at the newest
timestamp already in the lean db and only parses git commits of
Detail-All.htm that are newer than that, appending the handful of new rows.
A normal run (one new 5-minute commit) finishes in well under a second.

It reuses the template-matching logic from build_weather_db.py.

Usage:
    python3 scripts/update_weather_db.py --repo ../Davis_Weather --db weather.db
"""
import argparse
import calendar
import datetime
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_weather_db import (  # noqa: E402
    TEMPLATE_FILE, DATA_FILE, OLD_TEMPLATE_COMMIT,
    build_pattern, clean_value, get_commit_log, stream_blobs,
)
import subprocess  # noqa: E402

METRICS = [
    "outsideTemp", "outsideDewPt", "windChill", "outsideHeatIndex",
    "outsideHumidity", "barometer", "windSpeed", "rainRate",
    "dailyRain", "stormRain", "monthlyRain", "totalRain",
]


def wall_clock_epoch(iso: str) -> int:
    return calendar.timegm(datetime.datetime.fromisoformat(iso).timetuple())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to the repo holding Detail-All.htm history")
    ap.add_argument("--db", default="weather.db", help="lean db to append to")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()

    conn = sqlite3.connect(args.db)
    row = conn.execute("SELECT MAX(ts) FROM readings").fetchone()
    max_ts = row[0] if row and row[0] is not None else -1

    # current + historical templates (fallback for the earliest commits)
    htx_text = (repo / TEMPLATE_FILE).read_text(encoding="ascii", errors="replace")
    pattern, field_order = build_pattern(htx_text)
    old_htx = subprocess.run(
        ["git", "show", f"{OLD_TEMPLATE_COMMIT}:{TEMPLATE_FILE}"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    old_pattern, old_field_order = build_pattern(old_htx)
    patterns = [(pattern, field_order), (old_pattern, old_field_order)]

    commits = get_commit_log(repo)  # (hash, iso) chronological
    new = [(h, d) for (h, d) in commits if wall_clock_epoch(d) > max_ts]
    if not new:
        print("up to date; no new commits to append")
        conn.close()
        return

    hashes = [h for h, _ in new]
    dates = [d for _, d in new]
    ph = ",".join("?" * (len(METRICS) + 1))
    insert = f"INSERT OR IGNORE INTO readings VALUES ({ph})"

    appended = 0
    for blob, h, date in zip(stream_blobs(repo, hashes), hashes, dates):
        if blob is None:
            continue
        text = blob.decode("ascii", errors="replace")
        m = forder = None
        for pat, fo in patterns:
            m = pat.match(text)
            if m:
                forder = fo
                break
        if not m:
            continue
        gvals = m.groupdict()
        field_vals = {}
        for group, field in forder:
            if field not in field_vals:
                field_vals[field] = clean_value(gvals[group])
        ts = wall_clock_epoch(date)
        vals = [field_vals.get(k) for k in METRICS]
        # keep only clean numerics for the metric columns
        vals = [v if isinstance(v, (int, float)) else None for v in vals]
        conn.execute(insert, (ts, *vals))
        appended += 1

    conn.commit()
    conn.close()
    print(f"appended {appended} new row(s)")


if __name__ == "__main__":
    main()
