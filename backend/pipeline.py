#!/usr/bin/env python3
"""Data pipeline entry point: adapters -> normalized store -> static JSON.

Usage:
    python backend/pipeline.py [--scheduled]

--scheduled (CI only): wave models are fetched only when Open-Meteo has a
new run since our last successful fetch (per-model meta.json vs the stamps
in data/model_state.json — see openmeteo_marine.plan_models). Skipped
models' rows are carried forward from the previous run's store dump
(data/store.csv), so builders see the full model set either way; CI
persists both files in the actions cache. On the 01:45/13:45 UTC schedule
the twice-daily MFWAM skips a run whenever its 00Z/12Z publish hasn't
landed yet, with zero freshness loss. Local runs (no flag) fetch
everything — which on a 276-spot set is enough Open-Meteo calls to hit the
free tier's hourly cap if repeated back-to-back.

Exits non-zero if any CRITICAL source produced no data for any spot, so the
CI deploy is skipped and the previously published snapshot stays live.
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from adapters import ADAPTERS, openmeteo_marine
from builders import build_all
from buoys import build_buoys
from skill import score_spots
from spots import load_spots, save_spots
from store import ForecastStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_FILE = os.path.join(BASE_DIR, '..', 'data', 'locations.csv')
STORE_DUMP = os.path.join(BASE_DIR, '..', 'data', 'store.csv')
MODEL_STATE_FILE = os.path.join(BASE_DIR, '..', 'data', 'model_state.json')
API_DIR = os.path.join(BASE_DIR, '..', 'api')

# Window upper bound, not a promise: Open-Meteo accepts end_date up to
# today+16 and null-pads past each model's real horizon (GFS Wave runs to
# ~today+15 18:00 UTC — enough to complete 15 full LOCAL days after today,
# which a +15 window clipped one short of). Builders keep only the full
# days each spot's default model actually delivers, so shorter models and
# the null-padded tail just end sooner.
FORECAST_DAYS = 16


def spot_window_utc(spot):
    """Local midnight today through +FORECAST_DAYS, as UTC datetimes."""
    tz = ZoneInfo(spot.timezone)
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=FORECAST_DAYS)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _carry_forward_rows(sources):
    """Rows for `sources` from the previous run's store dump — how a
    scheduled run keeps a model it skipped (no new run upstream). Missing
    dump or malformed rows just mean nothing to carry."""
    rows = []
    try:
        with open(STORE_DUMP, newline='') as f:
            for r in csv.DictReader(f):
                if r.get('source') in sources:
                    r['value'] = float(r['value'])
                    rows.append(r)
    except Exception as e:
        print(f"  ! carry-forward from {STORE_DUMP} failed: {e}")
    return rows


# Variables a source must have in the dump to be safely carried forward.
# The base set is what every wave model provides; the partition set was
# added later (2026-07-19) — a pre-partition dump would carry a source
# forward with only its primary swell, silently truncating the forecast
# cards' swell breakdown. Keyed by source so partition-less models (ECMWF
# WAM has no swell partition; GWAM only one swell) aren't wrongly flagged.
_BASE_WAVE_VARS = {'wave_height', 'swell_height', 'swell_period',
                   'swell_direction'}
_PARTITION_VARS = {'swell2_height', 'swell2_period', 'swell2_direction',
                   'swell3_height', 'swell3_period', 'swell3_direction',
                   'windwave_height', 'windwave_period', 'windwave_direction'}
_EXPECTED_VARS = {
    'openmeteo:gfswave016': _BASE_WAVE_VARS | _PARTITION_VARS,
    'openmeteo:mfwam': _BASE_WAVE_VARS | _PARTITION_VARS,
    # ECMWF WAM publishes no partition at either resolution — base only.
}


def _stale_wave_models(spots, skipped):
    """Subset of `skipped` [(model, source)] that can't be safely carried
    forward from the previous dump — because its rows don't cover every
    current spot (a just-added spot would build empty) OR are missing an
    expected variable (a pre-schema-change dump would truncate, e.g. drop
    the secondary/tertiary swell partitions). Judged PER SOURCE. A
    missing/unreadable dump counts every model as stale — fail open."""
    slugs = {spot.slug for spot in spots}
    coverage = {}
    variables = {}
    try:
        with open(STORE_DUMP, newline='') as f:
            for r in csv.DictReader(f):
                coverage.setdefault(r['source'], set()).add(r['spot'])
                variables.setdefault(r['source'], set()).add(r['variable'])
    except Exception:
        return list(skipped)

    stale = []
    for m, s in skipped:
        if not slugs <= coverage.get(s, set()):
            stale.append((m, s))
        elif not _EXPECTED_VARS.get(s, set()) <= variables.get(s, set()):
            stale.append((m, s))
    return stale


def main():
    scheduled = '--scheduled' in sys.argv
    started = datetime.now(timezone.utc)
    spots = load_spots(LOCATIONS_FILE)
    print(f"Pipeline start {started:%Y-%m-%d %H:%M UTC} — {len(spots)} spots, "
          f"{len(ADAPTERS)} sources{' (scheduled)' if scheduled else ''}")

    # Scheduled runs fetch only wave models with a new run upstream and carry
    # the rest forward from the last dump; local runs always fetch all.
    due_models, model_avail = None, {}
    skipped_sources = set()
    if scheduled:
        due_models, skipped, model_avail = openmeteo_marine.plan_models(
            MODEL_STATE_FILE)
        # Carry-forward is only safe when the dump fully covers a model:
        # every current spot AND every expected variable. A just-added spot
        # or a pre-schema-change dump (e.g. before the swell partitions)
        # forces a real fetch instead of carrying truncated data forward.
        stale = _stale_wave_models(spots, skipped) if skipped else []
        if stale:
            print("Skipped models with incomplete dump coverage; fetching "
                  "anyway: " + ', '.join(model for model, _ in stale))
            due_models += stale
            skipped = [ms for ms in skipped if ms not in stale]
        skipped_sources = {source for _, source in skipped}
        if skipped:
            print("Wave models unchanged upstream (carried forward): "
                  + ', '.join(model for model, _ in skipped))

    store = ForecastStore()
    critical_failures = []

    # A single window covering every spot's local-day range. Spots share a
    # timezone in practice but not by guarantee; the builders clip each spot's
    # own local days, so a slightly wider batch fetch is harmless.
    windows = [spot_window_utc(spot) for spot in spots]
    batch_start = min(w[0] for w in windows)
    batch_end = max(w[1] for w in windows)

    # Adapters exposing fetch_batch (the Open-Meteo sources) fetch all spots in
    # one multi-coordinate request; the rest keep the per-spot loop.
    batch_adapters = [a for a in ADAPTERS if hasattr(a, 'fetch_batch')]
    per_spot_adapters = [a for a in ADAPTERS if not hasattr(a, 'fetch_batch')]

    for adapter in batch_adapters:
        kwargs = {}
        if adapter is openmeteo_marine and due_models is not None:
            kwargs['models'] = due_models
        try:
            t0 = time.monotonic()
            rows = adapter.fetch_batch(spots, batch_start, batch_end, **kwargs)
            dt = time.monotonic() - t0
            store.extend(rows)
            print(f"\n{adapter.NAME}: {len(rows)} rows (batched, {len(spots)} "
                  f"spots) in {dt:.1f}s")
            # An empty due-list legitimately yields no rows; not a failure.
            if not rows and adapter.CRITICAL and kwargs.get('models') != []:
                critical_failures.append(('(all)', adapter.NAME, 'no rows'))
        except Exception as e:
            print(f"\nX {adapter.NAME} failed: {e}")
            if adapter.CRITICAL:
                critical_failures.append(('(all)', adapter.NAME, e))

    # A model that was DUE but produced nothing (transient timeout, upstream
    # 5xx) must not simply vanish: _pick_wave_source would silently fall
    # through to a lesser model — e.g. GFS Wave timing out dropped every spot
    # onto a shorter-horizon model, losing days off the end of the forecast.
    # Serving its previous rows keeps the display model stable across a blip.
    # Guarding this on `due_models` meant it only ran under --scheduled: a
    # local run left due_models None and let a blipped model vanish from the
    # artifact outright (observed 2026-08-03, a different model each run —
    # once GFS Wave, dropping every spot onto MFWAM and the horizon from 16
    # days to 9). Expected sources are the full MODELS list when nothing was
    # planned, so the net holds either way.
    expected = due_models if due_models is not None else openmeteo_marine.MODELS
    if expected:
        got = store.sources()
        missing = {source for _, source in expected if source not in got}
        if missing:
            recovered = _carry_forward_rows(missing)
            if recovered:
                store.extend(recovered)
                print(f"\n! wave models with no rows ({', '.join(sorted(missing))}"
                      f"); carried {len(recovered)} previous rows forward")
            still = missing - store.sources()
            if still:
                print(f"\n! wave models missing with nothing to carry forward: "
                      f"{', '.join(sorted(still))}")

    if skipped_sources:
        carried = _carry_forward_rows(skipped_sources)
        store.extend(carried)
        print(f"\ncarried forward: {len(carried)} rows "
              f"({len(skipped_sources)} unchanged models)")

    for spot in spots:
        start_utc, end_utc = spot_window_utc(spot)
        print(f"\n{spot.name} ({spot.latitude:.3f}, {spot.longitude:.3f})")
        for adapter in per_spot_adapters:
            try:
                rows = adapter.fetch(spot, start_utc, end_utc)
                store.extend(rows)
                print(f"  {adapter.NAME}: {len(rows)} rows")
                if not rows and adapter.CRITICAL:
                    critical_failures.append((spot.name, adapter.NAME, 'no rows'))
            except Exception as e:
                print(f"  X {adapter.NAME} failed: {e}")
                if adapter.CRITICAL:
                    critical_failures.append((spot.name, adapter.NAME, e))

    store.save_csv(STORE_DUMP)
    print(f"\nStore: {len(store)} rows -> {STORE_DUMP}")

    # Stamp the models whose fetch actually produced rows; a due model that
    # failed keeps its old stamp and stays due next run. After save_csv so a
    # stamp never outruns the dump the next carry-forward reads.
    if scheduled and due_models:
        openmeteo_marine.stamp_models(
            MODEL_STATE_FILE, model_avail,
            store.sources() & {source for _, source in due_models})

    print("\nBuilding static JSON...")
    build_all(store, spots, API_DIR)

    # Non-critical: the buoy comparison view degrades to absent, never
    # blocks publishing the forecast snapshot. Also fills any spot's blank
    # closest_buoy (persisted below by save_spots; it lands in spots.json on
    # the next run).
    print("\nBuilding buoy comparison files...")
    try:
        build_buoys(API_DIR, spots, skipped_sources=skipped_sources)
    except Exception as e:
        print(f"  X buoys failed: {e}")

    # Forecast-skill scoring: archive today's forecasts and fold matured
    # points into per-lead-day bias/RMSE against the observed-driven MOP
    # nowcast (backend/skill.py). Best-effort — never blocks publishing.
    print("\nScoring forecast skill vs MOP nowcast...")
    try:
        score_spots(spots, API_DIR)
    except Exception as e:
        print(f"  X skill scoring failed: {e}")

    # Persist any newly resolved marine cells so future runs batch straight
    # through without re-running the per-spot dead-cell search. Runs even if
    # buoys failed above.
    save_spots(LOCATIONS_FILE, spots)
    print(f"\nUpdated resolved cells -> {LOCATIONS_FILE}")

    if critical_failures:
        print("\n❌ Critical source failures — refusing to publish a degraded snapshot:")
        for spot_name, adapter_name, err in critical_failures:
            print(f"  {spot_name} / {adapter_name}: {err}")
        sys.exit(1)

    # Stamp the snapshot so push deploys can pick the newest of the repo's
    # committed api/ vs. the CI cache. Written only on full success — a run
    # with critical failures exits above and never marks its output
    # publishable.
    with open(os.path.join(API_DIR, 'meta.json'), 'w') as f:
        json.dump({'generated_utc': started.isoformat()}, f)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n✓ Pipeline finished in {elapsed:.1f}s")


if __name__ == '__main__':
    main()
