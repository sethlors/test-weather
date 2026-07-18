#!/usr/bin/env python3
"""
Build a SQLite database of historical weather readings by walking every
git commit that touched Detail-All.htm and extracting the values that were
substituted into the Detail-All.htx template placeholders.

Usage:
    python3 scripts/build_weather_db.py [--repo PATH] [--out weather.db]
"""
import argparse
import html
import re
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

TEMPLATE_FILE = "Detail-All.htx"
DATA_FILE = "Detail-All.htm"

# Detail-All.htx has been edited twice in its history (both times in the
# first ~8 hours after this repo started, Dec 11-12 2025); every commit
# since is generated from the current template. We keep the old commit
# hash around so old readings can still be parsed with the template that
# was actually in effect at that time.
OLD_TEMPLATE_COMMIT = "3d1102b267a0ec3848dcd6ad9a04dd9aad0cffcf"


def build_pattern(htx_text: str):
    """Turn the .htx template into a regex with one named group per
    placeholder, preserving duplicate placeholder names with a numeric
    suffix on the *regex group* (the returned field_order maps each group
    back to its real field name)."""
    parts = re.split(r"(<!--[A-Za-z0-9_]+-->)", htx_text)
    pieces = []
    field_order = []  # (group_name, field_name), in document order
    seen = {}
    for part in parts:
        m = re.fullmatch(r"<!--([A-Za-z0-9_]+)-->", part)
        if m:
            field = m.group(1)
            seen[field] = seen.get(field, 0) + 1
            group = field if seen[field] == 1 else f"{field}__{seen[field]}"
            field_order.append((group, field))
            # A handful of "*Unit" placeholders sit directly adjacent to a
            # numeric placeholder with no literal text between them (e.g.
            # <!--outsideHumidity--><!--humUnit-->). A plain non-greedy
            # `.*?` there is ambiguous and swallows the number too, since
            # unit strings ("%", "in", "mph"...) never contain digits,
            # excluding digits from the unit group's class resolves it.
            char_class = "[^0-9<]*?" if field.endswith("Unit") else ".*?"
            pieces.append(f"(?P<{group}>{char_class})")
        else:
            pieces.append(re.escape(part))
    pattern = re.compile("".join(pieces), re.DOTALL)
    return pattern, field_order


def clean_value(raw: str):
    """Unescape HTML entities, strip whitespace, and coerce to float when
    the value is purely numeric."""
    val = html.unescape(raw).replace("\xa0", " ").strip()
    if val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return val


def get_commit_log(repo: Path):
    """Return [(commit_hash, iso_date), ...] in chronological order for
    every commit that touched DATA_FILE."""
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%H\t%aI", "--", DATA_FILE],
        cwd=repo, capture_output=True, check=True, text=True,
    ).stdout
    commits = []
    for line in out.splitlines():
        h, date = line.split("\t")
        commits.append((h, date))
    return commits


def stream_blobs(repo: Path, hashes):
    """Yield raw bytes of DATA_FILE at each commit hash, in order, using a
    single `git cat-file --batch` process instead of one process per commit."""
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )

    def feed():
        for h in hashes:
            proc.stdin.write(f"{h}:{DATA_FILE}\n".encode())
        proc.stdin.close()

    threading.Thread(target=feed, daemon=True).start()

    stdout = proc.stdout
    for _ in hashes:
        header = stdout.readline()
        if not header:
            raise RuntimeError("git cat-file --batch closed early")
        parts = header.split()
        if len(parts) != 3:
            # "<oid> missing" - shouldn't happen since git log gave us this path
            yield None
            continue
        _, _, size = parts
        size = int(size)
        content = stdout.read(size)
        stdout.read(1)  # trailing newline
        yield content

    proc.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--out", default="weather.db", type=Path)
    ap.add_argument("--limit", type=int, default=None, help="only process the first N commits (debugging)")
    args = ap.parse_args()

    repo = args.repo.resolve()
    htx_text = (repo / TEMPLATE_FILE).read_text(encoding="ascii", errors="replace")
    pattern, field_order = build_pattern(htx_text)

    old_htx_text = subprocess.run(
        ["git", "show", f"{OLD_TEMPLATE_COMMIT}:{TEMPLATE_FILE}"],
        cwd=repo, capture_output=True, check=True, text=True,
    ).stdout
    old_pattern, old_field_order = build_pattern(old_htx_text)
    if set(f for _, f in old_field_order) != set(f for _, f in field_order):
        print("WARNING: old and current template placeholder sets differ", file=sys.stderr)
    # try the current (majority-case) template first, fall back to the old one
    patterns = [(pattern, field_order), (old_pattern, old_field_order)]

    # unique field names, in first-seen order
    field_names = list(dict.fromkeys(f for _, f in field_order))
    print(f"Template has {len(field_order)} placeholders, {len(field_names)} unique fields", file=sys.stderr)

    # SQLite column names are case-insensitive, but some placeholders differ
    # only by case (e.g. monthlyRain / MonthlyRain) and are genuinely
    # distinct template slots -> give the SQL column a disambiguated name
    # while keeping field_vals keyed by the real field name.
    sql_col_name = {}
    seen_lower = {}
    for f in field_names:
        key = f.lower()
        seen_lower[key] = seen_lower.get(key, 0) + 1
        sql_col_name[f] = f if seen_lower[key] == 1 else f"{f}_alt{seen_lower[key]}"

    print("Listing commits...", file=sys.stderr)
    commits = get_commit_log(repo)
    if args.limit:
        commits = commits[: args.limit]
    print(f"{len(commits)} commits touch {DATA_FILE}", file=sys.stderr)

    out_path = args.out.resolve()
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(out_path)
    cols_sql = ", ".join(f'"{sql_col_name[f]}" NUMERIC' for f in field_names)
    conn.execute(
        f'CREATE TABLE readings (id INTEGER PRIMARY KEY, commit_hash TEXT UNIQUE, '
        f'commit_date TEXT, {cols_sql})'
    )
    conn.execute("CREATE INDEX idx_readings_date ON readings(commit_date)")
    quoted_cols = ", ".join(f'"{sql_col_name[f]}"' for f in field_names)
    placeholders = ", ".join("?" for _ in field_names)
    insert_sql = (
        f'INSERT INTO readings (commit_hash, commit_date, {quoted_cols}) '
        f'VALUES (?, ?, {placeholders})'
    )

    hashes = [h for h, _ in commits]
    dates = [d for _, d in commits]

    matched = 0
    unmatched = 0
    rows = []
    BATCH = 2000

    for i, (blob, (h, date)) in enumerate(zip(stream_blobs(repo, hashes), zip(hashes, dates))):
        if blob is None:
            unmatched += 1
            continue
        text = blob.decode("ascii", errors="replace")
        m = None
        for pat, forder in patterns:
            m = pat.match(text)
            if m:
                break
        if not m:
            unmatched += 1
            continue
        matched += 1
        gvals = m.groupdict()
        # collapse duplicate-named groups back onto their single field name
        field_vals = {}
        for group, field in forder:
            if field in field_vals:
                continue
            field_vals[field] = clean_value(gvals[group])
        rows.append((h, date, *[field_vals.get(f) for f in field_names]))

        if len(rows) >= BATCH:
            conn.executemany(insert_sql, rows)
            conn.commit()
            rows.clear()
            print(f"  {i+1}/{len(commits)} processed (matched={matched}, unmatched={unmatched})", file=sys.stderr)

    if rows:
        conn.executemany(insert_sql, rows)
        conn.commit()

    conn.close()
    print(f"Done. matched={matched} unmatched={unmatched} -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
