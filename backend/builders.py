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
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from zoneinfo import ZoneInfo

from rating_config import build_profile, bucket_for_score, score_conditions

HOURLY_STEP_H = 3          # cadence of hourly entries (matches the old 3-hourly data)
RATING_HOURS = (7, 19)     # local hours (inclusive) considered for the daily rating

# Wave models present in the store (source tag -> display label). Tags and
# preference order must match backend/adapters/openmeteo_marine.py; the app
# displays the first model with usable data at each spot.
WAVE_MODELS = {
    'openmeteo:gfswave016': 'GFS Wave 16km',
    'openmeteo:mfwam': 'MFWAM 8km',
    'openmeteo:ecmwf_wam9': 'ECMWF WAM 9km',
    # Nearshore products, after every global model: they act as last-resort
    # display picks but mainly feed the comparison overlay (our salix
    # forecast is what skill scoring evaluates before any promotion).
    # Transect-scale (~100 m) outranks the 1-km grid where matched.
    'surflie:nearshore': 'Salix',
    'cdip:mop-transect-fc': 'CDIP MOP transect',
    'cdip:mop': 'CDIP MOP 1km',
    'nwps:cg1': 'NOAA NWPS',
}

# Overlay-only reference series, never display-picked: the nowcast is
# observed-driven (buoy spectra through the MOP transform) — a truth proxy
# for the elapsed window, not a forecast.
OVERLAY_ONLY = {
    'cdip:mop-nowcast': 'CDIP observed',
}

# Headline ("surf") source: where it has data, its Hs + peak period/direction
# replace the display model's in bars, cards and the rating height. Skill vs
# the MOP nowcast put it at ~0.19 m RMSE / ~0 bias against GFS Wave's 0.29 /
# +0.10 (Oct 2026). The display model (wave_src) still supplies the swell-
# train breakdown, the rating's period/direction, and any hour it lacks.
SURF_SOURCE = 'surflie:nearshore'


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


SURF_MAX_GAP_H = 3   # widest gap between headline points that gets filled


def _fill_hours(series, circular=False):
    """Linearly fill whole hours between points at most SURF_MAX_GAP_H apart.

    Salix turns 3-hourly on the UTC grid after ~4.5 days, which never meets
    the bars' local 3-hourly grid (UTC-7/-8); without filling, every later
    bar fell back to the display model. Steps run in UTC so DST transitions
    stay exact; directions interpolate along the shorter arc.
    """
    out = dict(series)
    pts = sorted(series.items())
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        gap_h = (t1 - t0).total_seconds() / 3600
        if gap_h <= 1 or gap_h > SURF_MAX_GAP_H:
            continue
        delta = ((v1 - v0 + 180) % 360 - 180) if circular else v1 - v0
        u0 = t0.astimezone(timezone.utc)
        for k in range(1, int(gap_h)):
            t = (u0 + timedelta(hours=k)).astimezone(t0.tzinfo)
            v = v0 + delta * k / gap_h
            out[t] = v % 360 if circular else v
    return dict(sorted(out.items()))


def _surf_series(store, slug, tz, fill=True):
    """Headline-source (height, period, direction) local series.

    Points without a real height are dropped: salix artifacts end on a
    0.0 m point with no period/direction. fill=True adds interpolated whole
    hours (for the bars' grid); the overlay line uses the raw points.
    """
    h = {dt: v for dt, v in _local_series(
        store, slug, 'wave_height', tz, source=SURF_SOURCE).items() if v > 0.01}
    p = _local_series(store, slug, 'swell_period', tz, source=SURF_SOURCE)
    d = _local_series(store, slug, 'swell_direction', tz, source=SURF_SOURCE)
    if fill:
        h, p, d = _fill_hours(h), _fill_hours(p), _fill_hours(d, circular=True)
    return h, p, d


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


def _build_hourly(wave_h, swell_p, swell_d, wind_s, wind_d, profile,
                  trains=(), disp_p=None, disp_d=None):
    """3-hourly entries per local date where waves and wind both exist.

    Displayed height is combined Hs and displayed period/direction the
    combined mean pair (disp_p/disp_d, falling back to the primary swell
    where a source has no mean); the SCORE judges swell_p/swell_d - the
    nearshore peak pair where it exists, else the primary swell
    partition. trains is the display model's swell-train
    breakdown ((h, p, d) series per train, primary first, wind sea
    excluded); hours where at least one train is complete gain a `swells`
    list for the card's expandable row.
    """
    disp_p = disp_p or swell_p
    disp_d = disp_d or swell_d
    by_date = defaultdict(list)
    for dt, height in wave_h.items():
        if dt.hour % HOURLY_STEP_H or dt.minute:
            continue
        if not (dt in swell_p and dt in swell_d and dt in wind_s and dt in wind_d):
            continue
        score = round(score_conditions(
            height, swell_p[dt], swell_d[dt], wind_s[dt], wind_d[dt],
            profile=profile))
        rating, color = bucket_for_score(score)
        entry = {
            'datetime': _fmt_dt(dt),
            'swell': {
                'height': round(height, 2),
                'period': round(disp_p.get(dt, swell_p[dt]), 1),
                'direction': round(disp_d.get(dt, swell_d[dt])),
            },
            'wind': {
                'speed': round(wind_s[dt], 2),
                'direction': round(wind_d[dt]),
            },
            'score': score,
            'rating': rating,
            'color': color,
        }
        swells = [
            {'height': round(th[dt], 2), 'period': round(tp[dt], 1),
             'direction': round(td[dt])}
            for th, tp, td in trains
            if dt in th and dt in tp and dt in td
        ]
        if swells:
            entry['swells'] = swells
        by_date[dt.date()].append(entry)
    return by_date


def _daily_rating(hourly_entries):
    """Daily label/color/score from the prime-hours (7am-7pm) window.

    Averages the continuous hourly scores over daylight, then buckets - so a
    day reads by its typical daytime quality rather than by a mode over coarse
    labels. Returns (rating, color, score).
    """
    scores = [e['score'] for e in hourly_entries
              if RATING_HOURS[0] <= int(e['datetime'][11:13]) <= RATING_HOURS[1]]
    if not scores:
        rating, color = bucket_for_score(0)
        return rating, color, 0
    day_score = round(mean(scores))
    rating, color = bucket_for_score(day_score)
    return rating, color, day_score


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
    # Rating profile: hand-entered CSV cells win over the generated
    # orientation (spot_orientation.csv: swell window + offshore wind).
    profile = build_profile(spot.profile_meta, coast_normal=spot.coast_normal)
    wave_src = _pick_wave_source(store, spot.slug)
    if wave_src is None:
        print(f"  ! no usable wave model for {spot.name}; skipping")
        return False
    wave_h = _local_series(store, spot.slug, 'wave_height', tz, source=wave_src)
    swell_p = _local_series(store, spot.slug, 'swell_period', tz, source=wave_src)
    swell_d = _local_series(store, spot.slug, 'swell_direction', tz, source=wave_src)
    # Displayed period/direction: the combined MEAN pair, matching the
    # combined height — the summary describes the whole sea; individual
    # trains live in the per-hour `swells` breakdown. Sources without a
    # mean fall back to the dominant train. The RATING never uses the mean
    # (swell_p/swell_d instead): mean period would double-penalize choppy
    # days, since wind is already a scoring factor.
    disp_p = (_local_series(store, spot.slug, 'wave_period', tz, source=wave_src)
              or swell_p)
    disp_d = (_local_series(store, spot.slug, 'wave_direction', tz, source=wave_src)
              or swell_d)
    # Surf headline: the nearshore source's height and peak pair override
    # the display model hour by hour where it has data, for both the row and
    # the rating, so the score judges the swell the card shows. A peak
    # period isn't a mean, so the double-penalty concern above doesn't apply.
    # Where the nearshore source has no data (spots without a transect, the
    # tail past its horizon) the rating falls back to the display model's
    # primary swell (swell_p/swell_d).
    model_h = wave_h
    surf_h, surf_p, surf_d = _surf_series(store, spot.slug, tz)
    surf_h = {dt: v for dt, v in surf_h.items() if dt in wave_h}
    head_src = SURF_SOURCE if surf_h else wave_src
    if surf_h and wave_src != SURF_SOURCE:
        wave_h = {dt: surf_h.get(dt, v) for dt, v in wave_h.items()}
        surf_pd_p = {dt: surf_p[dt] for dt in surf_h if dt in surf_p}
        surf_pd_d = {dt: surf_d[dt] for dt in surf_h if dt in surf_d}
        disp_p = {**disp_p, **surf_pd_p}
        disp_d = {**disp_d, **surf_pd_d}
        swell_p = {**swell_p, **surf_pd_p}
        swell_d = {**swell_d, **surf_pd_d}
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

    # Swell-train breakdown from the display model (primary/secondary/
    # tertiary partitions; wind sea deliberately excluded). Depth follows the
    # model: GFS Wave 3 trains, MFWAM 2, others fewer/none.
    trains = []
    for pre in ('swell_', 'swell2_', 'swell3_'):
        th = _local_series(store, spot.slug, pre + 'height', tz, source=wave_src)
        tp = _local_series(store, spot.slug, pre + 'period', tz, source=wave_src)
        td = _local_series(store, spot.slug, pre + 'direction', tz, source=wave_src)
        if th and tp and td:
            trains.append((th, tp, td))

    # --- hourly files ---
    # Only full days: a trailing day the default model covers partially is
    # dropped rather than shown with missing hours.
    full_day = 24 // HOURLY_STEP_H
    hourly_by_date = {
        d: entries
        for d, entries in _build_hourly(wave_h, swell_p, swell_d,
                                        wind_s, wind_d, profile,
                                        trains=trains, disp_p=disp_p,
                                        disp_d=disp_d).items()
        if len(entries) >= full_day}
    for date_obj, entries in hourly_by_date.items():
        entries.sort(key=lambda e: e['datetime'])
        _write_json(
            os.path.join(out_dir, 'hourly', spot.slug, f'{date_obj.isoformat()}.json'),
            entries)

    # --- daily forecast ---
    today = datetime.now(tz).date()
    by_date = defaultdict(lambda: defaultdict(list))
    for series, key in ((wave_h, 'height'), (disp_p, 'period'), (disp_d, 'sdir'),
                        (wind_s, 'speed'), (wind_d, 'wdir')):
        for dt, v in series.items():
            by_date[dt.date()][key].append(v)

    forecast = []
    for date_obj in sorted(d for d in hourly_by_date
                           if by_date[d]['height'] and d >= today):
        vals = by_date[date_obj]
        rating, color, day_score = _daily_rating(hourly_by_date.get(date_obj, []))
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
            'score': day_score,
        })

    _write_json(os.path.join(out_dir, 'forecast', f'{spot.slug}.json'), {
        'spotName': spot.name,
        'slug': spot.slug,
        'region': spot.region,
        'forecast': forecast,
    })

    # --- model comparison file (combined Hs, 3-hourly, per model) ---
    # Both CDIP products stay in the overlay where available: the ~100 m
    # transect forecast and the 1-km grid are the same model pulled at
    # different resolutions, and seeing them disagree is itself signal.
    models_out = []
    for src, label in {**WAVE_MODELS, **OVERLAY_ONLY}.items():
        # Each line is its source's own series (the bars are the blended
        # headline); the headline source's line is the default one.
        if src == SURF_SOURCE and surf_h:
            series = _surf_series(store, spot.slug, tz, fill=False)[0]
        elif src == wave_src:
            series = model_h
        else:
            series = _local_series(store, spot.slug, 'wave_height', tz, source=src)
        if not _usable(series):
            continue
        # Full native resolution (hourly for the global models, 6-hourly for
        # MOP): the overlay lines interpolate between the 3-hourly bars.
        # Period/direction ride along where the source has them, so the
        # overlay tooltip can echo the bars' height/period/arrow readout.
        # The comparison view prefers the MEAN combined-sea pair — like the
        # heights, one definition across lines (Open-Meteo wave_period/
        # wave_direction, MOP waveTa/waveDm) — falling back to the peak/
        # partition pair for sources without a mean (salix, NWPS).
        src_p = (_local_series(store, spot.slug, 'wave_period', tz, source=src)
                 or _local_series(store, spot.slug, 'swell_period', tz, source=src))
        src_d = (_local_series(store, spot.slug, 'wave_direction', tz, source=src)
                 or _local_series(store, spot.slug, 'swell_direction', tz, source=src))
        points = []
        for dt, v in series.items():
            if dt.minute:
                continue
            pt = {'t': _fmt_dt(dt), 'v': round(v, 2)}
            if dt in src_p:
                pt['p'] = round(src_p[dt], 1)
            if dt in src_d:
                pt['d'] = round(src_d[dt])
            points.append(pt)
        if points:
            models_out.append({
                'id': src.split(':', 1)[1],
                'label': label,
                'default': src == head_src,
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
          f"waves from {WAVE_MODELS[wave_src]}"
          f"{f' (surf from {WAVE_MODELS[head_src]})' if head_src != wave_src else ''}, "
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
            # Preassigned CDIP station for the Buoys view ('' until the
            # first pipeline run after the spot is added).
            'closest_buoy': s.closest_buoy,
        }
        for s in built
    ])
    return built
