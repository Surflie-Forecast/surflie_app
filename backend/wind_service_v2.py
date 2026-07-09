#!/usr/bin/env python3
import math
import numpy as np
import pandas as pd
import xarray as xr
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from timezonefinder import TimezoneFinder
from herbie import Herbie
import zoneinfo

# -------------------------
# Utilities
# -------------------------

def wind_speed_dir_from_uv(u: float, v: float) -> Tuple[float, float]:
    speed = float(math.hypot(u, v))
    direction = (180.0 + math.degrees(math.atan2(u, v))) % 360.0
    return speed, direction

def mps_to_knots(x: float) -> float:
    return float(x) * 1.9438444924406

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2)**2
    return 2 * r * math.asin(math.sqrt(a))

def get_tz_name(lat: float, lon: float) -> str:
    tf = TimezoneFinder()
    return tf.timezone_at(lat=lat, lng=lon) or "UTC"

def floor_to_cycle(dt_utc: datetime, cycle_hours: int) -> datetime:
    """Ensures the datetime remains offset-aware UTC."""
    # Ensure input is UTC
    dt_utc = dt_utc.astimezone(timezone.utc)
    # 2-hour buffer for data publishing delay
    dt_utc = dt_utc - timedelta(hours=2)
    floored_hour = (dt_utc.hour // cycle_hours) * cycle_hours
    return dt_utc.replace(hour=floored_hour, minute=0, second=0, microsecond=0)

def choose_init_fxx(run_time_utc: datetime, valid_time_utc: datetime, cycle_hours: int) -> Tuple[datetime, int]:
    init = floor_to_cycle(run_time_utc, cycle_hours)
    # Ensure comparison is possible by forcing UTC
    valid_time_utc = valid_time_utc.astimezone(timezone.utc)
    
    while init > valid_time_utc:
        init = init - timedelta(hours=cycle_hours)
    
    fxx = int(round((valid_time_utc - init).total_seconds() / 3600.0))
    return init, fxx

def nearest_value_from_xarray(ds, var_name: str, lat: float, lon: float) -> Tuple[float, float, float]:
    da = ds[var_name].squeeze()
    
    # Standardize longitude to 0-360 if necessary (common in GFS)
    lon_query = lon if lon >= 0 else lon + 360
    
    # Try Herbie's built-in fast point extractor first
    try:
        p = ds.herbie.nearest_points(points=(lon, lat))
        val = float(p[var_name].values)
        return val, float(p.latitude.values), float(p.longitude.values)
    except:
        # Fallback to manual selection
        lat_key = next((c for c in da.coords if c.lower() in {'latitude', 'lat', 'nav_lat'}), "lat")
        lon_key = next((c for c in da.coords if c.lower() in {'longitude', 'lon', 'nav_lon'}), "lon")
        
        pick = da.sel({lat_key: lat, lon_key: lon_query}, method="nearest")
        return float(pick.values), float(pick[lat_key].values), float(pick[lon_key].values)

# -------------------------
# Model Specs
# -------------------------

@dataclass
class HerbieModelSpec:
    name: str
    herbie_model: str
    product: Optional[str]
    cycle_hours: int

HERBIE_MODELS = {
    "gfs":   HerbieModelSpec("GFS 0.25", "gfs", "pgrb2.0p25", 6),
    "hrrr":  HerbieModelSpec("HRRR", "hrrr", "sfc", 1),
    "hrdps": HerbieModelSpec("HRDPS", "hrdps", "continental/2.5km", 6),
    "ecmwf": HerbieModelSpec("ECMWF", "ifs", "oper", 6),
}

# -------------------------
# Core Fetcher
# -------------------------

def fetch_wind(model_key: str, run_time_utc: datetime, valid_time_utc: datetime, lat: float, lon: float) -> Dict[str, Any]:
    spec = HERBIE_MODELS[model_key]
    init, fxx = choose_init_fxx(run_time_utc, valid_time_utc, spec.cycle_hours)
    
    H = Herbie(init, model=spec.herbie_model, product=spec.product, fxx=fxx, verbose=False)
    
    # Search strings: HRDPS uses ':10 m AGL:', others usually ':10 m above ground:'
    # Using a regex '(?:U|V)GRD' to get both in one call to save time
    search = ":[U|V]GRD:10 m"
    if model_key == "hrdps":
        search = ":[U|V]GRD:10 m AGL"

    ds = H.xarray(search)
    
    # Identify variable names (Herbie often renames them to u10/v10 or u/v)
    u_var = [v for v in ds.data_vars if 'u' in v.lower()][0]
    v_var = [v for v in ds.data_vars if 'v' in v.lower()][0]
    
    u10, glat, glon = nearest_value_from_xarray(ds, u_var, lat, lon)
    v10, _, _ = nearest_value_from_xarray(ds, v_var, lat, lon)
    
    speed, d_from = wind_speed_dir_from_uv(u10, v10)
    
    return {
        "status": "ok",
        "model_name": spec.name,
        "wind_speed_mps": round(speed, 2),
        "wind_speed_kt": round(mps_to_knots(speed), 2),
        "wind_dir_deg_from": round(d_from, 1),
        "grid_lat": round(glat, 4),
        "grid_lon": round(glon, 4),
        "dist_km": round(haversine_km(lat, lon, glat, glon), 2)
    }

# -------------------------
# Execution
# -------------------------

def main():
    # Setup
    run_time_utc = datetime.now(timezone.utc)
    locations = [
        {"site_name": "C st.", "region": "Ventura", "lat": 34.27, "lon": -119.30},
        {"site_name": "Devereaux", "region": "Santa Barbara", "lat": 34.41, "lon": -119.88}
    ]
    
    results = []
    # Forecast for the next 48 hours, every 6 hours
    for loc in locations:
        tz = get_tz_name(loc['lat'], loc['lon'])
        for h in range(0, 49, 6):
            valid_utc = (run_time_utc + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
            valid_local = valid_utc.astimezone(zoneinfo.ZoneInfo(tz))
            
            for m_key in HERBIE_MODELS.keys():
                row = {
                    "site_name": loc['site_name'],
                    "region": loc['region'],
                    "valid_time_local": valid_local.isoformat(),
                    "model_key": m_key,
                    "status": "pending"
                }
                try:
                    data = fetch_wind(m_key, run_time_utc, valid_utc, loc['lat'], loc['lon'])
                    row.update(data)
                except Exception as e:
                    row["status"] = "error"
                    row["error"] = str(e)
                results.append(row)

    df = pd.DataFrame(results)
    df.to_csv("wind_forecast.csv", index=False)
    print("Forecast complete. Saved to wind_forecast.csv")

if __name__ == "__main__":
    main()