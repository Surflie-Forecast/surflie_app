"""Source adapters.

Each adapter module exposes:

    NAME                  short identifier used in logs
    CRITICAL              True if the pipeline must fail when this source
                          yields no data (False = degrade gracefully)
    fetch(spot, start_utc, end_utc) -> list of store rows (see store.FIELDS)

To add a new data source, create a module with that interface and append it
to ADAPTERS. Nothing else in the pipeline needs to change.
"""
from . import openmeteo_marine, openmeteo_weather, noaa_tides

ADAPTERS = [openmeteo_marine, openmeteo_weather, noaa_tides]
