"""Primary swell forecast from the Open-Meteo Marine API.

The model is MeteoFrance MFWAM — the same wave model the old pipeline pulled
from Copernicus Marine (cmems_mod_glo_wav_anfc), so forecast values stay
continuous. One HTTP request per spot replaces the NetCDF subset download,
no credentials are required, and cell_selection=sea replaces the old
"snap to nearest water pixel" logic.
"""
from . import _openmeteo

NAME = 'openmeteo-marine'
CRITICAL = True
SOURCE = 'openmeteo:mfwam'

URL = 'https://marine-api.open-meteo.com/v1/marine'
MODEL = 'meteofrance_wave'
VARMAP = {
    'swell_wave_height': 'swell_height',       # m
    'swell_wave_period': 'swell_period',       # s
    'swell_wave_direction': 'swell_direction', # deg, coming-from
}


def fetch(spot, start_utc, end_utc):
    return _openmeteo.fetch_hourly_rows(
        URL, spot, VARMAP, SOURCE, start_utc, end_utc,
        extra_params={'models': MODEL, 'cell_selection': 'sea'},
    )
