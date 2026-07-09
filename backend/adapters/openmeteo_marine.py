"""Wave forecasts from the Open-Meteo Marine API — every model, tagged by source.

All models are fetched with the full variable set (combined Hs + primary
swell height/period/direction); builders pick the displayed model per spot
in MODELS order, skipping models whose nearest cell is degenerate — e.g.
NCEP GFS Wave's 25 km land-sea mask returns all-zero inside the Santa
Barbara Channel at C st. even with cell_selection=sea. Model choice and
ordering come from data_exploration/model_exploration_devereaux.ipynb.

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


def fetch(spot, start_utc, end_utc):
    rows = []
    failures = []
    for model, source in MODELS:
        try:
            rows.extend(_openmeteo.fetch_hourly_rows(
                URL, spot, VARMAP, source, start_utc, end_utc,
                extra_params={'models': model, 'cell_selection': 'sea'},
            ))
        except Exception as e:
            failures.append(model)
            print(f"    ! wave model {model} failed: {e}")

    if not rows and failures:
        raise RuntimeError(f"all wave models failed: {', '.join(failures)}")
    return rows
