"""Spot metadata loaded from data/locations.csv.

The slug is the stable identifier used for API file paths and URLs;
the display name is free to contain spaces/punctuation and to change.

resolved_cells caches, per Open-Meteo marine model source tag, the working
grid coordinate found for this spot (the seaward cell a dead-cell search
landed on). Persisting it lets subsequent runs batch every spot in one
multi-coordinate request instead of re-running the per-spot offset search.

closest_buoy preassigns the CDIP station whose comparison file the Buoys
view opens for this spot. Blank rows are filled once by buoys.build_buoys
(nearest active buoy) and persisted here, so it's never recomputed — and a
hand-edited value in the CSV always wins.
"""
import csv
import json
from dataclasses import dataclass, field

FIELDNAMES = [
    'site_name', 'slug', 'region', 'latitude', 'longitude', 'timezone',
    'closest_buoy', 'resolved_cells',
]


@dataclass(frozen=True)
class Spot:
    name: str
    slug: str
    region: str
    latitude: float
    longitude: float
    timezone: str  # IANA name, e.g. "America/Los_Angeles"
    # CDIP station id ('' = unassigned; buoys.build_buoys fills it via
    # object.__setattr__ — the one sanctioned mutation of a frozen field).
    closest_buoy: str = ''
    # source tag -> [lat, lon] of the resolved working cell. frozen=True
    # protects the identity fields; the dict CONTENTS are mutated in place as
    # models resolve during a run (never reassign the attribute).
    resolved_cells: dict = field(default_factory=dict)


def load_spots(path):
    spots = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            # Tolerate the column being absent (old CSV), blank, or malformed.
            resolved = {}
            raw = (row.get('resolved_cells') or '').strip()
            if raw:
                try:
                    resolved = json.loads(raw)
                except Exception:
                    resolved = {}
            spots.append(Spot(
                name=row['site_name'].strip(),
                slug=row['slug'].strip(),
                region=row['region'].strip(),
                latitude=float(row['latitude']),
                longitude=float(row['longitude']),
                timezone=row['timezone'].strip(),
                closest_buoy=(row.get('closest_buoy') or '').strip(),
                resolved_cells=resolved,
            ))
    return spots


def save_spots(path, spots):
    """Write spots back to CSV, preserving original columns and appending the
    resolved_cells JSON blob (compact, or empty string when unresolved)."""
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for spot in spots:
            blob = ''
            if spot.resolved_cells:
                blob = json.dumps(spot.resolved_cells, separators=(',', ':'))
            writer.writerow({
                'site_name': spot.name,
                'slug': spot.slug,
                'region': spot.region,
                'latitude': spot.latitude,
                'longitude': spot.longitude,
                'timezone': spot.timezone,
                'closest_buoy': spot.closest_buoy,
                'resolved_cells': blob,
            })
