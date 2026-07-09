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
from spots import load_spots
from store import ForecastStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_FILE = os.path.join(BASE_DIR, '..', 'data', 'locations.csv')
STORE_DUMP = os.path.join(BASE_DIR, '..', 'data', 'store.csv')
API_DIR = os.path.join(BASE_DIR, '..', 'api')

FORECAST_DAYS = 10


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

    for spot in spots:
        start_utc, end_utc = spot_window_utc(spot)
        print(f"\n{spot.name} ({spot.latitude:.3f}, {spot.longitude:.3f})")
        for adapter in ADAPTERS:
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

    if critical_failures:
        print("\n❌ Critical source failures — refusing to publish a degraded snapshot:")
        for spot_name, adapter_name, err in critical_failures:
            print(f"  {spot_name} / {adapter_name}: {err}")
        sys.exit(1)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n✓ Pipeline finished in {elapsed:.1f}s")


if __name__ == '__main__':
    main()
