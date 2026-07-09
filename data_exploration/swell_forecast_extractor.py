#!/usr/bin/env python3
"""
Swell Forecast Data Extractor
Extracts swell height, period, and direction from multiple sources:

1. Open-Meteo Marine API (free, no API key required):
   - MeteoFrance Wave (MFWAM) - Global 8km
   - ECMWF WAM - Global 9km
   - ECMWF WAM 0.25 - Global 25km  
   - GFS Wave - Global 16-25km
   - DWD EWAM - Europe 5km
   - DWD GWAM - Global 25km
   - ERA5-Ocean - Global 50km (historical)

2. CDIP (Coastal Data Information Program) via THREDDS/OPeNDAP:
   - MOP California Grid Forecast - High resolution (~1km) California coastal model
   - Station Forecasts - ~7-10 day forecasts from closest active buoys
   - Active buoys are dynamically discovered from ERDDAP
   - Uses ECMWF forecasts + CDIP buoy data

Variables extracted:
- Primary swell: height, period, direction
- Secondary swell: height, period, direction  
- Tertiary swell: height, period, direction (GFS only)
- Wind waves: height, period, direction
- Combined wave height

Usage:
    python swell_forecast_extractor.py [input_csv] [output_csv]

Requirements:
    pip install pandas requests xarray netCDF4 numpy

Optional (for better timezone handling):
    pip install pytz timezonefinder
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import sys
import os
import warnings

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# Try to import optional packages
try:
    import xarray as xr
    XARRAY_AVAILABLE = True
except ImportError:
    XARRAY_AVAILABLE = False
    print("Warning: xarray not installed. CDIP data source will be unavailable.")
    print("Install with: pip install xarray netCDF4")

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False

try:
    from timezonefinder import TimezoneFinder
    TZ_FINDER_AVAILABLE = True
except ImportError:
    TZ_FINDER_AVAILABLE = False


# =============================================================================
# OPEN-METEO MARINE API CONFIGURATION
# =============================================================================

MARINE_MODEL_CONFIGS = {
    # MeteoFrance Wave Model
    "mfwam": {
        "display_name": "MeteoFrance MFWAM",
        "model_param": "best_match",  # Will use MeteoFrance for relevant regions
        "resolution": "~8km",
        "description": "MeteoFrance Global Wave Model via Copernicus",
        "forecast_days": 10,
        "has_secondary_swell": True,
        "has_tertiary_swell": False
    },
    # ECMWF WAM High Resolution
    "ecmwf_wam": {
        "display_name": "ECMWF WAM 9km",
        "model_param": "ecmwf_wam",
        "resolution": "~9km",
        "description": "ECMWF Wave Model",
        "forecast_days": 15,
        "has_secondary_swell": False,
        "has_tertiary_swell": False
    },
    # ECMWF WAM 0.25 degree
    "ecmwf_wam025": {
        "display_name": "ECMWF WAM 25km",
        "model_param": "ecmwf_wam025",
        "resolution": "~25km",
        "description": "ECMWF Wave Model 0.25°",
        "forecast_days": 15,
        "has_secondary_swell": False,
        "has_tertiary_swell": False
    },
    # GFS Wave 0.25 degree
    "gfs_wave025": {
        "display_name": "GFS Wave 25km",
        "model_param": "gfs_wave025",
        "resolution": "~25km",
        "description": "NOAA GFS Wave Model",
        "forecast_days": 16,
        "has_secondary_swell": True,
        "has_tertiary_swell": True
    },
    # GFS Wave 0.16 degree (higher resolution)
    "gfs_wave016": {
        "display_name": "GFS Wave 16km",
        "model_param": "gfs_wave016",
        "resolution": "~16km",
        "description": "NOAA GFS Wave Model High Res",
        "forecast_days": 16,
        "has_secondary_swell": True,
        "has_tertiary_swell": True
    },
    # DWD EWAM (Europe)
    "dwd_ewam": {
        "display_name": "DWD EWAM (Europe)",
        "model_param": "ewam",
        "resolution": "~5km",
        "description": "DWD European Wave Model",
        "forecast_days": 8,
        "region": "Europe",
        "has_secondary_swell": False,
        "has_tertiary_swell": False
    },
    # DWD GWAM (Global)
    "dwd_gwam": {
        "display_name": "DWD GWAM",
        "model_param": "gwam",
        "resolution": "~25km",
        "description": "DWD Global Wave Model",
        "forecast_days": 4,
        "has_secondary_swell": False,
        "has_tertiary_swell": False
    },
}


# =============================================================================
# TIMEZONE UTILITIES
# =============================================================================

def get_local_timezone(lat: float, lon: float) -> str:
    """Get the local timezone for given coordinates."""
    if TZ_FINDER_AVAILABLE:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        return tz_name if tz_name else "auto"
    return "auto"


def get_local_date(lat: float, lon: float) -> tuple:
    """Get the current local date and datetime for given coordinates."""
    tz_name = get_local_timezone(lat, lon)
    
    if PYTZ_AVAILABLE and tz_name != "auto":
        try:
            tz = pytz.timezone(tz_name)
            local_now = datetime.now(tz)
            local_date = local_now.date()
            return local_date, local_now, tz_name
        except Exception:
            pass
    
    local_now = datetime.utcnow()
    local_date = local_now.date()
    return local_date, local_now, "auto"


# =============================================================================
# OPEN-METEO MARINE API FUNCTIONS
# =============================================================================

def build_marine_api_url(lat: float, lon: float, model: str, 
                         start_date: str, end_date: str, timezone: str,
                         include_secondary: bool = True,
                         include_tertiary: bool = False) -> str:
    """Build the Open-Meteo Marine API URL."""
    base_url = "https://marine-api.open-meteo.com/v1/marine"
    
    # Build hourly variables list - start with core variables available for all models
    hourly_vars = [
        # Combined waves
        "wave_height",
        "wave_direction", 
        "wave_period",
        # Wind waves
        "wind_wave_height",
        "wind_wave_direction",
        "wind_wave_period",
        "wind_wave_peak_period",
        # Primary swell
        "swell_wave_height",
        "swell_wave_direction",
        "swell_wave_period",
        "swell_wave_peak_period",
    ]
    
    # Add secondary swell if available (MeteoFrance and GFS models)
    if include_secondary:
        hourly_vars.extend([
            "secondary_swell_wave_height",
            "secondary_swell_wave_period",
            "secondary_swell_wave_direction",
        ])
    
    # Add tertiary swell if available (GFS models only)
    if include_tertiary:
        hourly_vars.extend([
            "tertiary_swell_wave_height",
            "tertiary_swell_wave_period",
            "tertiary_swell_wave_direction",
        ])
    
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(hourly_vars),
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
        "cell_selection": "sea",  # Prefer sea grid cells for marine data
    }
    
    # Add model if not best_match
    if model != "best_match":
        params["models"] = model
    
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{param_str}"


def fetch_marine_forecast(lat: float, lon: float, model_key: str,
                          start_date: str, end_date: str, timezone: str) -> dict:
    """Fetch marine forecast data from Open-Meteo API."""
    if model_key not in MARINE_MODEL_CONFIGS:
        return {"error": f"Unknown model: {model_key}"}
    
    config = MARINE_MODEL_CONFIGS[model_key]
    model_param = config["model_param"]
    include_secondary = config.get("has_secondary_swell", False)
    include_tertiary = config.get("has_tertiary_swell", False)
    
    url = build_marine_api_url(
        lat, lon, model_param, start_date, end_date, timezone,
        include_secondary, include_tertiary
    )
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        # If we get a 400 error with secondary/tertiary swells, retry without them
        if "400" in str(e) and (include_secondary or include_tertiary):
            # Retry without secondary/tertiary swell
            url_basic = build_marine_api_url(
                lat, lon, model_param, start_date, end_date, timezone,
                include_secondary=False, include_tertiary=False
            )
            try:
                response = requests.get(url_basic, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e2:
                return {"error": str(e2)}
        return {"error": str(e)}


def process_marine_response(response: dict, model_key: str,
                            site_name: str, lat: float, lon: float,
                            local_date) -> list:
    """Process the Open-Meteo Marine API response."""
    results = []
    
    if "error" in response:
        return results
    
    if "hourly" not in response:
        return results
    
    hourly = response["hourly"]
    times = hourly.get("time", [])
    
    config = MARINE_MODEL_CONFIGS[model_key]
    
    for i, timestamp in enumerate(times):
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        
        if dt.date() < local_date:
            continue
        
        record = {
            "site_name": site_name,
            "latitude": lat,
            "longitude": lon,
            "source": "Open-Meteo",
            "model": config["display_name"],
            "model_key": model_key,
            "resolution": config["resolution"],
            "timestamp_local": timestamp,
            # Combined waves
            "wave_height_m": hourly.get("wave_height", [None] * len(times))[i],
            "wave_direction_deg": hourly.get("wave_direction", [None] * len(times))[i],
            "wave_period_s": hourly.get("wave_period", [None] * len(times))[i],
            # Wind waves
            "wind_wave_height_m": hourly.get("wind_wave_height", [None] * len(times))[i],
            "wind_wave_direction_deg": hourly.get("wind_wave_direction", [None] * len(times))[i],
            "wind_wave_period_s": hourly.get("wind_wave_period", [None] * len(times))[i],
            "wind_wave_peak_period_s": hourly.get("wind_wave_peak_period", [None] * len(times))[i],
            # Primary swell
            "swell_height_m": hourly.get("swell_wave_height", [None] * len(times))[i],
            "swell_direction_deg": hourly.get("swell_wave_direction", [None] * len(times))[i],
            "swell_period_s": hourly.get("swell_wave_period", [None] * len(times))[i],
            "swell_peak_period_s": hourly.get("swell_wave_peak_period", [None] * len(times))[i],
            # Secondary swell
            "secondary_swell_height_m": hourly.get("secondary_swell_wave_height", [None] * len(times))[i],
            "secondary_swell_direction_deg": hourly.get("secondary_swell_wave_direction", [None] * len(times))[i],
            "secondary_swell_period_s": hourly.get("secondary_swell_wave_period", [None] * len(times))[i],
            # Tertiary swell
            "tertiary_swell_height_m": hourly.get("tertiary_swell_wave_height", [None] * len(times))[i],
            "tertiary_swell_direction_deg": hourly.get("tertiary_swell_wave_direction", [None] * len(times))[i],
            "tertiary_swell_period_s": hourly.get("tertiary_swell_wave_period", [None] * len(times))[i],
            "extraction_time_utc": datetime.utcnow().isoformat()
        }
        
        # Only add if we have at least wave height
        if record["wave_height_m"] is not None or record["swell_height_m"] is not None:
            results.append(record)
    
    return results


# =============================================================================
# CDIP DATA SOURCE FUNCTIONS
# =============================================================================

# Cache for active CDIP stations (populated dynamically from ERDDAP)
_CDIP_ACTIVE_STATIONS_CACHE = None
_CDIP_CACHE_TIME = None
CDIP_CACHE_DURATION_HOURS = 1  # How long to cache the active stations list


def fetch_active_cdip_stations(active_within_days: int = 7) -> pd.DataFrame:
    """
    Fetch list of active CDIP buoy stations from ERDDAP.
    
    Returns a DataFrame with columns:
    - station_id: Station ID (e.g., "071")
    - metaStationName: Station name (e.g., "Harvest")
    - latitude: Station latitude
    - longitude: Station longitude
    - time: Last report time
    - is_active: Whether station has reported within active_within_days
    
    Parameters:
    -----------
    active_within_days : int
        Number of days within which a station must have reported to be considered active
    
    Returns:
    --------
    pd.DataFrame
        DataFrame of active CDIP stations with their locations
    """
    global _CDIP_ACTIVE_STATIONS_CACHE, _CDIP_CACHE_TIME
    
    # Check cache
    if _CDIP_ACTIVE_STATIONS_CACHE is not None and _CDIP_CACHE_TIME is not None:
        cache_age_hours = (datetime.utcnow() - _CDIP_CACHE_TIME).total_seconds() / 3600
        if cache_age_hours < CDIP_CACHE_DURATION_HOURS:
            return _CDIP_ACTIVE_STATIONS_CACHE.copy()
    
    base_url = "https://erddap.cdip.ucsd.edu/erddap/tabledap/wave_agg.csv"
    # Query to get the latest report time for each station
    query = (
        "?station_id,metaStationName,latitude,longitude,time"
        "&orderByMax(%22station_id,time%22)"
    )
    
    try:
        df = pd.read_csv(base_url + query, dtype={"station_id": str})
        
        # Clean lat/lon if ERDDAP injects units strings in the first line
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        
        # Parse time and classify as active/inactive
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=active_within_days)
        df["is_active"] = df["time"] >= cutoff
        
        # Drop rows with invalid coordinates
        df = df.dropna(subset=["latitude", "longitude"])
        
        # Filter to only active stations
        active_df = df[df["is_active"]].copy()
        
        # Update cache
        _CDIP_ACTIVE_STATIONS_CACHE = active_df
        _CDIP_CACHE_TIME = datetime.utcnow()
        
        return active_df
        
    except Exception as e:
        print(f"    Warning: Could not fetch CDIP station list from ERDDAP: {e}")
        # Return empty DataFrame if fetch fails
        return pd.DataFrame(columns=["station_id", "metaStationName", "latitude", "longitude", "time", "is_active"])


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points in km."""
    R = 6371  # Earth's radius in kilometers
    
    lat1_rad = np.radians(lat1)
    lat2_rad = np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    
    a = np.sin(dlat/2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    
    return R * c


def find_closest_cdip_stations(lat: float, lon: float, n: int = 2, 
                               active_within_days: int = 7) -> list:
    """
    Find the n closest ACTIVE CDIP buoy stations to the given coordinates.
    
    This dynamically fetches the list of active stations from ERDDAP and
    calculates distances to find the closest ones.
    
    Parameters:
    -----------
    lat : float
        Target latitude
    lon : float
        Target longitude
    n : int
        Number of closest stations to return
    active_within_days : int
        Only consider stations that have reported within this many days
    
    Returns:
    --------
    list
        List of dictionaries with station info and distance
    """
    # Fetch active stations from ERDDAP
    active_stations = fetch_active_cdip_stations(active_within_days=active_within_days)
    
    if active_stations.empty:
        print("    Warning: No active CDIP stations available")
        return []
    
    distances = []
    for _, row in active_stations.iterrows():
        stn_lat = row["latitude"]
        stn_lon = row["longitude"]
        
        # Skip if coordinates are invalid
        if pd.isna(stn_lat) or pd.isna(stn_lon):
            continue
            
        dist = haversine_distance(lat, lon, stn_lat, stn_lon)
        distances.append({
            "station_id": str(row["station_id"]).zfill(3),  # Ensure 3-digit format
            "name": row["metaStationName"],
            "lat": stn_lat,
            "lon": stn_lon,
            "distance_km": dist,
            "last_report": row["time"]
        })
    
    distances.sort(key=lambda x: x["distance_km"])
    return distances[:n]


def fetch_cdip_station_forecast(station_id: str, station_name: str,
                                 site_name: str, site_lat: float, site_lon: float,
                                 local_date, distance_km: float) -> list:
    """
    Fetch CDIP MOP forecast data for a specific buoy station.
    
    Uses the CDIP THREDDS forecast endpoint which provides ~7-10 day forecasts.
    Example URL: https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_validation/BP071_forecast.nc
    
    Parameters:
    -----------
    station_id : str
        CDIP station ID (e.g., "071")
    station_name : str
        Station name for display
    site_name : str
        Name of the target site
    site_lat : float
        Target site latitude
    site_lon : float
        Target site longitude
    local_date : date
        Local date to filter forecasts from
    distance_km : float
        Distance from target site to buoy
    
    Returns:
    --------
    list
        List of forecast records
    """
    if not XARRAY_AVAILABLE:
        return []
    
    results = []
    
    # Ensure station_id is 3 digits
    station_id = str(station_id).zfill(3)
    
    # CDIP MOP Forecast URL (primary source)
    forecast_url = f"https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_validation/BP{station_id}_forecast.nc"
    
    try:
        ds = xr.open_dataset(forecast_url)
        
        # Get time variable
        times = ds["waveTime"].values
        
        # Define "today" in LA timezone, then convert to UTC for comparison
        if PYTZ_AVAILABLE:
            try:
                tz = pytz.timezone("America/Los_Angeles")
                start_today_la = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
                start_today_utc = start_today_la.astimezone(pytz.UTC).replace(tzinfo=None)
            except Exception:
                start_today_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_today_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get max time available
        tmax = pd.Timestamp(times.max())
        
        # Slice from today to max available
        time_mask = pd.to_datetime(times) >= start_today_utc
        
        # Get wave variables
        hs = ds["waveHs"].values  # Significant wave height (m)
        tp = ds["waveTp"].values if "waveTp" in ds.variables else None  # Peak period (s)
        dp = ds["waveDp"].values if "waveDp" in ds.variables else None  # Peak direction (deg)
        ta = ds["waveTa"].values if "waveTa" in ds.variables else None  # Average period (s)
        
        ds.close()
        
        for i, t in enumerate(times):
            if not time_mask[i]:
                continue
                
            try:
                dt = pd.Timestamp(t).to_pydatetime()
                
                # Filter for local date onwards
                if dt.date() < local_date:
                    continue
                
                hs_val = float(hs[i]) if not np.isnan(hs[i]) else None
                tp_val = float(tp[i]) if tp is not None and not np.isnan(tp[i]) else None
                dp_val = float(dp[i]) if dp is not None and not np.isnan(dp[i]) else None
                ta_val = float(ta[i]) if ta is not None and not np.isnan(ta[i]) else None
                
                record = {
                    "site_name": site_name,
                    "latitude": site_lat,
                    "longitude": site_lon,
                    "source": "CDIP",
                    "model": f"CDIP MOP Forecast Stn {station_id}",
                    "model_key": f"cdip_mop_forecast_{station_id}",
                    "buoy_id": station_id,
                    "buoy_name": station_name,
                    "buoy_distance_km": round(distance_km, 1),
                    "resolution": "Station point",
                    "timestamp_local": dt.isoformat(),
                    "wave_height_m": hs_val,
                    "wave_direction_deg": dp_val,
                    "wave_period_s": tp_val if tp_val else ta_val,
                    # Use wave data as swell (CDIP MOP provides total sea state)
                    "swell_height_m": hs_val,
                    "swell_direction_deg": dp_val,
                    "swell_period_s": tp_val if tp_val else ta_val,
                    "swell_peak_period_s": tp_val,
                    "extraction_time_utc": datetime.utcnow().isoformat()
                }
                
                if record["wave_height_m"] is not None:
                    results.append(record)
                    
            except Exception:
                continue
        
        return results
        
    except Exception as e:
        # Forecast file may not exist for all stations
        return []


def fetch_cdip_mop_grid_forecast(lat: float, lon: float, site_name: str,
                                  local_date) -> list:
    """
    Fetch wave data from CDIP MOP California high-resolution FORECAST grid model.
    This provides high-resolution (~0.01°, ~1km) coastal forecasts.
    
    Uses the FORECAST endpoint (not nowcast):
    https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_grids/CA_0.01_forecast.nc
    
    If the exact coordinates don't intersect with the grid, selects the closest
    valid grid point (same approach as other models).
    
    Parameters:
    -----------
    lat : float
        Target latitude
    lon : float
        Target longitude
    site_name : str
        Name of the target site
    local_date : date
        Local date to filter forecasts from
    
    Returns:
    --------
    list
        List of forecast records
    """
    if not XARRAY_AVAILABLE:
        print("    CDIP unavailable (xarray not installed)")
        return []
    
    # Check if location is roughly within CDIP California coverage
    # Expanded bounds to allow for closest-point selection near edges
    if not (30.0 <= lat <= 48.0 and -130.0 <= lon <= -115.0):
        print("    Outside CDIP California coverage area")
        return []
    
    results = []
    
    # CDIP MOP California FORECAST URL (not nowcast)
    cdip_url = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_grids/CA_0.01_forecast.nc"
    
    try:
        # Open the dataset
        ds = xr.open_dataset(cdip_url)
        
        # Get the grid coordinates - use metaLatitude/metaLongitude
        lats = ds['metaLatitude'].values
        lons = ds['metaLongitude'].values
        
        # Find nearest grid point
        lat_idx = np.argmin(np.abs(lats - lat))
        lon_idx = np.argmin(np.abs(lons - lon))
        
        nearest_lat = lats[lat_idx]
        nearest_lon = lons[lon_idx]
        
        # Calculate distance to nearest point
        distance_to_nearest = haversine_distance(lat, lon, nearest_lat, nearest_lon)
        
        # If too far (>100km), likely outside coverage entirely
        if distance_to_nearest > 100:
            print(f"    Nearest CDIP grid point too far ({distance_to_nearest:.1f}km)")
            ds.close()
            return []
        
        # Check if we have valid data at this point
        # Try to get first timestep to verify data exists
        test_data = ds['waveHs'].isel(metaLatitude=lat_idx, metaLongitude=lon_idx, waveTime=0).values
        
        if np.isnan(test_data):
            # Data is NaN at this point - search for closest valid point
            print(f"    No data at nearest point, searching for closest valid grid point...", end=" ")
            
            # Get all wave heights for first timestep to find valid points
            hs_grid = ds['waveHs'].isel(waveTime=0).values
            
            # Find all valid (non-NaN) points
            valid_mask = ~np.isnan(hs_grid)
            valid_indices = np.argwhere(valid_mask)
            
            if len(valid_indices) == 0:
                print("No valid data in grid")
                ds.close()
                return []
            
            # Calculate distances to all valid points
            min_dist = float('inf')
            best_lat_idx, best_lon_idx = lat_idx, lon_idx
            
            for vi, vj in valid_indices:
                valid_lat = lats[vi]
                valid_lon = lons[vj]
                dist = haversine_distance(lat, lon, valid_lat, valid_lon)
                if dist < min_dist:
                    min_dist = dist
                    best_lat_idx, best_lon_idx = vi, vj
            
            lat_idx, lon_idx = best_lat_idx, best_lon_idx
            nearest_lat = lats[lat_idx]
            nearest_lon = lons[lon_idx]
            distance_to_nearest = min_dist
            print(f"Found at {distance_to_nearest:.1f}km")
        
        # Get time variable
        times = ds['waveTime'].values
        
        # Define "today" for filtering
        if PYTZ_AVAILABLE:
            try:
                tz = pytz.timezone("America/Los_Angeles")
                start_today_la = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
                start_today_utc = start_today_la.astimezone(pytz.UTC).replace(tzinfo=None)
            except Exception:
                start_today_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_today_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Extract wave data at nearest valid point
        hs = ds['waveHs'].isel(metaLatitude=lat_idx, metaLongitude=lon_idx).values
        tp = ds['waveTp'].isel(metaLatitude=lat_idx, metaLongitude=lon_idx).values
        dp = ds['waveDp'].isel(metaLatitude=lat_idx, metaLongitude=lon_idx).values
        
        ds.close()
        
        # Determine resolution note
        if distance_to_nearest < 1:
            resolution_note = "~1km"
        else:
            resolution_note = f"~1km (nearest point {distance_to_nearest:.1f}km away)"
        
        # Convert times and filter
        for i, t in enumerate(times):
            try:
                dt = pd.Timestamp(t).to_pydatetime()
                
                # Filter for local date onwards
                if dt.date() < local_date:
                    continue
                
                hs_val = float(hs[i]) if not np.isnan(hs[i]) else None
                tp_val = float(tp[i]) if not np.isnan(tp[i]) else None
                dp_val = float(dp[i]) if not np.isnan(dp[i]) else None
                
                record = {
                    "site_name": site_name,
                    "latitude": lat,
                    "longitude": lon,
                    "source": "CDIP",
                    "model": "CDIP MOP California Grid Forecast",
                    "model_key": "cdip_mop_ca_grid_forecast",
                    "buoy_id": None,
                    "buoy_name": None,
                    "buoy_distance_km": None,
                    "resolution": resolution_note,
                    "timestamp_local": dt.isoformat(),
                    "wave_height_m": hs_val,
                    "wave_direction_deg": dp_val,
                    "wave_period_s": tp_val,
                    # CDIP MOP provides combined parameters
                    "swell_height_m": hs_val,
                    "swell_direction_deg": dp_val,
                    "swell_period_s": tp_val,
                    "extraction_time_utc": datetime.utcnow().isoformat()
                }
                
                if record["wave_height_m"] is not None:
                    results.append(record)
                    
            except Exception:
                continue
        
        return results
        
    except Exception as e:
        print(f"    CDIP MOP Grid Forecast error: {str(e)[:50]}...")
        return []


# =============================================================================
# MAIN EXTRACTION FUNCTIONS  
# =============================================================================

def load_locations(csv_path: str) -> pd.DataFrame:
    """Load location data from CSV file."""
    df = pd.read_csv(csv_path)
    
    required_cols = ["latitude", "longitude"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
    
    if "site_name" not in df.columns:
        df["site_name"] = [f"Location_{i+1}" for i in range(len(df))]
    
    return df


def extract_swell_forecasts(locations_df: pd.DataFrame,
                            models_to_use: list = None,
                            include_cdip: bool = True,
                            n_closest_buoys: int = 2) -> pd.DataFrame:
    """
    Extract swell forecast data for all locations from all specified models.
    
    Parameters:
    -----------
    locations_df : pd.DataFrame
        DataFrame with columns: site_name, latitude, longitude
    models_to_use : list, optional
        List of Open-Meteo marine model keys. If None, uses all.
    include_cdip : bool
        Whether to include CDIP data (California only)
    n_closest_buoys : int
        Number of closest CDIP buoys to fetch WW3 forecasts from (default: 2)
    
    Returns:
    --------
    pd.DataFrame
        Combined swell forecast data from all sources
    """
    if models_to_use is None:
        models_to_use = list(MARINE_MODEL_CONFIGS.keys())
    
    all_results = []
    
    for idx, row in locations_df.iterrows():
        site_name = row.get("site_name", f"Location_{idx+1}")
        lat = row["latitude"]
        lon = row["longitude"]
        
        print(f"\nProcessing location: {site_name} ({lat}, {lon})")
        
        # Get local timezone and date
        local_date, local_now, tz_name = get_local_date(lat, lon)
        print(f"  Local timezone: {tz_name}")
        print(f"  Local date/time: {local_now.strftime('%Y-%m-%d %H:%M')}")
        
        # =================================================================
        # OPEN-METEO MARINE API
        # =================================================================
        print(f"\n  --- Open-Meteo Marine API ---")
        
        for model_key in models_to_use:
            config = MARINE_MODEL_CONFIGS.get(model_key)
            if not config:
                continue
            
            forecast_days = config.get("forecast_days", 7)
            start_date = local_date.strftime("%Y-%m-%d")
            end_date = (local_date + timedelta(days=forecast_days)).strftime("%Y-%m-%d")
            
            print(f"  Fetching {config['display_name']} ({forecast_days} days)...", end=" ")
            
            response = fetch_marine_forecast(
                lat, lon, model_key, start_date, end_date, tz_name
            )
            
            results = process_marine_response(
                response, model_key, site_name, lat, lon, local_date
            )
            
            if results:
                # Add buoy columns with None values for Open-Meteo data
                for r in results:
                    r["buoy_id"] = None
                    r["buoy_name"] = None
                    r["buoy_distance_km"] = None
                print(f"Got {len(results)} records")
                all_results.extend(results)
            else:
                if "error" in response:
                    print(f"Error: {response['error'][:40]}...")
                else:
                    print("No data")
            
            time.sleep(0.2)
        
        # =================================================================
        # CDIP DATA (California region)
        # =================================================================
        if include_cdip:
            print(f"\n  --- CDIP (California Coastal) ---")
            
            # 1. MOP Grid Forecast Data (high resolution)
            print(f"  Fetching CDIP MOP California Grid Forecast...", end=" ")
            mop_results = fetch_cdip_mop_grid_forecast(lat, lon, site_name, local_date)
            if mop_results:
                print(f"Got {len(mop_results)} records")
                all_results.extend(mop_results)
            else:
                print("No data or outside coverage")
            
            # 2. Closest Active Buoy Station Forecasts (~7-10 days)
            if XARRAY_AVAILABLE:
                print(f"  Finding closest active CDIP buoys...", end=" ")
                closest_buoys = find_closest_cdip_stations(lat, lon, n=n_closest_buoys)
                
                if closest_buoys:
                    print(f"Found {len(closest_buoys)} nearby")
                else:
                    print("None found")
                
                for buoy in closest_buoys:
                    stn_id = buoy["station_id"]
                    stn_name = buoy["name"]
                    distance = buoy["distance_km"]
                    
                    print(f"  Fetching CDIP MOP Forecast Station {stn_id} ({stn_name}, {distance:.1f}km away)...", end=" ")
                    
                    buoy_results = fetch_cdip_station_forecast(
                        stn_id, stn_name, site_name, lat, lon, local_date, distance
                    )
                    
                    if buoy_results:
                        print(f"Got {len(buoy_results)} records")
                        all_results.extend(buoy_results)
                    else:
                        print("No data")
    
    # Convert to DataFrame
    if all_results:
        df = pd.DataFrame(all_results)
        
        # Ensure all columns exist (fill missing with None)
        all_columns = [
            "site_name", "latitude", "longitude", "source", "model", 
            "model_key", "buoy_id", "buoy_name", "buoy_distance_km",
            "resolution", "timestamp_local",
            "wave_height_m", "wave_direction_deg", "wave_period_s",
            "wind_wave_height_m", "wind_wave_direction_deg", 
            "wind_wave_period_s", "wind_wave_peak_period_s",
            "swell_height_m", "swell_direction_deg", 
            "swell_period_s", "swell_peak_period_s",
            "secondary_swell_height_m", "secondary_swell_direction_deg",
            "secondary_swell_period_s",
            "tertiary_swell_height_m", "tertiary_swell_direction_deg",
            "tertiary_swell_period_s",
            "extraction_time_utc"
        ]
        
        for col in all_columns:
            if col not in df.columns:
                df[col] = None
        
        # Reorder columns
        df = df[[c for c in all_columns if c in df.columns]]
        
        # Sort by site, model, and timestamp
        df = df.sort_values(["site_name", "model", "timestamp_local"])
        
        return df
    else:
        return pd.DataFrame()


def main():
    """Main function to run the swell forecast extraction."""
    # Configuration
    if len(sys.argv) >= 2:
        input_csv = sys.argv[1]
    else:
        input_csv = "/mnt/user-data/uploads/locations.csv"
    
    if len(sys.argv) >= 3:
        output_csv = sys.argv[2]
    else:
        output_csv = "/mnt/user-data/outputs/swell_forecast_results.csv"
    
    # Models to query
    models_to_use = [
        "gfs_wave025",      # GFS Wave 25km - 16 days, has secondary/tertiary swell
        "gfs_wave016",      # GFS Wave 16km - 16 days, higher res
        "ecmwf_wam",        # ECMWF WAM 9km - 15 days
        "ecmwf_wam025",     # ECMWF WAM 25km - 15 days
        "mfwam",            # MeteoFrance - 10 days, has secondary swell
        "dwd_gwam",         # DWD Global - 4 days
    ]
    
    print("=" * 70)
    print("Swell Forecast Data Extractor")
    print("=" * 70)
    print("\nData Sources:")
    print("-" * 70)
    print("1. Open-Meteo Marine API (free, no API key):")
    print(f"   {'Model':<25} {'Resolution':<12} {'Forecast':<12} {'Swells'}")
    print("-" * 70)
    for key in models_to_use:
        cfg = MARINE_MODEL_CONFIGS.get(key, {})
        swells = "1"
        if cfg.get("has_secondary_swell"):
            swells = "1+2"
        if cfg.get("has_tertiary_swell"):
            swells = "1+2+3"
        print(f"   {cfg.get('display_name', key):<25} {cfg.get('resolution', '?'):<12} "
              f"{cfg.get('forecast_days', '?')} days{'':<5} {swells}")
    print("-" * 70)
    print("2. CDIP (Coastal Data Information Program):")
    print("   a) MOP California Grid Forecast: ~1km resolution (forecast endpoint)")
    print("      - Uses closest valid grid point if coordinates don't intersect")
    print("   b) Station Forecasts: ~7-10 days forecast at closest active buoys")
    print("      - Active buoys dynamically discovered from ERDDAP")
    print("      - Columns: buoy_id, buoy_name, buoy_distance_km identify the buoy")
    print("-" * 70)
    
    # Load locations
    print(f"\nLoading locations from: {input_csv}")
    try:
        locations_df = load_locations(input_csv)
        print(f"Loaded {len(locations_df)} locations:")
        for _, row in locations_df.iterrows():
            print(f"  - {row.get('site_name', 'Unknown')}: {row['latitude']}, {row['longitude']}")
    except Exception as e:
        print(f"Error loading locations: {e}")
        sys.exit(1)
    
    # Extract forecasts
    print("\n" + "=" * 70)
    print("Extracting swell forecasts...")
    print("=" * 70)
    
    results_df = extract_swell_forecasts(
        locations_df,
        models_to_use=models_to_use,
        include_cdip=True
    )
    
    if results_df.empty:
        print("\nNo forecast data retrieved!")
        sys.exit(1)
    
    # Save results
    print("\n" + "=" * 70)
    print("Saving results...")
    print("=" * 70)
    
    output_dir = os.path.dirname(output_csv)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    results_df.to_csv(output_csv, index=False)
    print(f"\nResults saved to: {output_csv}")
    
    # Summary statistics
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total records: {len(results_df)}")
    print(f"Sites: {results_df['site_name'].nunique()}")
    print(f"Models/Sources: {results_df['model'].nunique()}")
    
    print("\nRecords per model:")
    for model, count in results_df.groupby('model').size().items():
        print(f"  {model}: {count} records")
    
    # Show buoy info if present
    buoy_data = results_df[results_df['buoy_id'].notna()]
    if not buoy_data.empty:
        print("\nCDIP Buoy stations used:")
        for _, row in buoy_data.drop_duplicates(subset=['buoy_id', 'site_name']).iterrows():
            print(f"  Station {row['buoy_id']} ({row['buoy_name']}) - "
                  f"{row['buoy_distance_km']}km from {row['site_name']}")
    
    print("\nDate range per site:")
    for site in results_df['site_name'].unique():
        site_data = results_df[results_df['site_name'] == site]
        min_time = site_data['timestamp_local'].min()
        max_time = site_data['timestamp_local'].max()
        print(f"  {site}: {min_time[:10]} to {max_time[:10]}")
    
    # Show sample of swell data
    print("\nSample data (first record with swell info):")
    sample = results_df[results_df['swell_height_m'].notna()].head(1)
    if not sample.empty:
        row = sample.iloc[0]
        print(f"  Site: {row['site_name']}")
        print(f"  Model: {row['model']}")
        if pd.notna(row.get('buoy_id')):
            print(f"  Buoy: {row['buoy_id']} ({row['buoy_name']})")
        print(f"  Time: {row['timestamp_local']}")
        if pd.notna(row.get('swell_height_m')):
            print(f"  Primary Swell: {row['swell_height_m']:.2f}m, "
                  f"{row['swell_period_s']:.1f}s, {row['swell_direction_deg']:.0f}°")
        if pd.notna(row.get('secondary_swell_height_m')):
            print(f"  Secondary Swell: {row['secondary_swell_height_m']:.2f}m, "
                  f"{row['secondary_swell_period_s']:.1f}s, "
                  f"{row['secondary_swell_direction_deg']:.0f}°")
    
    return results_df


if __name__ == "__main__":
    main()
