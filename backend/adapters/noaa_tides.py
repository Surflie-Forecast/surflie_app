"""Tide predictions from NOAA CO-OPS.

For each spot we pick the nearest tide-prediction station (trying the three
closest until one responds) and fetch two products:

  - 6-minute resolution predictions -> variable "tide_height"
  - high/low peaks                  -> variables "tide_high" / "tide_low"

All timestamps are requested in GMT and stored as UTC; builders convert to
each spot's local timezone.
"""
import math
from datetime import datetime, timezone

import requests

NAME = 'noaa-tides'
CRITICAL = False  # the app degrades gracefully without tide data

STATIONS_URL = ('https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/'
                'stations.json?type=tidepredictions')
PREDICTIONS_URL = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
HEADERS = {'User-Agent': 'surf-forecast-pipeline (github.com/pcarbomestre)'}

_stations_cache = None


def _haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_stations(lat, lon, limit=3):
    global _stations_cache
    if _stations_cache is None:
        resp = requests.get(STATIONS_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        _stations_cache = [
            s for s in resp.json().get('stations', [])
            if 'lat' in s and 'lng' in s
        ]
    ranked = sorted(
        _stations_cache,
        key=lambda s: _haversine_miles(lat, lon, float(s['lat']), float(s['lng'])))
    return ranked[:limit]


def _fetch_predictions(station_id, start_utc, end_utc, interval=None):
    params = {
        'product': 'predictions',
        'station': station_id,
        'begin_date': start_utc.strftime('%Y%m%d'),
        'end_date': end_utc.strftime('%Y%m%d'),
        'datum': 'MLLW',
        'time_zone': 'gmt',
        'units': 'metric',
        'format': 'json',
    }
    if interval:
        params['interval'] = interval
    resp = requests.get(PREDICTIONS_URL, params=params, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json().get('predictions') or []


def _rows(spot, predictions, source, start_utc, end_utc, variable=None):
    rows = []
    for p in predictions:
        try:
            ts = datetime.strptime(p['t'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            value = float(p['v'])
        except (KeyError, ValueError):
            continue
        if not (start_utc <= ts <= end_utc):
            continue
        var = variable
        if var is None:  # peaks carry their own type: H/HH -> high, L/LL -> low
            kind = p.get('type', '')
            var = 'tide_high' if kind.startswith('H') else 'tide_low'
        rows.append({
            'spot': spot.slug,
            'ts_utc': ts.isoformat(),
            'variable': var,
            'value': value,
            'source': source,
        })
    return rows


def fetch(spot, start_utc, end_utc):
    last_err = None
    for station in _nearest_stations(spot.latitude, spot.longitude):
        station_id, station_name = station['id'], station['name']
        source = f'noaa:{station_id}'
        try:
            raw = _fetch_predictions(station_id, start_utc, end_utc)
            if not raw:
                continue
            rows = _rows(spot, raw, source, start_utc, end_utc, variable='tide_height')
            peaks = _fetch_predictions(station_id, start_utc, end_utc, interval='hilo')
            rows += _rows(spot, peaks, source, start_utc, end_utc)
            print(f"    using station {station_name} ({station_id})")
            return rows
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"No tide station usable near {spot.name}: {last_err or 'no predictions returned'}")
