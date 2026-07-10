#!/usr/bin/env python3
"""Data pipeline entry point: adapters -> normalized store -> static JSON.

Usage:
    python backend/pipeline.py

Exits non-zero if any CRITICAL source produced no data for any spot, so the
CI deploy is skipped and the previously published snapshot stays live.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from adapters import ADAPTERS
from builders import build_all
from buoys import build_buoys
from spots import load_spots, save_spots
from store import ForecastStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_FILE = os.path.join(BASE_DIR, '..', 'data', 'locations.csv')
STORE_DUMP = os.path.join(BASE_DIR, '..', 'data', 'store.csv')
API_DIR = os.path.join(BASE_DIR, '..', 'api')

# Window upper bound, not a promise: Open-Meteo serves at most today+15
# days (the 16-day GFS horizon counting today). Builders keep only the full
# days each spot's default model actually delivers, so shorter models just
# end sooner.
FORECAST_DAYS = 15


def spot_window_utc(spot):
    """Local midnight today through +FORECAST_DAYS, as UTC datetimes."""
    tz = ZoneInfo(spot.timezone)
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=FORECAST_DAYS)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def main():
    started = datetime.now(timezone.utc)
    spots = load_spots(LOCATIONS_FILE)
    print(f"Pipeline start {started:%Y-%m-%d %H:%M UTC} — {len(spots)} spots, "
          f"{len(ADAPTERS)} sources")

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
        try:
            rows = adapter.fetch_batch(spots, batch_start, batch_end)
            store.extend(rows)
            print(f"\n{adapter.NAME}: {len(rows)} rows (batched, {len(spots)} spots)")
            if not rows and adapter.CRITICAL:
                critical_failures.append(('(all)', adapter.NAME, 'no rows'))
        except Exception as e:
            print(f"\nX {adapter.NAME} failed: {e}")
            if adapter.CRITICAL:
                critical_failures.append(('(all)', adapter.NAME, e))

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

    print("\nBuilding static JSON...")
    build_all(store, spots, API_DIR)

    # Non-critical: the buoy comparison view degrades to absent, never
    # blocks publishing the forecast snapshot.
    print("\nBuilding buoy comparison files...")
    try:
        build_buoys(API_DIR)
    except Exception as e:
        print(f"  X buoys failed: {e}")

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

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n✓ Pipeline finished in {elapsed:.1f}s")


if __name__ == '__main__':
    main()
