"""Build the static JSON files consumed by the frontend.

Reads exclusively from the ForecastStore (normalized, UTC), converts to each
spot's local timezone here, and writes the API contract:

    api/spots.json                    list of spots (name, slug, region, lat/lon)
    api/forecast/{slug}.json          daily summaries + ratings
    api/hourly/{slug}/{date}.json     3-hourly swell + wind entries
    api/tide/{slug}.json              6-min tide curve + high/low peaks
    api/models/{slug}.json            per-model combined Hs for the overlay

Output keys and value types intentionally match the pre-rewrite contract so
the frontend needs no changes beyond slug-based URLs. The displayed "wave"
height is combined significant wave height (Hs) from the default model; the
swell partition (period/direction) qualifies it and drives the rating.
"""
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median
from zoneinfo import ZoneInfo

from rating_config import RatingConfig

HOURLY_STEP_H = 3          # cadence of hourly entries (matches the old 3-hourly data)
RATING_HOURS = (7, 19)     # local hours (inclusive) considered for the daily rating

# Wave models present in the store (source tag -> display label). Tags and
# preference order must match backend/adapters/openmeteo_marine.py; the app
# displays the first model with usable data at each spot.
WAVE_MODELS = {
    'openmeteo:gfswave025': 'GFS Wave 25km',
    'openmeteo:mfwam': 'MFWAM 8km',
    'openmeteo:ecmwf_wam9': 'ECMWF WAM 9km',
    'openmeteo:ecmwf_wam25': 'ECMWF WAM 25km',
    'openmeteo:gwam': 'DWD GWAM 25km',
}


def _usable(series):
    """A model's series counts only if it has real (non-masked) values.

    GFS Wave's 25 km land-sea mask yields all-zero series at some nearshore
    cells (e.g. C st. inside the Santa Barbara Channel).
    """
    return bool(series) and max(series.values()) > 0.01


def _pick_wave_source(store, slug):
    """First model (preference order) with usable Hs and a swell partition."""
    for src in WAVE_MODELS:
        if (_usable(store.series(slug, 'wave_height', src))
                and store.series(slug, 'swell_period', src)):
            return src
    return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, separators=(',', ':'))


def _local_series(store, slug, variable, tz, source=None):
    """{local datetime: value}, chronologically sorted."""
    utc = store.series(slug, variable, source)
    return {ts.astimezone(tz): v for ts, v in sorted(utc.items())}


def _fmt_dt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _day_label(date_obj, today):
    delta = (date_obj - today).days
    if delta == 0:
        return 'Today'
    if delta == 1:
        return 'Tomorrow'
    return date_obj.strftime('%A')


def _build_hourly(wave_h, swell_p, swell_d, wind_s, wind_d):
    """3-hourly entries per local date where waves and wind both exist.

    height is combined Hs; period/direction are the primary swell partition.
    """
    by_date = defaultdict(list)
    for dt, height in wave_h.items():
        if dt.hour % HOURLY_STEP_H or dt.minute:
            continue
        if not (dt in swell_p and dt in swell_d and dt in wind_s and dt in wind_d):
            continue
        by_date[dt.date()].append({
            'datetime': _fmt_dt(dt),
            'swell': {
                'height': round(height, 2),
                'period': round(swell_p[dt], 1),
                'direction': round(swell_d[dt]),
            },
            'wind': {
                'speed': round(wind_s[dt], 2),
                'direction': round(wind_d[dt]),
            },
        })
    return by_date


def _daily_rating(hourly_entries):
    """Most common hourly rating during prime hours (7am-7pm local)."""
    ratings = []
    for entry in hourly_entries:
        hour = int(entry['datetime'][11:13])
        if RATING_HOURS[0] <= hour <= RATING_HOURS[1]:
            ratings.append(RatingConfig.get_rating(
                entry['swell']['height'],
                entry['swell']['period'],
                entry['wind']['speed'],
            ))
    if not ratings:
        return 'Poor', '#ef4444'
    return Counter(ratings).most_common(1)[0][0]


def _tide_extremes(date_obj, tide_peaks, tide_raw):
    """(min_height, min_time, max_height, max_time) for one local date.

    Prefers true high/low peaks; falls back to the raw curve.
    """
    points = [(dt, v) for dt, v in tide_peaks.items() if dt.date() == date_obj]
    if not points:
        points = [(dt, v) for dt, v in tide_raw.items() if dt.date() == date_obj]
    if not points:
        return '0.00', '', '0.00', ''
    lo = min(points, key=lambda p: p[1])
    hi = max(points, key=lambda p: p[1])
    return f'{lo[1]:.2f}', lo[0].strftime('%H:%M'), f'{hi[1]:.2f}', hi[0].strftime('%H:%M')


def build_spot(store, spot, out_dir):
    tz = ZoneInfo(spot.timezone)
    wave_src = _pick_wave_source(store, spot.slug)
    if wave_src is None:
        print(f"  ! no usable wave model for {spot.name}; skipping")
        return False
    wave_h = _local_series(store, spot.slug, 'wave_height', tz, source=wave_src)
    swell_p = _local_series(store, spot.slug, 'swell_period', tz, source=wave_src)
    swell_d = _local_series(store, spot.slug, 'swell_direction', tz, source=wave_src)
    wind_s = _local_series(store, spot.slug, 'wind_speed', tz)
    wind_d = _local_series(store, spot.slug, 'wind_direction', tz)
    tide_raw = _local_series(store, spot.slug, 'tide_height', tz)
    tide_peaks = {}
    for var in ('tide_high', 'tide_low'):
        tide_peaks.update(_local_series(store, spot.slug, var, tz))
    tide_types = {
        dt: 'H' for dt in _local_series(store, spot.slug, 'tide_high', tz)}
    tide_types.update(
        {dt: 'L' for dt in _local_series(store, spot.slug, 'tide_low', tz)})

    if not wave_h:
        print(f"  ! no wave data for {spot.name}; skipping")
        return False

    # --- hourly files ---
    hourly_by_date = _build_hourly(wave_h, swell_p, swell_d, wind_s, wind_d)
    for date_obj, entries in hourly_by_date.items():
        entries.sort(key=lambda e: e['datetime'])
        _write_json(
            os.path.join(out_dir, 'hourly', spot.slug, f'{date_obj.isoformat()}.json'),
            entries)

    # --- daily forecast ---
    today = datetime.now(tz).date()
    by_date = defaultdict(lambda: defaultdict(list))
    for series, key in ((wave_h, 'height'), (swell_p, 'period'), (swell_d, 'sdir'),
                        (wind_s, 'speed'), (wind_d, 'wdir')):
        for dt, v in series.items():
            by_date[dt.date()][key].append(v)

    forecast = []
    for date_obj in sorted(d for d in by_date if by_date[d]['height'] and d >= today):
        vals = by_date[date_obj]
        rating, color = _daily_rating(hourly_by_date.get(date_obj, []))
        tide_min, tide_min_t, tide_max, tide_max_t = _tide_extremes(
            date_obj, tide_peaks, tide_raw)
        forecast.append({
            'day': _day_label(date_obj, today),
            'date': date_obj.strftime('%b %d'),
            'date_iso': date_obj.isoformat(),
            'swell': {
                'height': f"{mean(vals['height']):.2f}",
                'period': f"{mean(vals['period']):.1f}" if vals['period'] else '0.0',
                'direction': f"{median(vals['sdir']):.0f}" if vals['sdir'] else '0',
                'unit': 'm',
            },
            'wind': {
                'speed': f"{mean(vals['speed']):.1f}" if vals['speed'] else '0.0',
                'direction': f"{median(vals['wdir']):.0f}" if vals['wdir'] else '0',
                'unit': 'kts',
            },
            'tide': {
                'max': {'height': tide_max, 'time': tide_max_t or '--:--'},
                'min': {'height': tide_min, 'time': tide_min_t or '--:--'},
            },
            'rating': rating,
            'color': color,
        })

    _write_json(os.path.join(out_dir, 'forecast', f'{spot.slug}.json'), {
        'spotName': spot.name,
        'slug': spot.slug,
        'region': spot.region,
        'forecast': forecast,
    })

    # --- model comparison file (combined Hs, 3-hourly, per model) ---
    models_out = []
    for src, label in WAVE_MODELS.items():
        series = (wave_h if src == wave_src
                  else _local_series(store, spot.slug, 'wave_height', tz, source=src))
        if not _usable(series):
            continue
        points = [
            {'t': _fmt_dt(dt), 'v': round(v, 2)}
            for dt, v in series.items()
            if dt.hour % HOURLY_STEP_H == 0 and not dt.minute]
        if points:
            models_out.append({
                'id': src.split(':', 1)[1],
                'label': label,
                'default': src == wave_src,
                'series': points,
            })
    if models_out:
        _write_json(os.path.join(out_dir, 'models', f'{spot.slug}.json'),
                    {'variable': 'wave_height', 'unit': 'm', 'models': models_out})

    # --- tide file ---
    if tide_raw:
        _write_json(os.path.join(out_dir, 'tide', f'{spot.slug}.json'), {
            'dataset': [
                {'t': _fmt_dt(dt), 'v': round(v, 2)} for dt, v in tide_raw.items()],
            'peaks': [
                {'t': _fmt_dt(dt), 'v': round(v, 2), 'type': tide_types[dt]}
                for dt, v in sorted(tide_peaks.items())],
        })

    print(f"  ✓ {spot.name}: {len(forecast)} days, "
          f"{len(hourly_by_date)} hourly files, "
          f"{len(tide_raw)} tide points, "
          f"waves from {WAVE_MODELS[wave_src]}, "
          f"{len(models_out)} models in overlay")
    return True


def build_all(store, spots, out_dir):
    # Regenerate the whole API directory from scratch so renamed/removed
    # spots leave no stale files behind.
    for sub in ('forecast', 'hourly', 'tide', 'models'):
        shutil.rmtree(os.path.join(out_dir, sub), ignore_errors=True)

    built = []
    for spot in spots:
        if build_spot(store, spot, out_dir):
            built.append(spot)

    _write_json(os.path.join(out_dir, 'spots.json'), [
        {
            'name': s.name,
            'slug': s.slug,
            'region': s.region,
            'latitude': s.latitude,
            'longitude': s.longitude,
        }
        for s in built
    ])
    return built
