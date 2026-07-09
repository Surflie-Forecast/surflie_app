"""10 m wind forecast from the Open-Meteo Forecast API.

gfs_seamless is the same NOAA GFS data the old pipeline scraped from NOMADS
(~160 GRIB downloads per run), in a single HTTP request per spot, already
in knots.
"""
from . import _openmeteo

NAME = 'openmeteo-weather'
CRITICAL = True
SOURCE = 'openmeteo:gfs'

URL = 'https://api.open-meteo.com/v1/forecast'
MODEL = 'gfs_seamless'
VARMAP = {
    'wind_speed_10m': 'wind_speed',          # kn
    'wind_direction_10m': 'wind_direction',  # deg, coming-from
}


def fetch(spot, start_utc, end_utc):
    return _openmeteo.fetch_hourly_rows(
        URL, spot, VARMAP, SOURCE, start_utc, end_utc,
        extra_params={
            'models': MODEL,
            'wind_speed_unit': 'kn',
            'cell_selection': 'nearest',
        },
    )
