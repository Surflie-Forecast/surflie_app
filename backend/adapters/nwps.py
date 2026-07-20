"""NOAA NWPS nearshore SWAN nests (per-WFO CG1 grids off NOMADS).

The Nearshore Wave Prediction System runs a SWAN nest at each coastal
Weather Forecast Office, forced by WW3 boundary spectra and the
forecaster-edited wind grids — an independent nearshore product from both
CDIP MOP (same WW3 lineage, different transform) and our salix pipeline.
Hourly bulk parameters out to ~144 h, on-demand cycles (~2-3 per day per
site).

One CG1 GRIB2 file covers a whole WFO domain (~24-64 MB), so this is a
batch adapter: spots are grouped by the SITES box containing them, each
needed site's latest complete cycle is downloaded once, and every spot
samples its nearest wet cell. A per-site cycle stamp
(data/mop_cache/nwps_state.json) skips the download when no new cycle has
been published since the last successful fetch.

Each cycle is a pure forward forecast, so points merge with a small
per-spot cache (data/mop_cache/<slug>_nwps.json) to keep the overlay's
first day full — same scheme as cdip_mop_transect_fc.

Non-critical: spots outside every site box yield no rows, and a missing
pygrib, a NOMADS failure, or a partial/corrupt file serves the caches.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

import requests

from . import _cdip_transect as tr

try:
    import numpy as np
    import pygrib
except ImportError:
    pygrib = None

NAME = 'nwps'
CRITICAL = False
SOURCE = 'nwps:cg1'

BASE = 'https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod'

# WFO site -> (NCEP region dir, CG1 outer-grid bounds as
# (lat_min, lat_max, lon_min, lon_max), read from the live grids). A spot in
# several boxes (the domains overlap at WFO borders) takes the site whose
# grid center it sits closest to. Add coastal WFOs here as spots expand.
SITES = {
    'mtr': ('wr', (35.00, 39.40, -125.80, -120.74)),  # SF Bay/Monterey
    'lox': ('wr', (32.60, 36.10, -123.36, -117.30)),  # LA/Oxnard
}

# GRIB shortName -> cache point key; tr.rows turns h/p/d into
# wave_height (combined Hs) / swell_period / swell_direction rows.
GRIB_VARS = {'swh': 'h', 'perpw': 'p', 'dirpw': 'd'}

MAX_CELL_KM = 10.0     # give up on a spot with no wet cell nearer than this
STATE_FILE = os.path.join(tr.CACHE_DIR, 'nwps_state.json')


def _cache_file(spot):
    return os.path.join(tr.CACHE_DIR, f'{spot.slug}_nwps.json')


def _site_for(spot):
    best = None
    for site, (_, (la0, la1, lo0, lo1)) in SITES.items():
        if la0 <= spot.latitude <= la1 and lo0 <= spot.longitude <= lo1:
            d2 = ((spot.latitude - (la0 + la1) / 2) ** 2
                  + (spot.longitude - (lo0 + lo1) / 2) ** 2)
            if best is None or d2 < best[1]:
                best = (site, d2)
    return best[0] if best else None


def _listing(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def _latest_cycle(site):
    """(cycle id 'YYYYMMDD_HH', grib url) of the newest published run.

    A cycle counts only once its CG1 file appears in the listing; scans
    today then yesterday (UTC) and raises if neither day has one.
    """
    region = SITES[site][0]
    now = datetime.now(timezone.utc)
    for day in (now, now - timedelta(days=1)):
        stamp = day.strftime('%Y%m%d')
        try:
            hours = re.findall(r'href="(\d{2})/"',
                               _listing(f'{BASE}/{region}.{stamp}/{site}/'))
        except requests.RequestException:
            continue
        fname_fmt = f'{site}_nwps_CG1_{stamp}_{{hh}}00.grib2'
        for hh in sorted(set(hours), reverse=True):
            url = f'{BASE}/{region}.{stamp}/{site}/{hh}/CG1/'
            if fname_fmt.format(hh=hh) in _listing(url):
                return f'{stamp}_{hh}', url + fname_fmt.format(hh=hh)
    raise RuntimeError(f'no published CG1 cycle found for {site}')


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def _site_points(url, site_spots, start_utc, end_utc):
    """{slug: {ts_utc_iso: {'h','p','d'}}} sampled at nearest wet cells."""
    with tempfile.NamedTemporaryFile(suffix='.grib2') as tmp:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.flush()

        grbs = pygrib.open(tmp.name)
        try:
            first = grbs.select(shortName='swh')[0]
            lats, lons = first.latlons()
            lons = np.where(lons > 180, lons - 360, lons)
            wet = ~np.ma.getmaskarray(first.values)

            cells = {}
            for spot in site_spots:
                d2 = ((lats - spot.latitude) ** 2
                      + ((lons - spot.longitude)
                         * np.cos(np.radians(spot.latitude))) ** 2)
                d2 = np.where(wet, d2, np.inf)
                j, i = np.unravel_index(np.argmin(d2), d2.shape)
                if np.sqrt(d2[j, i]) * 111.32 > MAX_CELL_KM:
                    print(f"    {NAME}: no wet cell within {MAX_CELL_KM} km "
                          f"of {spot.slug}")
                    continue
                cells[spot.slug] = (j, i)

            points = {slug: {} for slug in cells}
            grbs.seek(0)
            for m in grbs:
                key = GRIB_VARS.get(m.shortName)
                if key is None:
                    continue
                ts = m.validDate.replace(tzinfo=timezone.utc)
                if not (start_utc <= ts <= end_utc):
                    continue
                vals = m.values
                for slug, (j, i) in cells.items():
                    if not np.ma.is_masked(vals[j, i]):
                        points[slug].setdefault(
                            ts.isoformat(), {})[key] = float(vals[j, i])
            return points
        finally:
            grbs.close()


def fetch_batch(spots, start_utc, end_utc):
    site_spots = {}
    for spot in spots:
        site = _site_for(spot)
        if site is None:
            print(f"    {NAME}: {spot.slug} outside every NWPS site box")
        else:
            site_spots.setdefault(site, []).append(spot)

    state = _load_state()
    rows = []
    for site, sp_list in site_spots.items():
        fresh = None  # None = serve caches unmerged
        if pygrib is None:
            print(f"  {NAME}: pygrib not installed; serving caches")
        else:
            try:
                cycle, url = _latest_cycle(site)
                if state.get(site) == cycle:
                    print(f"  {NAME}: {site} cycle {cycle} unchanged; "
                          f"serving caches")
                else:
                    fresh = _site_points(url, sp_list, start_utc, end_utc)
                    state[site] = cycle
                    _save_state(state)
            except Exception as e:
                print(f"  {NAME}: {site} fetch failed ({e}); serving caches")

        for spot in sp_list:
            merged = tr.load_cache(_cache_file(spot), start_utc, end_utc)
            if fresh is not None:
                merged.update(fresh.get(spot.slug, {}))
                if merged:
                    tr.save_cache(_cache_file(spot), merged)
            rows.extend(tr.rows(spot, SOURCE, merged))
    return rows
