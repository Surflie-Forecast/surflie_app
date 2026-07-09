import xarray as xr
import pandas as pd
import datetime
import math
import csv
import os
import pytz
import time
import tempfile
import requests

# --- NOAA GFS via the NOMADS GRIB-filter service -------------------------------
# The legacy OpenDAP/DODS endpoint (nomads.ncep.noaa.gov/dods/...) was retired by
# NOAA (see Service Change Notice 25-81). The GRIB-filter service serves the same
# GFS 0.25-degree data, but one forecast hour per file. We request a tiny spatial
# subset (UGRD/VGRD at 10 m) per forecast hour, decode it with cfgrib, and extract
# the nearest grid point for each spot.
GRIB_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

# GFS 0.25 forecast-hour cadence: hourly through f120, then 3-hourly.
# 240h = 10 days of coverage.
FORECAST_HOURS = list(range(0, 121)) + list(range(123, 241, 3))

# Process-lifetime cache of decoded GRIB subsets keyed by (date_str, cycle, fxx),
# so that fetching multiple spots does not re-download the same forecast hours.
_GRIB_CACHE = {}


def _select_cycle(now_utc):
    """Pick the latest GFS cycle that guarantees full coverage of today's LA day.

    Identical logic to the original OpenDAP implementation: we avoid 12z/18z so
    the run starts early enough (<= 06z UTC) to cover the LA morning.
    """
    date_str = now_utc.strftime('%Y%m%d')
    hour = now_utc.hour

    if hour < 8:
        # 00:00 - 08:00 UTC. In LA this is still the previous day.
        # Safe bet: previous day's 06z.
        cycle = '06'
        date_str = (now_utc - datetime.timedelta(days=1)).strftime('%Y%m%d')
    elif hour < 11:
        # 08:00 - 11:00 UTC. Today's 06z not published yet (~10:30 UTC); use 00z.
        cycle = '00'
    else:
        # 11:00+ UTC. 06z is available.
        cycle = '06'

    return date_str, cycle


def _fetch_grib_subset(date_str, cycle, fxx, bbox):
    """Download one forecast-hour GRIB subset and return it as an xarray Dataset.

    Cached per (date_str, cycle, fxx). Returns None on failure.
    bbox is (toplat, bottomlat, leftlon, rightlon) in -180..180 lon.
    """
    key = (date_str, cycle, fxx)
    if key in _GRIB_CACHE:
        return _GRIB_CACHE[key]

    toplat, bottomlat, leftlon, rightlon = bbox
    params = {
        "dir": f"/gfs.{date_str}/{cycle}/atmos",
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{fxx:03d}",
        "var_UGRD": "on",
        "var_VGRD": "on",
        "lev_10_m_above_ground": "on",
        "subregion": "on",
        "toplat": toplat,
        "bottomlat": bottomlat,
        "leftlon": leftlon,
        "rightlon": rightlon,
    }

    ds = None
    for attempt in range(3):
        try:
            r = requests.get(GRIB_FILTER_URL, params=params, timeout=60)
            if r.status_code == 200 and r.content[:4] == b'GRIB':
                # cfgrib needs a real file on disk.
                tmp = tempfile.NamedTemporaryFile(suffix='.grib2', delete=False)
                try:
                    tmp.write(r.content)
                    tmp.close()
                    ds = xr.open_dataset(tmp.name, engine='cfgrib').load()
                finally:
                    for p in (tmp.name, tmp.name + '.idx'):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                break
            else:
                # 404 means that forecast hour isn't published yet; don't retry hard.
                if r.status_code == 404:
                    break
                time.sleep(3)
        except Exception as e:
            print(f"    [f{fxx:03d} attempt {attempt+1}] {e}. Retrying in 3s...")
            time.sleep(3)

    _GRIB_CACHE[key] = ds
    return ds


def fetch_wind_data(lat, lon, name, output_csv_path):
    # --- 1. TIMEZONE / WINDOW SETUP ---
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=10)

    print(f"Fetching wind data for {name} ({lat}, {lon})...")
    print(f"Target Window (LA): {start_of_today_la} to {end_of_window_la}")

    # --- 2. DETERMINE NOAA GFS CYCLE ---
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_str, cycle = _select_cycle(now_utc)
    print(f"Connecting to NOAA GRIB filter: gfs.{date_str}/{cycle}z")

    # Small bounding box around the point (GRIB filter uses -180..180 lon).
    bbox = (lat + 0.5, lat - 0.5, lon - 0.5, lon + 0.5)
    target_lon_360 = 360 + lon if lon < 0 else lon

    # --- 3. FETCH EACH FORECAST HOUR & EXTRACT NEAREST POINT ---
    results = []
    misses = 0
    for fxx in FORECAST_HOURS:
        ds = _fetch_grib_subset(date_str, cycle, fxx, bbox)
        if ds is None:
            misses += 1
            # Once we run off the end of the published run, stop early.
            if misses > 3:
                break
            continue
        misses = 0

        try:
            pt = ds.sel(latitude=lat, longitude=target_lon_360, method='nearest')
            u = float(pt['u10'].values)
            v = float(pt['v10'].values)
        except Exception as e:
            print(f"    [f{fxx:03d}] extraction failed: {e}")
            continue

        if math.isnan(u) or math.isnan(v):
            continue

        # A. Speed (m/s)
        speed_ms = math.hypot(u, v)

        # B. Direction (meteorological convention: direction wind blows FROM)
        direction = (math.degrees(math.atan2(u, v)) + 180) % 360

        # C. Time: GRIB valid_time is UTC -> LA local
        valid = pd.Timestamp(ds['valid_time'].values).to_pydatetime()
        dt_utc = valid.replace(tzinfo=datetime.timezone.utc)
        dt_la = dt_utc.astimezone(la_tz)

        # Strict filtering: only today's midnight LA through end of window.
        if start_of_today_la <= dt_la <= end_of_window_la:
            results.append({
                'spot_name': name,
                'datetime': dt_la.strftime('%Y-%m-%d %H:%M:%S'),
                'wind_speed': round(speed_ms * 1.94384, 2),  # m/s -> knots
                'wind_direction': int(round(direction)),
            })

    results.sort(key=lambda x: x['datetime'])

    # --- 4. SAVE (append mode, same contract as before) ---
    if not results:
        print("No data found matching time window.")
        return

    headers = ['spot_name', 'datetime', 'wind_speed', 'wind_direction']
    file_exists = os.path.isfile(output_csv_path)

    try:
        with open(output_csv_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerows(results)

        print(f"Saved {len(results)} rows. (First: {results[0]['datetime']})")

    except Exception as e:
        print(f"Error writing to CSV: {e}")


if __name__ == "__main__":
    fetch_wind_data(34.27, -119.29, "Ventura", "wind_forecast.csv")
