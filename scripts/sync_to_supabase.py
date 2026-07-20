#!/usr/bin/env python3
"""
TEMPORARY STOPGAP -- retire this once the station is wired to push_reading.py
directly (see Davis_Weather/scripts/push_reading.py). Delete this script and
its GitHub Action (.github/workflows/interim-sync.yml) at that point.

Keeps Supabase fresh from the Davis_Weather repo's git history while the
station machine can't be reached to deploy the live-push script. Same
approach as the retired scripts/sync_from_remote.py, but writes to Supabase
via REST instead of a local sqlite file:

  1. reads the newest `ts` already in Supabase,
  2. asks the GitHub API for commits of Detail-All.htm newer than that,
  3. fetches each of those commits' raw Detail-All.htm,
  4. parses the metrics and upserts a row (on_conflict=ts, ignore-duplicates).

Usage:
    python3 scripts/sync_to_supabase.py
Env:
    SUPABASE_URL          e.g. https://xxxxx.supabase.co
    SUPABASE_SERVICE_KEY  service_role key (bypasses RLS -- required to write)
    GITHUB_TOKEN          optional; raises the GitHub API rate limit (used automatically in CI)
"""
import datetime
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_weather_db import build_pattern, clean_value  # noqa: E402
from weather_ts import epoch_from_message, format_utc  # noqa: E402

# Verified TLS everywhere. Prefer certifi's CA bundle when installed (fixes
# python.org macOS builds that ship without system CA certs); otherwise fall
# back to the platform default, which is correct on CI/Linux runners.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

OWNER = "tvLors"
REPO = "Davis_Weather"
DATA_FILE = "Detail-All.htm"
TEMPLATE_FILE = "Detail-All.htx"

METRICS = [
    "outsideTemp", "outsideDewPt", "windChill", "outsideHeatIndex",
    "outsideHumidity", "barometer", "windSpeed", "rainRate",
    "dailyRain", "stormRain", "monthlyRain", "totalRain",
]
TEXT_FIELDS = [("windDirection", "windDirection"), ("BarTrend", "barTrend")]
NO_VALUE = "---"
BATCH = 200


def clean_text(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if v and v != NO_VALUE else None


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
    out.sort()
    return out


def fetch_blob(sha):
    url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{sha}/{DATA_FILE}"
    return http_get(url, raw=True).decode("ascii", errors="replace")


def supabase_max_ts(url, key):
    req = urllib.request.Request(
        f"{url}/rest/v1/rpc/readings_bounds",
        data=b"{}",
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
        data = json.loads(r.read())
    return data[0]["hi"] or 0


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
    try:
        url = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_SERVICE_KEY"]
    except KeyError as e:
        print(f"ERROR: missing env var {e}", file=sys.stderr)
        sys.exit(1)
    token = os.environ.get("GITHUB_TOKEN") or None

    max_ts = supabase_max_ts(url, key)
    if max_ts:
        print(f"newest stored reading: {format_utc(max_ts)} (wall clock)")

    htx = http_get(
        f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/{TEMPLATE_FILE}",
        raw=True,
    ).decode("ascii", errors="replace")
    pattern, field_order = build_pattern(htx)

    commits = list_new_commits(max_ts, token)
    if not commits:
        print("up to date; no new commits")
        return

    rows = []
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
            field_vals.setdefault(field, clean_value(gvals[group]))
        row = {"ts": ts}
        for k in METRICS:
            v = field_vals.get(k)
            row[k] = v if isinstance(v, (int, float)) else None
        for src, dest in TEXT_FIELDS:
            row[dest] = clean_text(field_vals.get(src))
        rows.append(row)

    appended = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        post_batch(url, key, batch)
        appended += len(batch)

    newest = commits[-1][0]
    span_min = (newest - commits[0][0]) / 60.0
    print(
        f"appended {appended} new row(s), closing a {span_min:.0f} min gap "
        f"across {len(commits)} upstream commit(s); "
        f"newest reading now {format_utc(newest)} (wall clock)"
    )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"ERROR {e.code}: {e.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
