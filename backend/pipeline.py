#!/usr/bin/env python3
"""Data pipeline entry point: adapters -> normalized store -> static JSON.

Usage:
    python backend/pipeline.py [--scheduled]

--scheduled (CI only): wave models are fetched only when Open-Meteo has a
new run since our last successful fetch (per-model meta.json vs the stamps
in data/model_state.json — see openmeteo_marine.plan_models). Skipped
models' rows are carried forward from the previous run's store dump
(data/store.csv), so builders see the full model set either way; CI
persists both files in the actions cache. With the 01:45/07:45/13:45/19:45
UTC schedule, the twice-daily models (MFWAM, GWAM) each skip 2 of 4 runs
with zero freshness loss. Local runs (no flag) always fetch everything.

Exits non-zero if any CRITICAL source produced no data for any spot, so the
CI deploy is skipped and the previously published snapshot stays live.
"""
import csv
import json
import os
import sys
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
            rows = adapter.fetch_batch(spots, batch_start, batch_end, **kwargs)
            store.extend(rows)
            print(f"\n{adapter.NAME}: {len(rows)} rows (batched, {len(spots)} spots)")
            # An empty due-list legitimately yields no rows; not a failure.
            if not rows and adapter.CRITICAL and kwargs.get('models') != []:
                critical_failures.append(('(all)', adapter.NAME, 'no rows'))
        except Exception as e:
            print(f"\nX {adapter.NAME} failed: {e}")
            if adapter.CRITICAL:
                critical_failures.append(('(all)', adapter.NAME, e))

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
