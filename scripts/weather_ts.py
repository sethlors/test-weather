#!/usr/bin/env python3
"""
Canonical timestamp handling, shared by every part of the pipeline.

There is exactly one rule for how a reading's `ts` is derived, and it lives
here so the historical bootstrap (make_lean_db) and the incremental sync
(sync_from_remote) can never drift apart:

    A reading's wall-clock fields are treated as if they were UTC.

The weather station timestamps are local wall-clock time. We deliberately
discard the timezone offset and store the naive fields via calendar.timegm.
Combined with the dashboard rendering its x-axis in UTC, displayed times and
daily min/max bucket boundaries line up with the station's local midnight for
every viewer, regardless of their browser timezone.

Two input shapes exist in the wild; both must yield the identical epoch for a
given instant so that INSERT-OR-IGNORE dedup on the `ts` primary key works:

  - ISO commit dates, e.g. "2026-07-17T18:07:26-05:00"  (git author date)
  - commit-message reading times, e.g. "... 07/17/2026 18:07:26"
"""
import calendar
import datetime
import re

# Matches the reading time embedded in commit messages like
# "Weather update: Fri 07/17/2026 20:57:27" (fractional seconds ignored).
_MSG_TS = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")


def epoch_from_fields(year, month, day, hour, minute, second):
    """The single source of truth: wall-clock fields -> epoch, treating the
    fields as UTC (offset intentionally dropped)."""
    return calendar.timegm(
        datetime.datetime(year, month, day, hour, minute, second).timetuple()
    )


def epoch_from_iso(iso):
    """Wall-clock epoch from an ISO datetime string; any timezone offset is
    discarded (only the wall-clock fields are used)."""
    dt = datetime.datetime.fromisoformat(iso)
    return epoch_from_fields(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def epoch_from_message(message):
    """Wall-clock epoch parsed from a commit message's reading time, or None
    if the message contains no recognizable timestamp."""
    m = _MSG_TS.search(message)
    if not m:
        return None
    mo, da, yr, hh, mm, ss = map(int, m.groups())
    return epoch_from_fields(yr, mo, da, hh, mm, ss)


def format_utc(ts):
    """Human-readable rendering of a stored ts (wall-clock instant)."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
