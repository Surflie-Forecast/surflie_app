"""Shared plumbing for Open-Meteo APIs (weather + marine)."""
import re
import time
from datetime import datetime, timezone

import requests

# Open-Meteo rejects the whole request with 400 when end_date exceeds a
# model's current forecast horizon, naming the allowed max in the body:
#   {"reason":"Parameter 'end_date' is out of allowed range from ... to 2026-07-25","error":true}
# The horizon shifts day-to-day with each model run, so we clamp end_date to
# the stated max and retry once rather than losing the entire (critical) source.
_END_DATE_RANGE_RE = re.compile(
    r"end_date.*allowed range from \S+ to (\d{4}-\d{2}-\d{2})")


def _clamp_end_date_from_error(body):
    """Return the allowed max end_date (str) if body is that 400, else None."""
    try:
        reason = (body or {}).get('reason', '')
    except AttributeError:
        return None
    m = _END_DATE_RANGE_RE.search(reason)
    return m.group(1) if m else None


def get_json(url, params, retries=3, backoff_s=5, timeout=60):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 400 and 'end_date' in params:
                capped = _clamp_end_date_from_error(_safe_json(r))
                if capped and capped < str(params['end_date']):
                    print(f"    end_date {params['end_date']} beyond horizon; "
                          f"clamping to {capped}")
                    params = {**params, 'end_date': capped}
                    continue  # retry immediately with the clamped window
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff_s)
    raise RuntimeError(f"Open-Meteo request failed after {retries} attempts: {last_err}")


def _safe_json(response):
    try:
        return response.json()
    except ValueError:
        return None


def fetch_hourly_rows(url, spot, varmap, source, start_utc, end_utc, extra_params):
    """Fetch hourly variables for one spot and return normalized store rows.

    varmap maps Open-Meteo variable names -> canonical store variable names.
    """
    params = {
        'latitude': spot.latitude,
        'longitude': spot.longitude,
        'hourly': ','.join(varmap),
        'start_date': start_utc.date().isoformat(),
        'end_date': end_utc.date().isoformat(),
        'timezone': 'GMT',
    }
    params.update(extra_params)

    hourly = get_json(url, params).get('hourly') or {}
    times = [
        datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        for t in hourly.get('time', [])
    ]

    rows = []
    for api_var, variable in varmap.items():
        values = hourly.get(api_var)
        if values is None:
            # Multi-model responses suffix keys with the model name.
            values = next(
                (hourly[k] for k in hourly if k.startswith(api_var + '_')), None)
        if values is None:
            continue
        for ts, val in zip(times, values):
            if val is not None and start_utc <= ts <= end_utc:
                rows.append({
                    'spot': spot.slug,
                    'ts_utc': ts.isoformat(),
                    'variable': variable,
                    'value': float(val),
                    'source': source,
                })
    return rows


def _parse_hourly(obj, slug, varmap, source, start_utc, end_utc):
    """Turn one location's response object into store rows for that slug."""
    hourly = obj.get('hourly') or {}
    times = [
        datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        for t in hourly.get('time', [])
    ]

    rows = []
    for api_var, variable in varmap.items():
        values = hourly.get(api_var)
        if values is None:
            # Multi-model responses suffix keys with the model name.
            values = next(
                (hourly[k] for k in hourly if k.startswith(api_var + '_')), None)
        if values is None:
            continue
        for ts, val in zip(times, values):
            if val is not None and start_utc <= ts <= end_utc:
                rows.append({
                    'spot': slug,
                    'ts_utc': ts.isoformat(),
                    'variable': variable,
                    'value': float(val),
                    'source': source,
                })
    return rows


# Coordinates per multi-location request: past ~200 the comma-joined
# lat/lon lists push the URL beyond practical limits, so batches are split
# into chunks and the responses merged.
BATCH_CHUNK = 100


def fetch_hourly_rows_batch(url, coords, varmap, source, start_utc, end_utc,
                            extra_params):
    """Fetch hourly variables for many coordinates in as few requests as
    possible (one per BATCH_CHUNK coordinates).

    coords is a list of (slug, lat, lon). Open-Meteo accepts comma-joined
    latitude/longitude and returns a JSON array, one object per coordinate in
    input order (a single coordinate returns a bare object, normalized here).

    Returns (rows, snapped): rows are store rows for all locations; snapped
    maps slug -> (resp_lat, resp_lon), the grid coordinate each location
    reported it actually used, so callers can cache the resolved cell.
    """
    rows = []
    snapped = {}
    # A 100-coordinate chunk with the full marine variable set over a 16-day
    # window is a large response; 60 s was tight enough that GFS Wave — the
    # partition-bearing display model — timed out on CI and the whole site
    # silently degraded to a partition-less model. Scale with chunk size.
    timeout = max(60, 2 * len(coords[:BATCH_CHUNK]))
    for chunk_start in range(0, len(coords), BATCH_CHUNK):
        chunk = coords[chunk_start:chunk_start + BATCH_CHUNK]
        params = {
            'latitude': ','.join(str(lat) for _, lat, _ in chunk),
            'longitude': ','.join(str(lon) for _, _, lon in chunk),
            'hourly': ','.join(varmap),
            'start_date': start_utc.date().isoformat(),
            'end_date': end_utc.date().isoformat(),
            'timezone': 'GMT',
        }
        params.update(extra_params)

        data = get_json(url, params, timeout=timeout)
        if isinstance(data, dict):
            data = [data]

        for i, (slug, _, _) in enumerate(chunk):
            if i >= len(data):
                break
            obj = data[i]
            if not obj or not obj.get('hourly'):
                continue
            rows.extend(_parse_hourly(obj, slug, varmap, source,
                                      start_utc, end_utc))
            if obj.get('latitude') is not None and obj.get('longitude') is not None:
                snapped[slug] = (obj['latitude'], obj['longitude'])
    return rows, snapped
