"""Tide predictions from NOAA CO-OPS, cached, with an Open-Meteo fallback.

Tide predictions are deterministic harmonics — they never change between
runs — so each spot keeps a rolling cache (data/tide_cache/<slug>.json,
gitignored; CI persists it via actions/cache) covering exactly the app's
display window. One seeding fetch fills the whole window; after that a run
only tops up the tail day(s) the window has grown into, and days that
scrolled out of the window are pruned on load. A run whose window is fully
covered makes zero API calls.

From the nearest usable tide-prediction station we fetch two products:

  - 6-minute resolution predictions -> variable "tide_height"
  - high/low peaks                  -> variables "tide_high" / "tide_low"

The whole cache always comes from a single station (mixing stations mixes
datum offsets): a top-up first re-uses the cached station, and only on its
failure reseeds the full window from the nearest stations.

If NOAA can't serve the missing span (the CO-OPS predictions product has
been unreliable since its Jul 2026 cloud migration), a non-empty cache is
served as-is — better a day short at the tail than blank cards. Only with
an empty cache do we fall back to Open-Meteo Marine's hourly
sea_level_height_msl: the hourly curve becomes tide_height and highs/lows
are local extrema refined by parabolic interpolation. Fallback heights are
relative to MSL rather than NOAA's MLLW datum (lows go negative) and are
never cached, so NOAA takes over again the moment it recovers.

All timestamps are requested in GMT and stored as UTC; builders convert to
each spot's local timezone.
"""
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from . import _openmeteo

NAME = 'noaa-tides'
CRITICAL = False  # the app degrades gracefully without tide data

STATIONS_URL = ('https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/'
                'stations.json?type=tidepredictions')
PREDICTIONS_URL = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
HEADERS = {'User-Agent': 'surf-forecast-pipeline (github.com/pcarbomestre)'}

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'tide_cache')

MARINE_URL = 'https://marine-api.open-meteo.com/v1/marine'
FALLBACK_SOURCE = 'openmeteo:sealevel'

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


def _fetch_predictions(station_id, start_utc, end_utc, interval=None,
                       attempts=3, backoff=2):
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
    # The predictions endpoint intermittently returns "No Predictions data
    # was found" for stations that do have data (observed Jul 2026); the
    # same request succeeds on retry, so treat an empty body as transient.
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff * attempt)
        resp = requests.get(PREDICTIONS_URL, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        predictions = resp.json().get('predictions')
        if predictions:
            return predictions
    return []


def _points(predictions, start_utc, end_utc, variable=None):
    """Normalize raw API predictions to cache points within the window."""
    points = []
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
        points.append({'t': ts.isoformat(), 'var': var, 'v': value})
    return points


def _fetch_station_points(station_id, start_utc, end_utc):
    """Both products for one station and span, or [] if the curve is empty."""
    raw = _fetch_predictions(station_id, start_utc, end_utc)
    if not raw:
        return []
    points = _points(raw, start_utc, end_utc, variable='tide_height')
    peaks = _fetch_predictions(station_id, start_utc, end_utc, interval='hilo')
    points += _points(peaks, start_utc, end_utc)
    return points


# --- rolling per-spot cache ----------------------------------------------

def _cache_path(spot):
    return os.path.join(CACHE_DIR, f'{spot.slug}.json')


def _load_cache(spot):
    try:
        with open(_cache_path(spot)) as f:
            cache = json.load(f)
        if cache.get('station') and isinstance(cache.get('points'), list):
            return cache
    except Exception:
        pass
    return {'station': None, 'station_name': '', 'covered_through': '', 'points': []}


def _save_cache(spot, cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(spot), 'w') as f:
        json.dump(cache, f)


def _merge_points(cache, new_points):
    """Merge by (variable, timestamp); new values win on overlap."""
    merged = {(p['var'], p['t']): p for p in cache['points']}
    merged.update({(p['var'], p['t']): p for p in new_points})
    cache['points'] = [merged[k] for k in sorted(merged)]


def _cache_rows(spot, cache):
    source = f"noaa:{cache['station']}"
    return [{
        'spot': spot.slug,
        'ts_utc': p['t'],
        'variable': p['var'],
        'value': p['v'],
        'source': source,
    } for p in cache['points']]


# --- Open-Meteo fallback --------------------------------------------------

def _fallback_rows(spot, start_utc, end_utc):
    """Derive tide_height + tide_high/tide_low from Open-Meteo sea level.

    The hourly sea_level_height_msl series is stored as tide_height; peaks
    are interior local extrema, with time and height refined by fitting a
    parabola through the extremum and its two neighbours.
    """
    rows, _ = _openmeteo.fetch_hourly_rows_batch(
        MARINE_URL, [(spot.slug, spot.latitude, spot.longitude)],
        {'sea_level_height_msl': 'tide_height'}, FALLBACK_SOURCE,
        start_utc, end_utc, extra_params={})
    series = sorted(
        (datetime.fromisoformat(r['ts_utc']), r['value']) for r in rows)
    for i in range(1, len(series) - 1):
        (_, prev), (ts, v), (_, nxt) = series[i - 1], series[i], series[i + 1]
        # Strict on the left so a flat pair of equal values yields one peak.
        is_max = v > prev and v >= nxt
        is_min = v < prev and v <= nxt
        if not (is_max or is_min):
            continue
        denom = prev - 2 * v + nxt
        offset = 0.5 * (prev - nxt) / denom if denom else 0.0
        rows.append({
            'spot': spot.slug,
            'ts_utc': (ts + timedelta(hours=offset)).isoformat(),
            'variable': 'tide_high' if is_max else 'tide_low',
            'value': v - 0.25 * (prev - nxt) * offset,
            'source': FALLBACK_SOURCE,
        })
    return rows


# --- entry point ----------------------------------------------------------

def fetch(spot, start_utc, end_utc):
    cache = _load_cache(spot)

    # Days that scrolled out of the display window are gone for good.
    cutoff = start_utc.isoformat()
    cache['points'] = [p for p in cache['points'] if p['t'] >= cutoff]

    end_date = end_utc.date().isoformat()
    if cache['points'] and cache['covered_through'] >= end_date:
        _save_cache(spot, cache)  # persist the prune
        print(f"    tide cache covers window (station {cache['station_name']} "
              f"{cache['station']}); no fetch")
        return _cache_rows(spot, cache)

    # Top up from the cached station so the whole series shares one datum.
    if cache['points'] and cache['station']:
        try:
            covered = datetime.fromisoformat(cache['covered_through']).replace(
                tzinfo=timezone.utc)
            missing_start = max(start_utc, covered)
            points = _fetch_station_points(cache['station'], missing_start, end_utc)
        except Exception:
            points = []
        if points:
            _merge_points(cache, points)
            cache['covered_through'] = end_date
            _save_cache(spot, cache)
            print(f"    tide cache topped up through {end_date} "
                  f"(station {cache['station_name']} {cache['station']})")
            return _cache_rows(spot, cache)

    # Seed (or reseed after a station failure): the full window, nearest
    # usable station wins.
    last_err = None
    for station in _nearest_stations(spot.latitude, spot.longitude):
        try:
            points = _fetch_station_points(station['id'], start_utc, end_utc)
        except Exception as e:
            last_err = e
            continue
        if not points:
            continue
        cache = {
            'station': station['id'],
            'station_name': station['name'],
            'covered_through': end_date,
            'points': [],
        }
        _merge_points(cache, points)
        _save_cache(spot, cache)
        print(f"    tide cache seeded through {end_date} "
              f"(station {station['name']} {station['id']})")
        return _cache_rows(spot, cache)

    # NOAA couldn't serve the missing span. A partial cache beats mixing
    # datums with the fallback; only an empty cache falls through.
    if cache['points']:
        print(f"    ! NOAA top-up failed near {spot.name} "
              f"({last_err or 'no predictions returned'}); serving cache "
              f"through {cache['covered_through']}")
        return _cache_rows(spot, cache)

    print(f"    ! no NOAA station usable near {spot.name} "
          f"({last_err or 'no predictions returned'}); "
          f"falling back to Open-Meteo sea level (MSL datum, uncached)")
    rows = _fallback_rows(spot, start_utc, end_utc)
    if rows:
        return rows
    raise RuntimeError(
        f"No tide source usable near {spot.name}: {last_err or 'no predictions returned'}")
