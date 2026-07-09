#!/usr/bin/env python3
"""
Weather Forecast Data Extractor
Extracts wind speed and wind direction from multiple weather models via Open-Meteo API.

Supported models:
- GFS (Global Forecast System) - ~25km and ~13km resolution
- HRRR (High-Resolution Rapid Refresh) - 3km resolution (US only)
- NAM (North American Mesoscale) - 5km resolution (US only)  
- HRDPS (High Resolution Deterministic Prediction System) - 2.5km resolution (Canada)
- ECMWF IFS - 9km resolution (Global)
- ICON (ICOsahedral Nonhydrostatic) - 13km resolution (Global)
- GEM (Global Environmental Multiscale) - Canadian models

Note: Some models (HRRR-AK, NAM-AK) are not available in Open-Meteo API.
The API automatically returns data from the nearest pixel for any given coordinate.

Usage:
    python weather_forecast_extractor.py [input_csv] [output_csv]

Requirements:
    pip install pandas requests pytz timezonefinder

If timezonefinder is not installed, the script will use 'auto' timezone 
which lets Open-Meteo determine the timezone automatically.
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import sys
import os

# Try to import timezone libraries
try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("Warning: pytz not installed. Using UTC for timestamps.")

try:
    from timezonefinder import TimezoneFinder
    TZ_FINDER_AVAILABLE = True
except ImportError:
    TZ_FINDER_AVAILABLE = False
    print("Warning: timezonefinder not installed. Using 'auto' timezone.")


# Model configurations mapping user-requested models to Open-Meteo API parameters
MODEL_CONFIGS = {
    # GFS Models
    "gfs_seamless": {
        "display_name": "GFS Seamless",
        "endpoint": "gfs",
        "model_param": "gfs_seamless",
        "resolution": "~13-25km",
        "description": "Combined GFS global model",
        "forecast_days": 16  # GFS goes up to 16 days
    },
    "gfs_global": {
        "display_name": "GFS 22km",
        "endpoint": "gfs",
        "model_param": "gfs_global",
        "resolution": "~25km",
        "description": "GFS Global 0.25°",
        "forecast_days": 16
    },
    # HRRR Model (US only)
    "hrrr_conus": {
        "display_name": "HRRR 3km",
        "endpoint": "gfs",
        "model_param": "hrrr_conus",
        "resolution": "3km",
        "description": "High-Resolution Rapid Refresh (US Continental)",
        "region": "US",
        "forecast_days": 2  # HRRR only goes 18-48 hours
    },
    # NAM Model (US only)
    "nam_conus": {
        "display_name": "NAM 5km",
        "endpoint": "gfs",
        "model_param": "nam_conus",
        "resolution": "3km",
        "description": "North American Mesoscale (US Continental)",
        "region": "US",
        "forecast_days": 3  # NAM goes ~60 hours
    },
    # Canadian HRDPS
    "gem_hrdps_continental": {
        "display_name": "HRDPS 2.5km",
        "endpoint": "gem",
        "model_param": "gem_hrdps_continental",
        "resolution": "2.5km",
        "description": "Canadian High Resolution Deterministic Prediction System",
        "region": "Canada/Northern US",
        "forecast_days": 2  # HRDPS goes ~48 hours
    },
    # ECMWF Models
    "ecmwf_ifs04": {
        "display_name": "ECMWF IFS 9km",
        "endpoint": "ecmwf",
        "model_param": "ifs04",
        "resolution": "~9km",
        "description": "ECMWF Integrated Forecast System",
        "forecast_days": 10  # ECMWF IFS goes up to 10 days (some versions 15)
    },
    # ICON Models
    "icon_global": {
        "display_name": "ICON 13km",
        "endpoint": "dwd-icon",
        "model_param": "icon_global",
        "resolution": "~13km",
        "description": "DWD ICON Global Model",
        "forecast_days": 8  # ICON Global goes ~7.5 days
    },
    "icon_seamless": {
        "display_name": "ICON Seamless",
        "endpoint": "dwd-icon",
        "model_param": "icon_seamless",
        "resolution": "~2-13km",
        "description": "DWD ICON Combined (Global + EU + D2)",
        "forecast_days": 8
    },
    # NBM Model
    "nbm_conus": {
        "display_name": "NBM Conus",
        "endpoint": "gfs",
        "model_param": "nbm_conus",
        "resolution": "2.5km",
        "description": "National Blend of Models (US)",
        "region": "US",
        "forecast_days": 11  # NBM goes up to 11 days
    },
    # GEM Global
    "gem_global": {
        "display_name": "GEM Global",
        "endpoint": "gem",
        "model_param": "gem_global",
        "resolution": "~15km",
        "description": "Canadian GEM Global Model",
        "forecast_days": 10  # GEM Global goes ~10 days
    }
}

# Map user-requested model names to API model keys
USER_MODEL_MAPPING = {
    "gfs27": "gfs_global",
    "gfs 22km": "gfs_global", 
    "gfs+": "gfs_seamless",
    "hrrr 3km": "hrrr_conus",
    "hrrr": "hrrr_conus",
    "nam 5km": "nam_conus",
    "nam": "nam_conus",
    "hrdps 2.5 km": "gem_hrdps_continental",
    "hrdps": "gem_hrdps_continental",
    "ecmwf 9km": "ecmwf_ifs04",
    "ecmwf": "ecmwf_ifs04",
    "icon13": "icon_global",
    "icon 13km": "icon_global",
    "icon": "icon_seamless",
}


def get_local_timezone(lat: float, lon: float) -> str:
    """Get the local timezone for given coordinates."""
    if TZ_FINDER_AVAILABLE:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        return tz_name if tz_name else "auto"
    return "auto"  # Let Open-Meteo determine timezone


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
    
    # Fallback: use UTC
    local_now = datetime.utcnow()
    local_date = local_now.date()
    return local_date, local_now, "auto"


def build_api_url(endpoint: str, lat: float, lon: float, model: str, 
                  start_date: str, end_date: str, timezone: str) -> str:
    """Build the Open-Meteo API URL for the given parameters."""
    base_url = f"https://api.open-meteo.com/v1/{endpoint}"
    
    # Common parameters
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
    }
    
    # Add model parameter based on endpoint
    if endpoint in ["gfs", "dwd-icon", "gem"]:
        params["models"] = model
    elif endpoint == "ecmwf":
        # ECMWF endpoint uses different parameter
        pass  # Default model is used
    
    # Build URL
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{param_str}"


def fetch_forecast_data(lat: float, lon: float, model_key: str, 
                        start_date: str, end_date: str, timezone: str) -> dict:
    """Fetch forecast data from Open-Meteo API for a specific model."""
    if model_key not in MODEL_CONFIGS:
        return {"error": f"Unknown model: {model_key}"}
    
    config = MODEL_CONFIGS[model_key]
    endpoint = config["endpoint"]
    model_param = config["model_param"]
    
    url = build_api_url(endpoint, lat, lon, model_param, start_date, end_date, timezone)
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def process_forecast_response(response: dict, model_key: str, 
                              site_name: str, lat: float, lon: float,
                              local_date: datetime.date) -> list:
    """Process the API response and extract wind data."""
    results = []
    
    if "error" in response:
        print(f"    Warning: {response['error']}")
        return results
    
    if "hourly" not in response:
        print(f"    Warning: No hourly data in response for {model_key}")
        return results
    
    hourly = response["hourly"]
    times = hourly.get("time", [])
    wind_speeds = hourly.get("wind_speed_10m", [])
    wind_directions = hourly.get("wind_direction_10m", [])
    wind_gusts = hourly.get("wind_gusts_10m", [])
    
    config = MODEL_CONFIGS[model_key]
    
    for i, timestamp in enumerate(times):
        # Parse timestamp
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        
        # Filter: only include data from local_date onwards (start of day)
        if dt.date() < local_date:
            continue
        
        wind_speed = wind_speeds[i] if i < len(wind_speeds) else None
        wind_dir = wind_directions[i] if i < len(wind_directions) else None
        wind_gust = wind_gusts[i] if i < len(wind_gusts) else None
        
        # Skip if no wind data
        if wind_speed is None and wind_dir is None:
            continue
        
        results.append({
            "site_name": site_name,
            "latitude": lat,
            "longitude": lon,
            "model": config["display_name"],
            "model_key": model_key,
            "resolution": config["resolution"],
            "timestamp_local": timestamp,
            "wind_speed_10m_kmh": wind_speed,
            "wind_direction_10m_deg": wind_dir,
            "wind_gusts_10m_kmh": wind_gust,
            "extraction_time_utc": datetime.utcnow().isoformat()
        })
    
    return results


def load_locations(csv_path: str) -> pd.DataFrame:
    """Load location data from CSV file."""
    df = pd.read_csv(csv_path)
    
    # Validate required columns
    required_cols = ["latitude", "longitude"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
    
    # Add site_name if not present
    if "site_name" not in df.columns:
        df["site_name"] = [f"Location_{i+1}" for i in range(len(df))]
    
    return df


def extract_wind_forecasts(locations_df: pd.DataFrame, 
                           models_to_use: list = None) -> pd.DataFrame:
    """
    Extract wind forecast data for all locations from all specified models.
    Each model fetches its maximum available forecast horizon.
    
    Parameters:
    -----------
    locations_df : pd.DataFrame
        DataFrame with columns: site_name, latitude, longitude
    models_to_use : list, optional
        List of model keys to use. If None, uses all available models.
    
    Returns:
    --------
    pd.DataFrame
        Combined forecast data from all models and locations
    """
    if models_to_use is None:
        models_to_use = list(MODEL_CONFIGS.keys())
    
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
        
        for model_key in models_to_use:
            config = MODEL_CONFIGS.get(model_key)
            if not config:
                print(f"    Skipping unknown model: {model_key}")
                continue
            
            # Get model-specific forecast days
            forecast_days = config.get("forecast_days", 7)
            
            # Calculate date range for this specific model
            start_date = local_date.strftime("%Y-%m-%d")
            end_date = (local_date + timedelta(days=forecast_days)).strftime("%Y-%m-%d")
            
            print(f"  Fetching {config['display_name']} ({forecast_days} days, to {end_date})...", end=" ")
            
            # Fetch data
            response = fetch_forecast_data(
                lat, lon, model_key, start_date, end_date, tz_name
            )
            
            # Process response
            results = process_forecast_response(
                response, model_key, site_name, lat, lon, local_date
            )
            
            if results:
                print(f"Got {len(results)} records")
                all_results.extend(results)
            else:
                print("No data (model may not cover this region)")
            
            # Small delay to be nice to the API
            time.sleep(0.2)
    
    # Convert to DataFrame
    if all_results:
        df = pd.DataFrame(all_results)
        # Sort by site, model, and timestamp
        df = df.sort_values(["site_name", "model", "timestamp_local"])
        return df
    else:
        return pd.DataFrame()


def main():
    """Main function to run the wind forecast extraction."""
    # Configuration - can be overridden by command line arguments
    if len(sys.argv) >= 2:
        input_csv = sys.argv[1]
    else:
        input_csv = "/mnt/user-data/uploads/locations.csv"
    
    if len(sys.argv) >= 3:
        output_csv = sys.argv[2]
    else:
        output_csv = "/mnt/user-data/outputs/wind_forecast_results.csv"
    
    # Models to query (all available models that might work for US locations)
    # These cover the user's requested models where available in Open-Meteo
    models_to_use = [
        "gfs_seamless",      # GFS combined (~13-25km) - Global - maps to "gfs+"
        "gfs_global",        # GFS 22km/27km - Global - maps to "gfs27", "gfs 22km"
        "hrrr_conus",        # HRRR 3km - US only - maps to "hrrr 3km"
        "nam_conus",         # NAM 5km - US only - maps to "nam 5km"
        "nbm_conus",         # NBM 2.5km - US only
        "gem_hrdps_continental",  # HRDPS 2.5km - Canada/Northern US - maps to "hrdps 2.5 km"
        "gem_global",        # GEM Global 15km - Global
        "ecmwf_ifs04",       # ECMWF 9km - Global - maps to "ecmwf 9km"
        "icon_global",       # ICON 13km - Global - maps to "icon13", "icon 13km"
        "icon_seamless",     # ICON Combined - Global/EU
    ]
    
    print("=" * 70)
    print("Wind Forecast Data Extractor")
    print("Using Open-Meteo API (free, no API key required)")
    print("=" * 70)
    print("\nModel Mapping (User Request -> Open-Meteo Model):")
    print("-" * 70)
    print(f"  {'Model':<25} {'Resolution':<12} {'Max Forecast':<12}")
    print("-" * 70)
    print(f"  {'gfs27 / gfs 22km':<25} {'~25km':<12} {'16 days':<12}")
    print(f"  {'gfs+':<25} {'~13-25km':<12} {'16 days':<12}")
    print(f"  {'hrrr 3km':<25} {'3km':<12} {'2 days':<12}")
    print(f"  {'nam 5km':<25} {'~5km':<12} {'3 days':<12}")
    print(f"  {'nbm':<25} {'2.5km':<12} {'11 days':<12}")
    print(f"  {'hrdps 2.5 km':<25} {'2.5km':<12} {'2 days':<12}")
    print(f"  {'gem global':<25} {'~15km':<12} {'10 days':<12}")
    print(f"  {'ecmwf 9km':<25} {'~9km':<12} {'10 days':<12}")
    print(f"  {'icon13 / icon 13km':<25} {'~13km':<12} {'8 days':<12}")
    print(f"  {'icon seamless':<25} {'~2-13km':<12} {'8 days':<12}")
    print("-" * 70)
    print("  NOT AVAILABLE: hrrr-ak 3km, nam-ak 6km, exp3")
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
    print("Extracting wind forecasts from multiple models...")
    print("=" * 70)
    
    results_df = extract_wind_forecasts(
        locations_df, 
        models_to_use=models_to_use
    )
    
    if results_df.empty:
        print("\nNo forecast data retrieved!")
        sys.exit(1)
    
    # Save results
    print("\n" + "=" * 70)
    print("Saving results...")
    print("=" * 70)
    
    # Ensure output directory exists (only if there's a directory component)
    output_dir = os.path.dirname(output_csv)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    # Reorder columns for better readability
    column_order = [
        "site_name",
        "latitude", 
        "longitude",
        "timestamp_local",
        "model",
        "model_key",
        "resolution",
        "wind_speed_10m_kmh",
        "wind_direction_10m_deg",
        "wind_gusts_10m_kmh",
        "extraction_time_utc"
    ]
    results_df = results_df[[c for c in column_order if c in results_df.columns]]
    
    # Save to CSV
    results_df.to_csv(output_csv, index=False)
    print(f"\nResults saved to: {output_csv}")
    
    # Summary statistics
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total records: {len(results_df)}")
    print(f"Sites: {results_df['site_name'].nunique()}")
    print(f"Models with data: {results_df['model'].nunique()}")
    print("\nRecords per model:")
    for model, count in results_df.groupby('model').size().items():
        print(f"  {model}: {count} records")
    
    print("\nDate range per site:")
    for site in results_df['site_name'].unique():
        site_data = results_df[results_df['site_name'] == site]
        min_time = site_data['timestamp_local'].min()
        max_time = site_data['timestamp_local'].max()
        print(f"  {site}: {min_time} to {max_time}")
    
    return results_df


if __name__ == "__main__":
    main()
