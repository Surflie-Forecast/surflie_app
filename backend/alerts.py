"""Swell alarms: evaluate user alerts against the fresh forecast, email matches.

Runs at the end of each full pipeline build (CI passes the secrets; local
runs without them just print what they'd do). Reads alarms from Supabase
with the service key (bypasses RLS), forecast data from this run's outputs:

    spots  data/store.csv          per-model swell vars + GFS wind (UTC)
    buoys  api/buoys/{id}.json     per-model series with v/p/d (local time)

An alarm targets a spot or buoy, selects forecast models, and sets any
subset of conditions (null = ignore):

    min_height_m   combined Hs >= x         all models
    min_period_s   swell period >= x        gfswave016 / mfwam only
    dir_min/max    swell direction in range (wraps 360), same models
    max_wind_kts   wind speed <= x          spots only (GFS)
    wind_dir_*     wind direction in range  spots only

All set conditions must hold at the same forecast hour in the same model.
require_all_models: false = any selected model matching triggers; true =
the hour must match in every selected model (a model that lacks a needed
variable is excluded from the consensus; if none remain, no match).

Dedup: after an email, snooze_until is set to the end of the matched
contiguous window — matches at or before that valid-time stay silent, a
new later window re-alerts.

Env: SUPABASE_URL, SUPABASE_SECRET_KEY, RESEND_API_KEY, optional
ALERTS_FROM (default Resend's shared onboarding sender: only delivers to
the Resend account owner until a domain is verified), ALERTS_DRY_RUN=1
(evaluate + print, send nothing, write nothing back).

Never fails the build: missing secrets or an evaluation error prints and
exits 0 — alerts are best-effort on top of a successful data build.
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DUMP = os.path.join(BASE_DIR, '..', 'data', 'store.csv')
API_DIR = os.path.join(BASE_DIR, '..', 'api')
TZ = ZoneInfo('America/Los_Angeles')

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_SECRET_KEY', '')
RESEND_KEY = os.environ.get('RESEND_API_KEY', '')
ALERTS_FROM = os.environ.get('ALERTS_FROM', 'Surflie <onboarding@resend.dev>')
DRY_RUN = os.environ.get('ALERTS_DRY_RUN') == '1'

# Alarm with empty models[] watches the app's default wave model
DEFAULT_MODEL = 'gfswave016'

WIND_SOURCE = 'openmeteo:gfs'
MODEL_LABELS = {
    'mop': 'CDIP MOP', 'gfswave016': 'GFS Wave 16km', 'mfwam': 'MFWAM 8km',
    'ecmwf_wam9': 'ECMWF WAM 9km',
}

# Models retired 2026-08-03 -> their replacement. Alarms saved before the
# switch still name the old ids in Supabase; without remapping they would
# select a model the artifact no longer carries and silently never fire.
# gfswave025 -> the same NCEP model on the finer 0.16 deg grid;
# ecmwf_wam25 -> the same ECMWF model at 9 km (identical variable set);
# gwam -> MFWAM, the nearest survivor that also serves swell period and
# direction, so period/direction alarms keep working (ECMWF is Hs-only).
RETIRED_MODELS = {
    'gfswave025': 'gfswave016',
    'ecmwf_wam25': 'ecmwf_wam9',
    'gwam': 'mfwam',
}


def _sb(method, path, **kwargs):
    r = requests.request(
        method, f'{SUPABASE_URL}/rest/v1/{path}',
        headers={'apikey': SUPABASE_KEY,
                 'Authorization': f'Bearer {SUPABASE_KEY}',
                 'Content-Type': 'application/json',
                 'Prefer': 'return=minimal'},
        timeout=30, **kwargs)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------------------
# Forecast data -> {model_id: {ts_utc(datetime): {'v','p','d','w','wd'}}}

def _spot_hours(slug):
    """Per-model hourly dicts for a spot from the store dump. Wind (GFS) is
    shared: merged into every model's hours as 'w'/'wd'."""
    per_source = {}
    try:
        with open(STORE_DUMP, newline='') as f:
            for row in csv.DictReader(f):
                if row['spot'] != slug:
                    continue
                ts = datetime.fromisoformat(row['ts_utc'])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                per_source.setdefault(row['source'], {}).setdefault(
                    ts, {})[row['variable']] = float(row['value'])
    except FileNotFoundError:
        print(f"  ! no store dump at {STORE_DUMP}")
        return {}

    wind = per_source.get(WIND_SOURCE, {})
    out = {}
    for source, hours in per_source.items():
        # wave sources: openmeteo:{model} + the CDIP nearshore forecasts
        # (sparser timestamps and shorter horizons — matching hours just
        # come 3/6-hourly instead of hourly). The observed-driven nowcast
        # is NOT a forecast, so it never feeds alerts.
        is_wave = ((source.startswith('openmeteo:') and source != WIND_SOURCE)
                   or source in ('cdip:mop', 'cdip:mop-transect-fc'))
        if not is_wave:
            continue
        model_id = source.split(':', 1)[1]
        model_hours = {}
        for ts, vals in hours.items():
            pt = {}
            if 'wave_height' in vals:
                pt['v'] = vals['wave_height']
            if 'swell_period' in vals:
                pt['p'] = vals['swell_period']
            if 'swell_direction' in vals:
                pt['d'] = vals['swell_direction']
            w = wind.get(ts, {})
            if 'wind_speed' in w:
                pt['w'] = w['wind_speed']
            if 'wind_direction' in w:
                pt['wd'] = w['wind_direction']
            if pt:
                model_hours[ts] = pt
        if model_hours:
            out[model_id] = model_hours
    return out


def _buoy_hours(sid):
    """Per-model hourly dicts for a buoy from its api detail file (local-time
    strings -> aware datetimes). No wind at buoys."""
    try:
        with open(os.path.join(API_DIR, 'buoys', f'{sid}.json')) as f:
            detail = json.load(f)
    except FileNotFoundError:
        print(f"  ! no buoy file for {sid}")
        return {}
    out = {}
    for m in detail.get('models', []):
        hours = {}
        for pt in m['series']:
            ts = datetime.fromisoformat(pt['t']).replace(tzinfo=TZ)
            vals = {'v': pt['v']}
            if 'p' in pt:
                vals['p'] = pt['p']
            if 'd' in pt:
                vals['d'] = pt['d']
            hours[ts] = vals
        if hours:
            out[m['id']] = hours
    return out


# ---------------------------------------------------------------------------
# Evaluation

def _in_range(x, lo, hi):
    """Degree range, wrap-aware: 250..20 means WNW through NNE."""
    if lo <= hi:
        return lo <= x <= hi
    return x >= lo or x <= hi


def _needed_keys(alarm):
    keys = set()
    if alarm.get('min_height_m') is not None:
        keys.add('v')
    if alarm.get('min_period_s') is not None:
        keys.add('p')
    if alarm.get('dir_min') is not None:
        keys.add('d')
    if alarm.get('max_wind_kts') is not None:
        keys.add('w')
    if alarm.get('wind_dir_min') is not None:
        keys.add('wd')
    return keys


def _hour_matches(pt, alarm):
    a = alarm
    if a.get('min_height_m') is not None and pt['v'] < float(a['min_height_m']):
        return False
    if a.get('min_period_s') is not None and pt['p'] < float(a['min_period_s']):
        return False
    if a.get('dir_min') is not None and not _in_range(
            pt['d'], float(a['dir_min']), float(a['dir_max'])):
        return False
    if a.get('max_wind_kts') is not None and pt['w'] > float(a['max_wind_kts']):
        return False
    if a.get('wind_dir_min') is not None and not _in_range(
            pt['wd'], float(a['wind_dir_min']), float(a['wind_dir_max'])):
        return False
    return True


def _matching_hours(model_hours, alarm, after, until):
    """Sorted future hours where every set condition holds. Hours missing a
    needed variable never match; hours past the lead window are ignored."""
    needed = _needed_keys(alarm)
    return sorted(
        ts for ts, pt in model_hours.items()
        if after < ts <= until and needed <= pt.keys()
        and _hour_matches(pt, alarm))


def _window(hours, start):
    """(start, end) of the contiguous hourly run beginning at start."""
    end = start
    hset = set(hours)
    while end + timedelta(hours=1) in hset:
        end += timedelta(hours=1)
    return start, end


def evaluate(alarm, hours_by_model, now):
    """(window_start, window_end, matched_model_ids) or None."""
    selected = alarm.get('models') or [DEFAULT_MODEL]
    # Retired ids map to their replacement, de-duplicated: an alarm that
    # watched both ECMWF resolutions collapses to the surviving 9 km one
    # rather than counting it twice under require_all_models.
    selected = list(dict.fromkeys(RETIRED_MODELS.get(m, m) for m in selected))
    snooze = alarm.get('snooze_until')
    after = now
    if snooze:
        s = datetime.fromisoformat(snooze.replace('Z', '+00:00'))
        after = max(after, s)
    # lead window: only alert on matches starting within lead_days of now
    until = (now + timedelta(days=int(alarm['lead_days']))
             if alarm.get('lead_days') else datetime.max.replace(tzinfo=timezone.utc))

    needed = _needed_keys(alarm)
    per_model = {}
    for mid in selected:
        model_hours = hours_by_model.get(mid)
        if not model_hours:
            continue
        # a model that never serves a needed variable is excluded (ECMWF WAM
        # and MOP are Hs-only) rather than silently blocking a consensus
        served = set().union(*(pt.keys() for pt in model_hours.values()))
        if not needed <= served:
            continue
        per_model[mid] = _matching_hours(model_hours, alarm, after, until)

    if not per_model:
        return None

    if alarm.get('require_all_models'):
        common = set(per_model[next(iter(per_model))])
        for hours in per_model.values():
            common &= set(hours)
        if not common:
            return None
        hours = sorted(common)
        start, end = _window(hours, hours[0])
        return start, end, sorted(per_model)

    # any: earliest match across models; window from the model that matched
    best = None
    for mid, hours in per_model.items():
        if hours and (best is None or hours[0] < best[1][0]):
            best = (mid, hours)
    if best is None:
        return None
    start, end = _window(best[1], best[1][0])
    return start, end, [best[0]]


# ---------------------------------------------------------------------------
# Email

def _fmt(ts):
    return ts.astimezone(TZ).strftime('%a %b %-d, %-H:%M')


M_PER_FT = 0.3048


def _conditions_text(alarm, imperial=True):
    parts = []
    if alarm.get('min_height_m') is not None:
        h = float(alarm['min_height_m'])
        parts.append(f"waves ≥ {round(h / M_PER_FT, 1)} ft" if imperial
                     else f"waves ≥ {h} m")
    if alarm.get('min_period_s') is not None:
        parts.append(f"period ≥ {alarm['min_period_s']} s")
    if alarm.get('dir_min') is not None:
        parts.append(f"swell {alarm['dir_min']}–{alarm['dir_max']}°")
    if alarm.get('max_wind_kts') is not None:
        parts.append(f"wind ≤ {alarm['max_wind_kts']} kts")
    if alarm.get('wind_dir_min') is not None:
        parts.append(f"wind dir {alarm['wind_dir_min']}–{alarm['wind_dir_max']}°")
    if alarm.get('lead_days'):
        parts.append(f"within {alarm['lead_days']} days")
    return ', '.join(parts)


def _send_email(to, label, alarm, start, end, models, site_url, slug_hash,
                imperial=True):
    model_names = ', '.join(MODEL_LABELS.get(m, m) for m in models)
    mode = 'all of' if alarm.get('require_all_models') else ''
    subject = f"Surf alert: {label} — {_fmt(start)}"
    day_link = f"{site_url}/#{slug_hash}" if slug_hash else site_url
    html = f"""
    <div style="font-family:sans-serif;max-width:520px">
      <h2 style="color:#131929">🌊 {label}</h2>
      <p>Your alert conditions are in the forecast:</p>
      <p style="font-size:1.1em"><b>{_fmt(start)} → {_fmt(end)}</b>
         <span style="color:#64748b">(Pacific)</span></p>
      <p>Conditions: <b>{_conditions_text(alarm, imperial)}</b><br>
         Forecast by: {mode} {model_names}</p>
      <p><a href="{day_link}"
            style="color:#0077be">Check the full forecast on Surflie →</a></p>
      <p style="color:#94a3b8;font-size:0.85em">You get one email per swell
         window — this alert re-arms after {_fmt(end)}.</p>
    </div>"""
    r = requests.post(
        'https://api.resend.com/emails',
        headers={'Authorization': f'Bearer {RESEND_KEY}'},
        json={'from': ALERTS_FROM, 'to': [to],
              'subject': subject, 'html': html},
        timeout=30)
    r.raise_for_status()


# ---------------------------------------------------------------------------

def run():
    if not (SUPABASE_URL and SUPABASE_KEY):
        print('alerts: SUPABASE_URL / SUPABASE_SECRET_KEY not set — skipping')
        return
    if not RESEND_KEY and not DRY_RUN:
        print('alerts: RESEND_API_KEY not set — skipping')
        return

    alarms = _sb('GET', 'alarms?enabled=eq.true&select=*').json()
    if not alarms:
        print('alerts: no enabled alarms')
        return

    user_ids = ','.join(sorted({a['user_id'] for a in alarms}))
    profiles = {p['id']: p for p in _sb(
        'GET', f'profiles?id=in.({user_ids})&select=id,email,units').json()}

    # Display labels + deep links
    site_url = os.environ.get('SITE_URL', 'https://pcarbomestre.github.io/surflie')
    spot_names = {}
    try:
        with open(os.path.join(API_DIR, 'spots.json')) as f:
            spot_names = {s['slug']: s['name'] for s in json.load(f)}
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    hours_cache = {}
    sent = evaluated = 0
    for alarm in alarms:
        key = (alarm['kind'], alarm['item_id'])
        if key not in hours_cache:
            hours_cache[key] = (_spot_hours if alarm['kind'] == 'spot'
                                else _buoy_hours)(alarm['item_id'])
        result = evaluate(alarm, hours_cache[key], now)
        evaluated += 1
        if not result:
            continue
        start, end, models = result

        prof = profiles.get(alarm['user_id']) or {}
        email = prof.get('email')
        imperial = prof.get('units', 'imperial') != 'metric'
        if alarm['kind'] == 'spot':
            label = spot_names.get(alarm['item_id'], alarm['item_id'])
            slug_hash = alarm['item_id']
        else:
            label = f"CDIP {alarm['item_id']}"
            slug_hash = None
        print(f"  ✓ alarm {alarm['id'][:8]} ({label}): match "
              f"{_fmt(start)} → {_fmt(end)} via {','.join(models)}"
              f"{' [dry-run]' if DRY_RUN else ''}")
        if DRY_RUN:
            continue
        if not email:
            print(f"    ! no email for user {alarm['user_id'][:8]} — skipped")
            continue
        try:
            _send_email(email, label, alarm, start, end, models,
                        site_url, slug_hash, imperial)
        except Exception as e:
            print(f"    ! send failed: {e}")
            continue
        _sb('PATCH', f"alarms?id=eq.{alarm['id']}", json={
            'snooze_until': end.astimezone(timezone.utc).isoformat(),
            'last_notified_at': now.isoformat(),
        })
        sent += 1

    print(f"alerts: {evaluated} alarms evaluated, {sent} emails sent")


if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        # Best-effort: an alerts failure must never sink a good data build
        print(f"alerts: failed ({e}) — continuing")
        sys.exit(0)
