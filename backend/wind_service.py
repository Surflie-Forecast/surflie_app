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
    # CRITICAL UPDATE: For West Coast (PST/PDT), "Today" starts at ~07:00 or 08:00 UTC.
    # The 12z run (starts 12:00 UTC) and 18z run (starts 18:00 UTC) are TOO LATE 
    # to provide data for the early morning of "Today".
    # Therefore, we MUST cap the cycle at '06z' (starts 06:00 UTC) to ensure full daily coverage.
    
    if hour < 4: 
        # Late night UTC (e.g. 02:00). 06z yesterday is definitely out. 18z yesterday is safer/newer?
        # Actually 18z yesterday starts 18:00 prev day. Good coverage.
        # But let's stick to the standard logic for previous day:
        cycle = '18'
        date_str = (now_utc - datetime.timedelta(days=1)).strftime('%Y%m%d')
    elif hour < 10: 
        # 04:00 - 10:00 UTC. 00z (starts 00:00) is likely ready (avail ~04:00).
        cycle = '00'
    else:
        # 10:00+ UTC. 06z (starts 06:00) is likely ready (avail ~10:00).
        # We STOP here. Even if it is 20:00 UTC and 12z is ready, we DON'T use it 
        # because it starts too late (12:00 UTC) for our 08:00 UTC target.
        cycle = '06'

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

if __name__ == "__main__":
    # Example Usage
    # fetch_wind_data(34.27, -119.29, "Ventura", "wind_forecast.csv")
    # fetch_wind_data(34.41, -119.85, "Campus Point", "wind_forecast.csv")