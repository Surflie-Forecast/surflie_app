"""Wave forecasts from the Open-Meteo Marine API — every model, tagged by source.

All models are fetched with the full variable set (combined Hs + primary
swell height/period/direction), so every spot is represented in every model
with the first entry of MODELS (NCEP GFS Wave) as the default. Model choice
and ordering come from data_exploration/model_exploration_devereaux.ipynb.

A model's nearest grid cell can be dead nearshore even with
cell_selection=sea — GFS Wave's 25 km land-sea mask returns all-zero inside
the Santa Barbara Channel at C st. When that happens the model is re-sampled
at increasing seaward offsets (one, then two, 25 km cells; westward
directions first for a west coast) until a live cell answers, mirroring the
nearest-valid-water-cell logic of the MOP adapter.

Not every model serves every variable (ECMWF WAM is Hs-only); missing
variables simply yield no rows. A single model failing degrades to the
remaining models, never the whole source: the adapter only fails if no
model returned anything.
"""
from . import _openmeteo

NAME = 'openmeteo-marine'
CRITICAL = True

URL = 'https://marine-api.open-meteo.com/v1/marine'

# api model name -> source tag (the store/builders contract), preference order
MODELS = [
    ('ncep_gfswave025', 'openmeteo:gfswave025'),
    ('meteofrance_wave', 'openmeteo:mfwam'),
    ('ecmwf_wam', 'openmeteo:ecmwf_wam9'),
    ('ecmwf_wam025', 'openmeteo:ecmwf_wam25'),
    ('gwam', 'openmeteo:gwam'),
]

# wave_height (combined Hs — what a buoy measures) is the displayed "wave";
# the swell partition qualifies it and drives the rating.
VARMAP = {
    'wave_height': 'wave_height',              # m, combined Hs
    'swell_wave_height': 'swell_height',       # m
    'swell_wave_period': 'swell_period',       # s
    'swell_wave_direction': 'swell_direction', # deg, coming-from
}

# Seaward search for a live cell: (dlat, dlon) unit directions, W/SW first,
# scaled by one then two 25 km cells.
DIRECTIONS = [(0, -1), (-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1), (0, 1)]
OFFSET_RINGS_DEG = (0.25, 0.5)


def _usable(rows):
    hs = [r['value'] for r in rows if r['variable'] == 'wave_height']
    return bool(hs) and max(hs) > 0.01


def _resolve_spot(spot, model, source, start_utc, end_utc):
    """Run the per-spot dead-cell offset search for one model.

    Tries the spot's own coordinate, then increasing seaward offsets until a
    live cell answers. On success records the working (snapped) coordinate into
    spot.resolved_cells[source] and returns its rows; on total failure returns
    an empty list (leaving source unresolved).
    """
    def at(slug, lat, lon):
        return _openmeteo.fetch_hourly_rows_batch(
            URL, [(slug, lat, lon)], VARMAP, source, start_utc, end_utc,
            extra_params={'models': model, 'cell_selection': 'sea'})

    rows, snapped = at(spot.slug, spot.latitude, spot.longitude)
    if _usable(rows):
        coord = snapped.get(spot.slug, (spot.latitude, spot.longitude))
        spot.resolved_cells[source] = [coord[0], coord[1]]
        return rows

    for radius in OFFSET_RINGS_DEG:
        for dlat, dlon in DIRECTIONS:
            lat = spot.latitude + dlat * radius
            lon = spot.longitude + dlon * radius
            rows, snapped = at(spot.slug, lat, lon)
            if _usable(rows):
                print(f"    {model}: dead cell at {spot.slug}; "
                      f"sampled {radius:.2f}° offset "
                      f"({dlat * radius:+.2f}, {dlon * radius:+.2f})")
                coord = snapped.get(spot.slug, (lat, lon))
                spot.resolved_cells[source] = [coord[0], coord[1]]
                return rows
    print(f"    ! wave model {model}: no live cell within "
          f"{OFFSET_RINGS_DEG[-1]:.2f}° of {spot.slug}")
    return []


def _group_by_slug(rows):
    grouped = {}
    for r in rows:
        grouped.setdefault(r['spot'], []).append(r)
    return grouped


def fetch_batch(spots, start_utc, end_utc):
    """Fetch every model for every spot, batching cached cells into one request
    per model. Mutates each spot's resolved_cells with newly resolved coords."""
    out = []
    failures = []

    for model, source in MODELS:
        try:
            known = [s for s in spots if source in s.resolved_cells]
            unknown = [s for s in spots if source not in s.resolved_cells]

            if known:
                coords = [(s.slug, *s.resolved_cells[source]) for s in known]
                batch_rows, _ = _openmeteo.fetch_hourly_rows_batch(
                    URL, coords, VARMAP, source, start_utc, end_utc,
                    extra_params={'models': model, 'cell_selection': 'sea'})
                grouped = _group_by_slug(batch_rows)
                for s in known:
                    spot_rows = grouped.get(s.slug, [])
                    if _usable(spot_rows):
                        out.extend(spot_rows)
                    else:
                        # Cached cell went dead — drop it and re-resolve below.
                        s.resolved_cells.pop(source, None)
                        unknown.append(s)

            for s in unknown:
                out.extend(_resolve_spot(s, model, source, start_utc, end_utc))
        except Exception as e:
            failures.append(model)
            print(f"    ! wave model {model} failed: {e}")

    if not out and failures:
        raise RuntimeError(f"all wave models failed: {', '.join(failures)}")
    return out


def fetch(spot, start_utc, end_utc):
    return fetch_batch([spot], start_utc, end_utc)
