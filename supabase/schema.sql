-- Weather dashboard schema for Supabase (Postgres).
--
-- Run this once in the Supabase SQL editor on a fresh project. Mirrors the
-- `readings` table shape the dashboard has always used (see
-- scripts/build_weather_db.py / scripts/sync_from_remote.py in this repo),
-- so a backfill from the existing weather.db is a straight column-for-column
-- copy.
--
-- Access model:
--   - service_role (used only by the station's push_reading.py, via a secret
--     key that never leaves the station machine) bypasses RLS entirely and
--     is the only writer.
--   - anon (the key embedded in the public static site) can only SELECT, via
--     the RLS policy below. There is no anon INSERT/UPDATE/DELETE policy, so
--     the public site cannot write even if the anon key leaks.
--   - Aggregation (hourly/daily bucketing) happens in RPC functions rather
--     than shipping raw rows to the browser for long ranges, mirroring what
--     the old sql.js GROUP BY queries did client-side.

create table if not exists readings (
  ts               bigint primary key,
  "outsideTemp"      double precision,
  "outsideDewPt"     double precision,
  "windChill"        double precision,
  "outsideHeatIndex" double precision,
  "outsideHumidity"  double precision,
  "barometer"        double precision,
  "windSpeed"        double precision,
  "rainRate"         double precision,
  "dailyRain"        double precision,
  "stormRain"        double precision,
  "monthlyRain"      double precision,
  "totalRain"        double precision,
  "windDirection"    text,
  "barTrend"         text
);

alter table readings enable row level security;

drop policy if exists "public read" on readings;
create policy "public read" on readings
  for select
  to anon, authenticated
  using (true);

-- no insert/update/delete policy for anon/authenticated -> only service_role
-- (which bypasses RLS) can write.

-- Realtime: broadcast INSERTs on this table so the dashboard can subscribe
-- instead of polling.
alter publication supabase_realtime add table readings;

-- ---------------------------------------------------------------------
-- Allowed metric columns, shared by every RPC below. Keeping this as an
-- explicit whitelist (rather than trusting quote_ident on caller input)
-- means a typo'd or malicious metric name fails loudly instead of quietly
-- reading an unintended column.
-- ---------------------------------------------------------------------
create or replace function _readings_assert_metric(metric text)
returns void
language plpgsql
immutable
as $$
begin
  if metric not in (
    'outsideTemp', 'outsideDewPt', 'windChill', 'outsideHeatIndex',
    'outsideHumidity', 'barometer', 'windSpeed', 'rainRate',
    'dailyRain', 'stormRain', 'monthlyRain', 'totalRain'
  ) then
    raise exception 'invalid metric: %', metric;
  end if;
end;
$$;

-- min/max ts across all readings, for the dashboard's "since <date>" header
-- and to bound the date pickers.
create or replace function readings_bounds()
returns table(lo bigint, hi bigint)
language sql
stable
as $$
  select min(ts), max(ts) from readings;
$$;

-- most recent row, for the "current conditions" strip.
create or replace function readings_latest()
returns setof readings
language sql
stable
as $$
  select * from readings order by ts desc limit 1;
$$;

-- full 5-minute resolution series for a metric over a range (short ranges
-- only -- the dashboard picks this vs. bucketed based on span).
create or replace function readings_raw(metric text, start_ts bigint, end_ts bigint)
returns table(t bigint, v double precision)
language plpgsql
stable
as $$
begin
  perform _readings_assert_metric(metric);
  return query execute format(
    'select ts, %I from readings where ts between $1 and $2 and %I is not null order by ts',
    metric, metric
  ) using start_ts, end_ts;
end;
$$;

-- hourly or daily bucketed avg/min/max for a metric over a range.
-- span_sec is 3600 (hourly) or 86400 (daily) -- validated to just those two
-- since it drives a bucket-width computation, not an identifier.
create or replace function readings_bucketed(metric text, start_ts bigint, end_ts bigint, span_sec bigint)
returns table(t bigint, avg double precision, mn double precision, mx double precision)
language plpgsql
stable
as $$
begin
  perform _readings_assert_metric(metric);
  if span_sec not in (3600, 86400) then
    raise exception 'invalid span_sec: %', span_sec;
  end if;
  return query execute format(
    'select (ts/$1)*$1 as b, avg(%I), min(%I), max(%I) ' ||
    'from readings where ts between $2 and $3 and %I is not null ' ||
    'group by b order by b',
    metric, metric, metric, metric
  ) using span_sec, start_ts, end_ts;
end;
$$;

-- one bar per day (MAX per day) for cumulative daily-counter metrics like
-- dailyRain, which reset at local midnight.
create or replace function readings_daily_total(metric text, start_ts bigint, end_ts bigint)
returns table(t bigint, mx double precision)
language plpgsql
stable
as $$
begin
  perform _readings_assert_metric(metric);
  return query execute format(
    'select (ts/86400)*86400 as b, max(%I) from readings ' ||
    'where ts between $1 and $2 and %I is not null group by b order by b',
    metric, metric
  ) using start_ts, end_ts;
end;
$$;

grant execute on function readings_bounds() to anon, authenticated;
grant execute on function readings_latest() to anon, authenticated;
grant execute on function readings_raw(text, bigint, bigint) to anon, authenticated;
grant execute on function readings_bucketed(text, bigint, bigint, bigint) to anon, authenticated;
grant execute on function readings_daily_total(text, bigint, bigint) to anon, authenticated;
