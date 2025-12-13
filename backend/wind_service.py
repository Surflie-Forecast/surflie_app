import xarray as xr
import pandas as pd
import numpy as np
import datetime
import math
import csv
import os
import pytz
import time

def fetch_wind_data(lat, lon, name, output_csv_path):
    # --- 1. TIMEZONE SETUP (Identical to original) ---
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    # Define exact Start/End of the requested days
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=10)
    
    print(f"Fetching wind data for {name} ({lat}, {lon})...")
    print(f"Target Window (LA): {start_of_today_la} to {end_of_window_la}")

    # --- 2. DETERMINE NOAA GFS CYCLE ---
    # We need to construct the URL for the latest available cycle (00z, 06z, 12z, 18z)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_str = now_utc.strftime('%Y%m%d')
    hour = now_utc.hour

    # Logic to pick the latest "likely available" cycle (approx 4 hour lag)
    # Logic to pick the cycle that ensures FULL coverage for the current LA day.
    # Start of "Today" in LA is approx 08:00 UTC (07:00 UTC in Daylight Savings).
    # To cover 08:00 UTC onwards, we need a cycle starting at 06z (06:00 UTC) or earlier.
    # Cycles 12z and 18z start too late (12:00/18:00 UTC) and miss the LA morning.
    
    if hour < 8: 
        # 00:00 - 08:00 UTC. In LA, this is still the previous day (16:00 - 24:00 PST).
        # We need data for "Today" (which is Day T-1 in UTC terms).
        # Safe bet: Day T-1 06z.
        cycle = '06'
        date_str = (now_utc - datetime.timedelta(days=1)).strftime('%Y%m%d')
    elif hour < 11: 
        # 08:00 - 11:00 UTC. It is "Today" in LA (00:00 - 03:00 PST).
        # We want Day T 06z, but it's not available until ~10:30 UTC.
        # So we fall back to Day T 00z.
        cycle = '00'
    else:
        # 11:00+ UTC. 06z is definitely available.
        cycle = '06'
        # Note: We purposely avoid 12z and 18z to maintain full-day coverage.

    url = f'https://nomads.ncep.noaa.gov/dods/gfs_0p25/gfs{date_str}/gfs_0p25_{cycle}z'
    print(f"Connecting to NOAA: {url}")

    # --- 3. CONNECT & SLICE (With Retry Logic) ---
    ds = None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Lazy load the dataset
            ds = xr.open_dataset(url)
            break
        except Exception as e:
            print(f"  [Attempt {attempt+1}] Connection failed ({e}). Retrying in 5s...")
            time.sleep(5)
    
    if ds is None:
        print("  X Failed to connect to NOAA.")
        return

    try:
        # Convert Lon to 0-360 scale (GFS standard)
        target_lon_360 = 360 + lon if lon < 0 else lon
        
        # Select variables, nearest location, and next 10 days (~80 3hr steps)
        # ugrd10m = U Component, vgrd10m = V Component
        subset = ds[['ugrd10m', 'vgrd10m']].sel(
            lat=lat, 
            lon=target_lon_360, 
            method='nearest'
        ).isel(time=slice(0, 85)) # Grab slightly more than 10 days to be safe
        
        # Download actual values to DataFrame
        df = subset.to_dataframe().reset_index()
        
    except Exception as e:
        print(f"  X Data extraction failed: {e}")
        return

    # --- 4. CALCULATE & FILTER ---
    results = []
    
    for _, row in df.iterrows():
        u = row['ugrd10m']
        v = row['vgrd10m']
        
        if pd.isna(u): continue
        
        # A. Speed (Hypotenuse)
        speed_ms = math.sqrt(u**2 + v**2)
        
        # B. Direction (Meteorological Convention)
        # Standard Math: atan2(u, v) gives direction vector points TO
        # Met Convention: We want direction wind blows FROM.
        # Formula: (atan2(u, v) * 180/pi) + 180
        rad_to_deg = 180 / math.pi
        direction = (math.atan2(u, v) * rad_to_deg) + 180
        direction = direction % 360
        
        # C. Time Conversion
        dt_utc = row['time'].replace(tzinfo=datetime.timezone.utc)
        dt_la = dt_utc.astimezone(la_tz)
        
        # STRICT FILTERING (Same as your original script)
        # Only save rows >= Today's Midnight LA
        if dt_la >= start_of_today_la and dt_la <= end_of_window_la:
            results.append({
                'spot_name': name,
                'datetime': dt_la.strftime('%Y-%m-%d %H:%M:%S'),
                'wind_speed': round(speed_ms * 1.94384, 2), # Convert m/s to Knots
                'wind_direction': int(round(direction))
            })

    # Sort by date
    results.sort(key=lambda x: x['datetime'])

    # --- 5. CLEAN & SAVE (Append Mode) ---
    if not results:
        print("No data found matching time window.")
        return

    headers = ['spot_name', 'datetime', 'wind_speed', 'wind_direction']
    file_exists = os.path.isfile(output_csv_path)
    
    try:
        with open(output_csv_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            # Only write header if file didn't exist
            if not file_exists: 
                writer.writeheader()
            writer.writerows(results)
            
        print(f"Saved {len(results)} rows. (First: {results[0]['datetime']})")
        
    except Exception as e:
        print(f"Error writing to CSV: {e}")

# if __name__ == "__main__":
    # Example Usage
    # fetch_wind_data(34.27, -119.29, "Ventura", "wind_forecast.csv")
    # fetch_wind_data(34.41, -119.85, "Campus Point", "wind_forecast.csv")