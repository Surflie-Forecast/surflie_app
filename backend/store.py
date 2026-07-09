"""Normalized forecast store — the seam between ingestion and serving.

Every adapter emits rows of the same shape regardless of provider:

    spot      spot slug
    ts_utc    ISO-8601 UTC timestamp
    variable  canonical variable name (swell_height, wind_speed, ...)
    value     float
    source    provider/model identifier (e.g. "openmeteo:mfwam")

Builders read only from here and never talk to providers directly, so adding
a data source means writing one adapter and nothing else. The store is
rebuilt from scratch on every pipeline run; the CSV dump exists for local
inspection/debugging, not as an archive. If this ever outgrows flat files,
this module is the only place that needs to learn about a database.
"""
import csv
from datetime import datetime

FIELDS = ['spot', 'ts_utc', 'variable', 'value', 'source']


class ForecastStore:
    def __init__(self):
        self._rows = []

    def extend(self, rows):
        for r in rows:
            missing = [f for f in FIELDS if f not in r]
            if missing:
                raise ValueError(f"Row missing fields {missing}: {r}")
        self._rows.extend(rows)

    def series(self, spot_slug, variable):
        """Return {utc datetime: value} for one spot/variable."""
        out = {}
        for r in self._rows:
            if r['spot'] == spot_slug and r['variable'] == variable:
                out[datetime.fromisoformat(r['ts_utc'])] = r['value']
        return out

    def __len__(self):
        return len(self._rows)

    def save_csv(self, path):
        with open(path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted(
                self._rows, key=lambda r: (r['spot'], r['variable'], r['ts_utc'])))
