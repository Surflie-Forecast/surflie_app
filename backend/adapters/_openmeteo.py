"""Shared plumbing for Open-Meteo APIs (weather + marine)."""
import time
from datetime import datetime, timezone

import requests


def get_json(url, params, retries=3, backoff_s=5):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff_s)
    raise RuntimeError(f"Open-Meteo request failed after {retries} attempts: {last_err}")


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
