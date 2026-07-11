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

# Optional per-spot rating tuning. Any blank/absent column is derived from the
# spot's coastline orientation at rating time (see rating_config.build_profile),
# so these are refinements, not requirements — a new spot needs only lat/lon.
RATING_FIELDS = [
    'swell_min', 'swell_ideal', 'swell_max', 'offshore_dir',
    'size_min', 'size_ideal', 'size_max', 'tide_min', 'tide_max',
]

FIELDNAMES = [
    'site_name', 'slug', 'region', 'latitude', 'longitude', 'timezone',
    'closest_buoy', 'resolved_cells', *RATING_FIELDS,
]


def _opt_float(row, key):
    """Parse an optional numeric CSV cell; blank/missing/bad -> None."""
    raw = (row.get(key) or '').strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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
    # Hand-entered rating tuning: {RATING_FIELDS key: float}. Only keys the CSV
    # actually filled in are present; the rest are derived from coastline
    # orientation when the profile is built. Empty dict => fully derived.
    rating_meta: dict = field(default_factory=dict)

    @property
    def coast_normal(self):
        """Compass bearing (deg) this spot's coastline faces the open ocean.

        Used to DERIVE a rating profile (swell window + offshore wind) when the
        CSV supplies none. We have no coastline dataset, so this is a coarse
        default: US West Coast breaks face broadly west. Override precisely by
        filling swell_ideal / offshore_dir in locations.csv for any spot where
        this is wrong (e.g. a south- or east-facing break).
        """
        return 270.0


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
            rating_meta = {k: v for k in RATING_FIELDS
                           if (v := _opt_float(row, k)) is not None}
            spots.append(Spot(
                name=row['site_name'].strip(),
                slug=row['slug'].strip(),
                region=row['region'].strip(),
                latitude=float(row['latitude']),
                longitude=float(row['longitude']),
                timezone=row['timezone'].strip(),
                closest_buoy=(row.get('closest_buoy') or '').strip(),
                resolved_cells=resolved,
                rating_meta=rating_meta,
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
            row = {
                'site_name': spot.name,
                'slug': spot.slug,
                'region': spot.region,
                'latitude': spot.latitude,
                'longitude': spot.longitude,
                'timezone': spot.timezone,
                'closest_buoy': spot.closest_buoy,
                'resolved_cells': blob,
            }
            # Only write back rating cells the CSV actually supplied, so derived
            # spots stay blank (and keep deriving) rather than being frozen.
            for k in RATING_FIELDS:
                row[k] = spot.rating_meta.get(k, '')
            writer.writerow(row)
