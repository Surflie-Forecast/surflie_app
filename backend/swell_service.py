import copernicusmarine
import xarray as xr
import pandas as pd
import numpy as np
import datetime
import csv
import os
import pytz

# --- CONFIGURATION ---
# Copernicus credentials should be set as environment variables
# For GitHub Actions: Set as repository secrets (COPERNICUS_USERNAME, COPERNICUS_PASSWORD)
# For local development: Set in your shell or .env file
USERNAME = os.environ.get('COPERNICUS_USERNAME') 
PASSWORD = os.environ.get('COPERNICUS_PASSWORD')

def initialize_copernicus():
    """Checks for credentials and performs auto-login if needed."""
    # Validate credentials are set
    if not USERNAME or not PASSWORD:
        raise EnvironmentError(
            "Copernicus credentials not found. Please set environment variables:\n"
            "  - COPERNICUS_USERNAME\n"
            "  - COPERNICUS_PASSWORD\n"
            "For GitHub Actions, add these as repository secrets.\n"
            "For local development, export them in your shell or use a .env file."
        )
    
    creds_path = os.path.expanduser("~/.copernicusmarine/.copernicusmarine-credentials")
    
    if os.path.exists(creds_path):
        print("✓ Copernicus credentials detected.")
        return

    print("Credential file not found. Attempting auto-login...")
    try:
        # Attempt to login using the provided credentials
        copernicusmarine.login(username=USERNAME, password=PASSWORD)
        print("✓ Successfully logged in to Copernicus Marine.")
    except Exception as e:
        # CRITICAL: Raise exception to fail the pipeline if login fails
        raise RuntimeError(f"Copernicus Login Failed: {e}. Check your COPERNICUS_USERNAME and COPERNICUS_PASSWORD secrets.")

def get_smart_snap_pixel(ds, target_lat, target_lon):
    """Finds the nearest VALID (water) pixel to the target lat/lon."""
    
    # 1. Calculate Euclidean distance to all pixels in the subset
    dist = np.sqrt((ds.latitude - target_lat)**2 + (ds.longitude - target_lon)**2)
    
    # 2. Identify valid pixels (using the first time step as a mask)
    mask_var = ds['VHM0_SW1'].isel(time=0)
    valid_mask = ~np.isnan(mask_var)
    
    # 3. Mask land pixels (set distance to infinity)
    masked_dist = dist.where(valid_mask, np.inf)
    
    # 4. Find the index of the minimum distance
    min_dist_idx = masked_dist.argmin(...)
    
    # Extract the coordinate values
    nearest_lat = ds.latitude[min_dist_idx['latitude']].values
    nearest_lon = ds.longitude[min_dist_idx['longitude']].values
    
    distance_deg = masked_dist.min().values
    
    if np.isinf(distance_deg):
        raise ValueError("No valid water pixels found (point is too far inland).")
        
    print(f"  -> Snapped to water pixel: {float(nearest_lat):.4f}, {float(nearest_lon):.4f} (~{distance_deg*111:.2f} km away)")
    
    return ds.sel(latitude=nearest_lat, longitude=nearest_lon)

def fetch_swell_data(lat, lon, name, output_csv_path):
    # --- 1. TIMEZONE & DATE SETUP ---
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    # Define Start (Midnight Today LA) and End (Midnight + 10 Days)
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=10)
    
    # Request Buffer (-6 hours) to account for timezone diffs during fetch
    req_start_utc = (start_of_today_la - datetime.timedelta(hours=6)).astimezone(datetime.timezone.utc)
    req_end_utc = end_of_window_la.astimezone(datetime.timezone.utc)
    
    print(f"Fetching swell data for {name} ({lat}, {lon})...")

    # --- 2. FETCH DATA (Copernicus API v1.0+) ---
    buffer_deg = 0.2 
    temp_nc = f"temp_forecast_{name.replace(' ', '_')}.nc"
    
    try:
        copernicusmarine.subset(
            dataset_id="cmems_mod_glo_wav_anfc_0.083deg_PT3H-i",
            variables=["VHM0_SW1", "VTM01_SW1", "VMDR_SW1"],
            minimum_longitude=lon - buffer_deg,
            maximum_longitude=lon + buffer_deg,
            minimum_latitude=lat - buffer_deg,
            maximum_latitude=lat + buffer_deg,
            start_datetime=req_start_utc.strftime('%Y-%m-%d %H:%M:%S'),
            end_datetime=req_end_utc.strftime('%Y-%m-%d %H:%M:%S'),
            output_filename=temp_nc,
            overwrite=True
        )
        
        # DEBUG: Check file size
        if os.path.exists(temp_nc):
            size_kb = os.path.getsize(temp_nc) / 1024
            print(f"  -> File downloaded. Size: {size_kb:.2f} KB")
            if size_kb < 1:
                raise RuntimeError(f"Downloaded file is empty or too small ({size_kb:.2f} KB)")
        else:
             raise RuntimeError("Downloaded file not found on disk.")

        # Open the downloaded NetCDF file - Try h5netcdf first, then default
        try:
            ds = xr.open_dataset(temp_nc, engine='h5netcdf')
        except Exception:
            print("  -> 'h5netcdf' engine failed, retrying with default/netcdf4...")
            ds = xr.open_dataset(temp_nc)
        
    except Exception as e:
        print(f"  X Download/Open Failed: {e}")
        # IMPORTANT: Propagate error to fetch_data.py to fail the pipeline
        raise e

    # --- 3. SMART SNAP ---
    try:
        point_ds = get_smart_snap_pixel(ds, lat, lon)
    except ValueError as e:
        print(f"  X Smart Snap Failed: {e}")
        ds.close()
        if os.path.exists(temp_nc): os.remove(temp_nc)
        return

    # Convert to DataFrame
    df = point_ds.to_dataframe().reset_index()
    
    # Cleanup: Close file and delete temp
    ds.close()
    if os.path.exists(temp_nc):
        os.remove(temp_nc)

    # --- 4. PROCESS DATA ---
    new_rows = []
    seen_dates = set()
    
    # Sort to ensure chronological order
    df = df.sort_values('time')

    for _, row in df.iterrows():
        # Handle potential NaNs
        if pd.isna(row['VHM0_SW1']): continue
            
        # Convert UTC to Local Time (LA)
        dt_utc = row['time'].replace(tzinfo=datetime.timezone.utc)
        dt_la = dt_utc.astimezone(la_tz)
        date_str = dt_la.strftime('%Y-%m-%d %H:%M:%S')

        # Filter: Strictly Today onwards
        if dt_la >= start_of_today_la:
            if date_str not in seen_dates:
                new_rows.append({
                    'spot_name': name,
                    'datetime': date_str,
                    'swell_height': round(float(row['VHM0_SW1']), 2),
                    'swell_period': round(float(row['VTM01_SW1']), 1),
                    'swell_direction': int(round(row['VMDR_SW1']))
                })
                seen_dates.add(date_str)

    # --- 5. PERSISTENCE FILL (Fill late night gaps) ---
    if new_rows:
        last_row = new_rows[-1]
        last_dt = datetime.datetime.strptime(last_row['datetime'], '%Y-%m-%d %H:%M:%S')
        target_end_of_day = last_dt.replace(hour=22, minute=0, second=0)
        
        current_dt = last_dt
        while current_dt < target_end_of_day:
            current_dt += datetime.timedelta(hours=3)
            if current_dt.strftime('%Y-%m-%d %H:%M:%S') not in seen_dates:
                new_rows.append({
                    'spot_name': name,
                    'datetime': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'swell_height': last_row['swell_height'],
                    'swell_period': last_row['swell_period'],
                    'swell_direction': last_row['swell_direction']
                })

    # --- 6. UPSERT TO CSV ---
    headers = ['spot_name', 'datetime', 'swell_height', 'swell_period', 'swell_direction']
    final_rows = []
    
    # A. Read existing data (keep OTHER spots)
    if os.path.exists(output_csv_path):
        with open(output_csv_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['spot_name'] != name:
                    final_rows.append(row)
    
    # B. Add NEW data
    final_rows.extend(new_rows)
    
    # C. Write result
    with open(output_csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(final_rows)
    
    print(f"  -> Success. Updated {len(new_rows)} rows for {name} to {output_csv_path}")

# if __name__ == "__main__":
#     initialize_copernicus()
#     # Example usage for testing
#     fetch_swell_data(34.41, -119.85, "Campus Point", "test_forecast.csv")