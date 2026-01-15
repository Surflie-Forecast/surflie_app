#!/usr/bin/env python3
"""
Read points from data/locations.csv and write wind forecasts (speed + direction)
for multiple models to data/.

Input CSV (data/locations.csv) columns:
  site_name,region,latitude,longitude

Output CSV:
  data/wind_points_out_<RUNUTC_YYYYmmddTHHMMSSZ>.csv

Time logic (WHAT YOU ASKED FOR):
- Let RUN_TIME = current UTC time when script executes.
- For each site:
  - Determine local timezone from lat/lon.
  - Build a list of VALID TIMES every 6 hours in LOCAL TIME:
      from local 00:00 of the RUN_TIME local date
      through the maximum "forecast horizon end" available at download time,
      using the latest cycle available at RUN_TIME for each model.
- For each (site, valid_time_local_6h), convert to UTC and fetch each model wind.

Nearest pixel:
- Always uses nearest gridpoint (and reports grid_lat/grid_lon + distance_km).

Install (in your .venv):
  pip install pandas numpy requests beautifulsoup4 timezonefinder herbie-data xarray cfgrib eccodes
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from timezonefinder import TimezoneFinder

from herbie import Herbie


# -------------------------
# Utilities
# -------------------------

def wind_speed_dir_from_uv(u: float, v: float) -> Tuple[float, float]:
    """Return (speed_mps, direction_deg_from) from u/v in m/s (meteorological 'from')."""
    speed = float(math.hypot(u, v))
    direction = (180.0 + math.degrees(math.atan2(u, v))) % 360.0
    return speed, direction


def mps_to_knots(x: float) -> float:
    return float(x) * 1.9438444924406


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_tz_name(lat: float, lon: float) -> str:
    tf = TimezoneFinder()
    tz = tf.timezone_at(lat=lat, lng=lon)
    return tz or "UTC"


def to_local(dt_utc: datetime, tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        return dt_utc


def to_utc(dt_local: datetime, tz_name: str) -> datetime:
    """Ensure local dt is tz-aware and return UTC."""
    try:
        from zoneinfo import ZoneInfo
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=ZoneInfo(tz_name))
        return dt_local.astimezone(timezone.utc)
    except Exception:
        return dt_local.astimezone(timezone.utc)


def floor_dt_to_interval(dt_local: datetime, hours: int) -> datetime:
    """Floor a tz-aware local datetime to previous 'hours' boundary."""
    h = (dt_local.hour // hours) * hours
    return dt_local.replace(hour=h, minute=0, second=0, microsecond=0)


def floor_to_cycle(dt_utc: datetime, cycle_hours: int) -> datetime:
    """Floor UTC time to most recent cycle boundary."""
    dt_utc = dt_utc.astimezone(timezone.utc)
    floored_hour = (dt_utc.hour // cycle_hours) * cycle_hours
    return dt_utc.replace(hour=floored_hour, minute=0, second=0, microsecond=0)


def choose_init_fxx_available_at_download(
    run_time_utc: datetime,
    valid_time_utc: datetime,
    cycle_hours: int,
) -> Tuple[datetime, int]:
    """
    Choose an init_time_utc such that:
      - init_time_utc <= run_time_utc   (available at download time)
      - init_time_utc <= valid_time_utc (so fxx >= 0)
    Then compute fxx hours = valid - init.

    We start from the latest cycle available at run_time_utc, then step back
    by cycle_hours until init <= valid_time_utc.
    """
    init = floor_to_cycle(run_time_utc, cycle_hours)
    while init > valid_time_utc:
        init = init - timedelta(hours=cycle_hours)

    fxx = int(round((valid_time_utc - init).total_seconds() / 3600.0))
    if fxx < 0:
        # safety
        while fxx < 0:
            init = init - timedelta(hours=cycle_hours)
            fxx = int(round((valid_time_utc - init).total_seconds() / 3600.0))
    return init, fxx


def nearest_value_from_xarray(ds, var_name: str, lat: float, lon: float) -> Tuple[float, float, float]:
    """Return (value, nearest_lat, nearest_lon) using nearest grid point."""
    da = ds[var_name]

    if "latitude" in da.coords and "longitude" in da.coords:
        latc = da.coords["latitude"]
        lonc = da.coords["longitude"]

        if latc.ndim == 1 and lonc.ndim == 1:
            pick = da.sel(latitude=lat, longitude=lon, method="nearest")
            return float(pick.values), float(pick["latitude"].values), float(pick["longitude"].values)

        if latc.ndim == 2 and lonc.ndim == 2:
            lat2 = latc.values
            lon2 = lonc.values
            mask = (np.abs(lat2 - lat) <= 2.0) & (np.abs(((lon2 - lon + 180) % 360) - 180) <= 2.0)
            if not np.any(mask):
                mask = np.ones_like(lat2, dtype=bool)

            idx = np.argwhere(mask)
            lat_sub = lat2[mask]
            lon_sub = lon2[mask]
            d = np.array([haversine_km(lat, lon, float(la), float(lo)) for la, lo in zip(lat_sub, lon_sub)])
            j = int(np.argmin(d))
            iy, ix = idx[j]
            return float(da.values[iy, ix]), float(lat2[iy, ix]), float(lon2[iy, ix])

    if "latitude" in ds and "longitude" in ds:
        latv = ds["latitude"].values.ravel()
        lonv = ds["longitude"].values.ravel()
        vals = da.values.ravel()

        dlon = ((lonv - lon + 180) % 360) - 180
        m = (np.abs(latv - lat) <= 2.0) & (np.abs(dlon) <= 2.0)
        if not np.any(m):
            m = np.ones_like(latv, dtype=bool)

        lat_sub = latv[m]
        lon_sub = lonv[m]
        val_sub = vals[m]

        d = np.array([haversine_km(lat, lon, float(la), float(lo)) for la, lo in zip(lat_sub, lon_sub)])
        j = int(np.argmin(d))
        return float(val_sub[j]), float(lat_sub[j]), float(lon_sub[j])

    raise ValueError("Could not find recognizable lat/lon coordinates in dataset.")


# -------------------------
# Model specs
# -------------------------

@dataclass
class HerbieModelSpec:
    name: str
    herbie_model: str
    product: Optional[str]
    cycle_hours: int
    max_fxx_hours: int  # conservative horizon for "available onward"


HERBIE_MODELS: Dict[str, HerbieModelSpec] = {
    # Conservative horizons; tweak if you want longer/shorter.
    "gfs":     HerbieModelSpec("GFS 0.25°",              "gfs",     "pgrb2.0p25",          6, 384),
    "hrrr":    HerbieModelSpec("HRRR CONUS",             "hrrr",    "sfc",                 1,  18),
    "hrrr-ak": HerbieModelSpec("HRRR Alaska",            "hrrrak",  "sfc",                 1,  18),
    "nam":     HerbieModelSpec("NAM CONUS nest",         "nam",     "conusnest",           6,  60),
    "nam-ak":  HerbieModelSpec("NAM Alaska nest",        "nam",     "alaskanest",          6,  60),
    "hrdps":   HerbieModelSpec("HRDPS 2.5km",            "hrdps",   "continental/2.5km",   6,  48),
    "ecmwf":   HerbieModelSpec("ECMWF IFS (open data)",  "ecmwf",   "oper",                6, 240),
}

DEFAULT_MODELS = ["gfs", "hrrr", "hrrr-ak", "nam", "nam-ak", "hrdps", "ecmwf", "icon"]


# -------------------------
# Herbie fetcher
# -------------------------

def fetch_uv_herbie(
    model_key: str,
    run_time_utc: datetime,
    valid_time_utc: datetime,
    lat: float,
    lon: float
) -> Dict[str, Any]:
    spec = HERBIE_MODELS[model_key]
    init, fxx = choose_init_fxx_available_at_download(run_time_utc, valid_time_utc, spec.cycle_hours)

    H = Herbie(init, model=spec.herbie_model, product=spec.product, fxx=fxx)

    ds_u = H.xarray(searchString=":UGRD:10 m above ground:")
    ds_v = H.xarray(searchString=":VGRD:10 m above ground:")

    u_var = list(ds_u.data_vars)[0]
    v_var = list(ds_v.data_vars)[0]

    u10, glat, glon = nearest_value_from_xarray(ds_u, u_var, lat, lon)
    v10, _, _ = nearest_value_from_xarray(ds_v, v_var, lat, lon)

    speed_mps, dir_from = wind_speed_dir_from_uv(u10, v10)

    return {
        "status": "ok",
        "model": spec.name,
        "model_key": model_key,
        "init_time_utc": init,
        "fxx_hours": fxx,
        "valid_time_utc": valid_time_utc,
        "u10_mps": u10,
        "v10_mps": v10,
        "wind_speed_mps": speed_mps,
        "wind_speed_kt": mps_to_knots(speed_mps),
        "wind_dir_deg_from": dir_from,
        "grid_lat": glat,
        "grid_lon": glon,
        "distance_km": haversine_km(lat, lon, glat, glon),
        "source": str(H.grib),
    }


# -------------------------
# ICON (DWD Open Data)
# -------------------------

DWD_ICON_BASE = "https://opendata.dwd.de/weather/nwp/icon/grib"

@dataclass
class IconSpec:
    name: str = "ICON global"
    cycle_hours: int = 6
    max_fxx_hours: int = 180  # conservative

ICON_SPEC = IconSpec()


def _dwd_list_files(url: str) -> List[str]:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.find_all("a"):
        href = a.get("href")
        if href and (href.endswith(".grib2") or href.endswith(".grib2.bz2")):
            out.append(href)
    return out


def _pick_icon_file(files: List[str], init_yyyymmddhh: str, fxx: int, var_tag: str) -> Optional[str]:
    lead = f"{fxx:03d}"
    pat = re.compile(rf".*_{init_yyyymmddhh}.*_{lead}.*_{var_tag}\.grib2(\.bz2)?$", re.IGNORECASE)
    matches = [f for f in files if pat.match(f)]
    if matches:
        return sorted(matches)[0]
    loose = [f for f in files if (init_yyyymmddhh in f and var_tag in f and lead in f)]
    return sorted(loose)[0] if loose else None


def fetch_uv_icon(run_time_utc: datetime, valid_time_utc: datetime, lat: float, lon: float) -> Dict[str, Any]:
    init, fxx = choose_init_fxx_available_at_download(run_time_utc, valid_time_utc, ICON_SPEC.cycle_hours)

    cycle = f"{init.hour:02d}"
    init_tag = init.strftime("%Y%m%d%H")

    u_dir = f"{DWD_ICON_BASE}/{cycle}/u_10m/"
    v_dir = f"{DWD_ICON_BASE}/{cycle}/v_10m/"

    files_u = _dwd_list_files(u_dir)
    files_v = _dwd_list_files(v_dir)

    u_file = _pick_icon_file(files_u, init_tag, fxx, "U_10M")
    v_file = _pick_icon_file(files_v, init_tag, fxx, "V_10M")

    if not u_file or not v_file:
        return {
            "status": "error",
            "model": ICON_SPEC.name,
            "model_key": "icon",
            "init_time_utc": init,
            "fxx_hours": fxx,
            "valid_time_utc": valid_time_utc,
            "error": f"Could not find ICON files for init={init_tag}, fxx={fxx}.",
        }

    u_url = u_dir + u_file
    v_url = v_dir + v_file

    import tempfile
    import xarray as xr

    def _download(url: str, outpath: str) -> str:
        rr = requests.get(url, stream=True, timeout=120)
        rr.raise_for_status()
        with open(outpath, "wb") as f:
            for chunk in rr.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return outpath

    def _maybe_decompress(path: str, url: str) -> str:
        if url.endswith(".bz2"):
            import bz2, shutil
            raw = path + ".bz2"
            shutil.move(path, raw)
            out = path
            with bz2.open(raw, "rb") as fin, open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            return out
        return path

    with tempfile.TemporaryDirectory() as td:
        u_path = f"{td}/u.grib2"
        v_path = f"{td}/v.grib2"

        _download(u_url, u_path)
        _download(v_url, v_path)
        u_path = _maybe_decompress(u_path, u_url)
        v_path = _maybe_decompress(v_path, v_url)

        ds_u = xr.open_dataset(u_path, engine="cfgrib")
        ds_v = xr.open_dataset(v_path, engine="cfgrib")

        u_var = list(ds_u.data_vars)[0]
        v_var = list(ds_v.data_vars)[0]

        u10, glat, glon = nearest_value_from_xarray(ds_u, u_var, lat, lon)
        v10, _, _ = nearest_value_from_xarray(ds_v, v_var, lat, lon)

    speed_mps, dir_from = wind_speed_dir_from_uv(u10, v10)

    return {
        "status": "ok",
        "model": ICON_SPEC.name,
        "model_key": "icon",
        "init_time_utc": init,
        "fxx_hours": fxx,
        "valid_time_utc": valid_time_utc,
        "u10_mps": u10,
        "v10_mps": v10,
        "wind_speed_mps": speed_mps,
        "wind_speed_kt": mps_to_knots(speed_mps),
        "wind_dir_deg_from": dir_from,
        "grid_lat": glat,
        "grid_lon": glon,
        "distance_km": haversine_km(lat, lon, glat, glon),
        "source": f"{u_url} ; {v_url}",
    }


# -------------------------
# Build the LOCAL 6h time grid: today 00:00 -> max horizon end
# -------------------------

def compute_horizon_end_utc(run_time_utc: datetime) -> datetime:
    """
    Compute the maximum horizon end (UTC) across all models based on the latest
    cycle available at run_time_utc for each model.
    """
    ends = []
    for spec in HERBIE_MODELS.values():
        init_latest = floor_to_cycle(run_time_utc, spec.cycle_hours)
        ends.append(init_latest + timedelta(hours=spec.max_fxx_hours))

    init_icon = floor_to_cycle(run_time_utc, ICON_SPEC.cycle_hours)
    ends.append(init_icon + timedelta(hours=ICON_SPEC.max_fxx_hours))

    return max(ends)


def build_local_6h_grid_today_to_horizon(
    run_time_utc: datetime,
    lat: float,
    lon: float
) -> Tuple[str, datetime, datetime, List[datetime]]:
    """
    For a site, build:
      - tz_name
      - run_time_local
      - horizon_end_local (converted from max horizon end UTC)
      - list of local valid times every 6 hours from local 00:00 today
        through horizon_end_local (floored to 6h)
    """
    tz_name = get_tz_name(lat, lon)
    run_local = to_local(run_time_utc, tz_name)

    start_local = run_local.replace(hour=0, minute=0, second=0, microsecond=0)

    horizon_end_utc = compute_horizon_end_utc(run_time_utc)
    horizon_end_local = to_local(horizon_end_utc, tz_name)
    horizon_end_local = floor_dt_to_interval(horizon_end_local, 6)

    slots = []
    t = start_local
    while t <= horizon_end_local:
        slots.append(t)
        t = t + timedelta(hours=6)

    return tz_name, run_local, horizon_end_local, slots


# -------------------------
# Orchestration
# -------------------------

def run(points_df: pd.DataFrame, run_time_utc: datetime, model_keys: List[str]) -> pd.DataFrame:
    required = {"site_name", "region", "latitude", "longitude"}
    missing = required - set(points_df.columns)
    if missing:
        raise ValueError(f"Input table missing columns: {sorted(missing)}")

    rows: List[Dict[str, Any]] = []

    for _, r in points_df.iterrows():
        lat = float(r["latitude"])
        lon = float(r["longitude"])
        site = str(r["site_name"])
        region = str(r["region"])

        tz_name, run_local, horizon_end_local, local_slots = build_local_6h_grid_today_to_horizon(
            run_time_utc, lat, lon
        )

        for valid_local in local_slots:
            valid_utc = to_utc(valid_local, tz_name)

            for mk in model_keys:
                try:
                    if mk == "icon":
                        out = fetch_uv_icon(run_time_utc, valid_utc, lat, lon)
                    else:
                        out = fetch_uv_herbie(mk, run_time_utc, valid_utc, lat, lon)
                except Exception as e:
                    out = {
                        "status": "error",
                        "model": mk,
                        "model_key": mk,
                        "init_time_utc": None,
                        "fxx_hours": None,
                        "valid_time_utc": valid_utc,
                        "error": repr(e),
                    }

                def _ser(v):
                    return v.isoformat() if isinstance(v, datetime) else v

                rows.append({
                    "site_name": site,
                    "region": region,
                    "latitude": lat,
                    "longitude": lon,
                    "tz_name": tz_name,

                    "run_time_utc": run_time_utc.isoformat(),
                    "run_time_local": run_local.isoformat(),

                    "horizon_end_local_6h": horizon_end_local.isoformat(),

                    "valid_time_local_6h": valid_local.isoformat(),
                    "valid_time_utc": valid_utc.isoformat(),

                    **{k: _ser(v) for k, v in out.items()},
                })

    return pd.DataFrame(rows)


def main():
    data_dir = Path("data")
    in_path = data_dir / "locations.csv"
    if not in_path.exists():
        raise FileNotFoundError(f"Could not find {in_path}")

    points = pd.read_csv(in_path)

    run_time_utc = datetime.now(timezone.utc).replace(microsecond=0)

    out_df = run(points, run_time_utc, DEFAULT_MODELS)

    out_name = f"wind_points_out_{run_time_utc.strftime('%Y%m%dT%H%M%SZ')}.csv"
    out_path = data_dir / out_name
    out_df.to_csv(out_path, index=False)

    print(f"Wrote {len(out_df):,} rows to {out_path}")


if __name__ == "__main__":
    main()
