"""Wave forecasts from the Open-Meteo Marine API — every model, tagged by source.

All models are fetched with the full variable set (combined Hs + primary
swell height/period/direction), so every spot is represented in every model
with the first entry of MODELS (NCEP GFS Wave) as the default. Model choice
and ordering come from data_exploration/model_exploration_devereaux.ipynb.

A model's nearest grid cell can be dead nearshore even with
cell_selection=sea — a 25 km land-sea mask returns all-zero inside the
Santa Barbara Channel at C st. When that happens the model is re-sampled at
increasing seaward offsets (one, then two, 0.25 deg steps; westward
directions first for a west coast) until a live cell answers, mirroring the
nearest-valid-water-cell logic of the MOP adapter. The finer grids rarely
need it: measured 2026-08-03, GFS Wave 0.16 deg resolved a live cell at all
276 spots' own coordinates, against ~155 needing the offset search on the
0.25 deg grid, which also displaced some spots (Malibu, Topanga) 30+ km
offshore — past the Channel Islands shadow line that decides their swell.

Not every model serves every variable; missing variables simply yield no
rows. ECMWF WAM publishes only combined Hs/period/direction — no swell
partition at either resolution, so the 9 km domain loses nothing the 25 km
one had (verified against the API 2026-08-03). A single model failing degrades to the
remaining models, never the whole source: the adapter only fails if no
model returned anything.

Models publish on different cycles (GFS Wave / ECMWF every 6h, MFWAM /
GWAM twice daily), so on a 6h schedule roughly two fetches in four return
byte-identical data. plan_models compares each model's
last_run_availability_time (from Open-Meteo's per-model meta.json — the
same feed its model-updates dashboard reads) against the stamp of our last
successful fetch, so scheduled runs fetch only what's actually new and
carry the rest forward. Meta being unreachable counts as due: fail open,
never silently stale.
"""
import json

import requests

from . import _openmeteo

NAME = 'openmeteo-marine'
CRITICAL = True

URL = 'https://marine-api.open-meteo.com/v1/marine'
META_URL = 'https://marine-api.open-meteo.com/data/{model}/static/meta.json'
# meta.json slugs that differ from the API's `models=` parameter name
META_SLUGS = {'gwam': 'dwd_gwam'}

# api model name -> source tag (the store/builders contract), preference order
MODELS = [
    ('ncep_gfswave016', 'openmeteo:gfswave016'),
    ('meteofrance_wave', 'openmeteo:mfwam'),
    ('ecmwf_wam', 'openmeteo:ecmwf_wam9'),
]

# wave_height (combined Hs — what a buoy measures) is the displayed "wave";
# the primary swell partition qualifies it and drives the rating.
VARMAP = {
    'wave_height': 'wave_height',              # m, combined Hs
    # Mean combined-sea period/direction: the only flavor ECMWF WAM
    # publishes, fetched for every model so the overlay tooltip can fall
    # back to it where the swell partition is absent. Never feeds the
    # rating (that stays on swell_period/swell_direction).
    'wave_period': 'wave_period',              # s, energy-mean
    'wave_direction': 'wave_direction',        # deg, mean coming-from
    'swell_wave_height': 'swell_height',       # m, primary swell train
    'swell_wave_period': 'swell_period',       # s
    'swell_wave_direction': 'swell_direction', # deg, coming-from
    # Full swell-train breakdown, stored for the forecast-card
    # disaggregation (no consumer yet). Depth varies by model — GFS Wave
    # carries three trains + wind sea, MFWAM two, GWAM one, ECMWF none —
    # and absent variables simply yield no rows.
    'secondary_swell_wave_height': 'swell2_height',
    'secondary_swell_wave_period': 'swell2_period',
    'secondary_swell_wave_direction': 'swell2_direction',
    'tertiary_swell_wave_height': 'swell3_height',
    'tertiary_swell_wave_period': 'swell3_period',
    'tertiary_swell_wave_direction': 'swell3_direction',
    'wind_wave_height': 'windwave_height',
    'wind_wave_period': 'windwave_period',
    'wind_wave_direction': 'windwave_direction',
}

# Seaward search for a live cell: (dlat, dlon) unit directions, W/SW first,
# scaled by one then two 25 km cells.
DIRECTIONS = [(0, -1), (-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1), (0, 1)]
OFFSET_RINGS_DEG = (0.25, 0.5)


def plan_models(state_path):
    """Split MODELS into (due, skipped) for a scheduled run.

    A model is due when its last_run_availability_time on Open-Meteo differs
    from the stamp of our last successful fetch (state_path, JSON:
    source tag -> availability epoch). Missing state or unreachable meta
    counts as due — fail open, never silently stale.

    Returns (due, skipped, avail) where due/skipped are [(model, source)]
    and avail maps source -> the availability stamp to persist once that
    model's fetch succeeds (see stamp_models).
    """
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        state = {}

    due, skipped, avail = [], [], {}
    for model, source in MODELS:
        ts = None
        try:
            slug = META_SLUGS.get(model, model)
            r = requests.get(META_URL.format(model=slug), timeout=15)
            r.raise_for_status()
            ts = r.json().get('last_run_availability_time')
        except Exception as e:
            print(f"    ! meta for {model} unreachable ({e}); fetching anyway")
        avail[source] = ts
        if ts is not None and state.get(source) == ts:
            skipped.append((model, source))
        else:
            due.append((model, source))
    return due, skipped, avail


def stamp_models(state_path, avail, fetched_sources):
    """Persist availability stamps for the models whose fetch produced rows,
    merging over the previous state so skipped models keep their stamps. A
    due model that failed keeps its old stamp and stays due next run."""
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        state = {}
    for source in fetched_sources:
        if avail.get(source) is not None:
            state[source] = avail[source]
    with open(state_path, 'w') as f:
        json.dump(state, f, indent=1)


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


def fetch_batch(spots, start_utc, end_utc, models=None):
    """Fetch every model (or just `models`, a [(model, source)] subset from
    plan_models) for every spot, batching cached cells into one request per
    model. Mutates each spot's resolved_cells with newly resolved coords."""
    out = []
    failures = []

    for model, source in (MODELS if models is None else models):
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
