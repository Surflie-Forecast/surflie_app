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

    def series(self, spot_slug, variable, source=None):
        """Return {utc datetime: value} for one spot/variable.

        source=None matches any provider; pass a source tag to disambiguate
        variables that several models emit (e.g. wave_height).
        """
        out = {}
        for r in self._rows:
            if r['spot'] == spot_slug and r['variable'] == variable \
                    and (source is None or r['source'] == source):
                out[datetime.fromisoformat(r['ts_utc'])] = r['value']
        return out

    def sources(self):
        """Set of source tags with at least one row — lets the pipeline tell
        which planned fetches actually delivered."""
        return {r['source'] for r in self._rows}

    def __len__(self):
        return len(self._rows)

    def save_csv(self, path):
        with open(path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted(
                self._rows, key=lambda r: (r['spot'], r['variable'], r['ts_utc'])))
