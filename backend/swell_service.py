import ee
import datetime
import csv
import os
import pytz

def initialize_gee():
    try:
        credentials_file = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if credentials_file and os.path.exists(credentials_file):
            ee.Initialize(ee.ServiceAccountCredentials(None, key_file=credentials_file))
        else:
            ee.Initialize()
        print("✓ GEE initialized")
    except Exception as e:
        print(f"GEE Init failed: {e}")
        raise e

def fetch_swell_data(lat, lon, name, output_csv_path):
    # --- 1. TIMEZONE & DATE SETUP ---
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    # Define exact Start (Midnight Today LA) and End (Midnight + 6 Days)
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=12)
    
    # Request Buffer (-4 hours) to catch early model runs
    buffer_hours = 4
    req_start_utc = (start_of_today_la - datetime.timedelta(hours=buffer_hours)).astimezone(datetime.timezone.utc)
    req_end_utc = end_of_window_la.astimezone(datetime.timezone.utc)
    
    print(f"Fetching swell data for {name}...")

    # --- 2. FETCH COLLECTION ---
    collection_id = 'COPERNICUS/MARINE/WAV/ANFC_0_083DEG_PT3H'
    point = ee.Geometry.Point([lon, lat])
    
    collection = ee.ImageCollection(collection_id) \
        .filterDate(req_start_utc, req_end_utc) \
        .filterBounds(point) \
        .select(['VHM0_SW1', 'VTM01_SW1', 'VMDR_SW1'])

    # --- 3. SMART SNAP (Find nearest ocean pixel) ---
    # Attempt to find data within 30km
    reference_image = collection.first().select('VHM0_SW1')
    available_points = reference_image.sample(
        region=point.buffer(30000), 
        scale=9000, 
        geometries=True
    )
    
    if available_points.size().getInfo() > 0:
        # Snap to closest valid point
        target_point = available_points.first().geometry()
    else:
        # Fallback to requested point (might be null if on land)
        target_point = point

    # --- 4. EXTRACT DATA ---
    def extract_data(image):
        time_millis = image.get('system:time_start')
        stats = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=target_point, scale=9000)
        return ee.Feature(None, {
            'time_millis': time_millis,
            'swell_height': stats.get('VHM0_SW1'),
            'swell_period': stats.get('VTM01_SW1'),
            'swell_direction': stats.get('VMDR_SW1')
        })

    features = collection.map(extract_data).getInfo()['features']
    
    # --- 5. PROCESS NEW DATA ---
    new_rows = []
    seen_dates = set()

    for f in features:
        props = f['properties']
        if props.get('swell_height') is not None:
            # Convert UTC millis to LA Datetime
            dt_utc = datetime.datetime.fromtimestamp(props['time_millis'] / 1000.0, tz=datetime.timezone.utc)
            dt_la = dt_utc.astimezone(la_tz)
            date_str = dt_la.strftime('%Y-%m-%d %H:%M:%S')

            # Filter 1: Strictly Today onwards (discard buffer)
            if dt_la >= start_of_today_la:
                # Filter 2: Deduplicate time steps
                if date_str not in seen_dates:
                    new_rows.append({
                        'spot_name': name,
                        'datetime': date_str,
                        'swell_height': round(props['swell_height'], 2),
                        'swell_period': round(props['swell_period'], 1),
                        'swell_direction': int(props['swell_direction'])
                    })
                    seen_dates.add(date_str)

    new_rows.sort(key=lambda x: x['datetime'])

    # --- 6. PERSISTENCE FILL (Fix truncation) ---
    # GEE wave models often end at 18:00 or 21:00 UTC, leaving late night 
    # hours missing in local time. We fill the last few hours of the 
    # LAST DAY ONLY by persisting the final value.
    if new_rows:
        last_row = new_rows[-1]
        last_dt = datetime.datetime.strptime(last_row['datetime'], '%Y-%m-%d %H:%M:%S')
        
        # Define target end time: 22:00 of the last day we have data for
        # (or the 9th day, but let's just complete the day of the last row)
        target_end_of_day = last_dt.replace(hour=22, minute=0, second=0)
        
        current_dt = last_dt
        while current_dt < target_end_of_day:
            current_dt += datetime.timedelta(hours=3)
            print(f"  -> Filling missing slot: {current_dt} with persistence")
            new_rows.append({
                'spot_name': name,
                'datetime': current_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'swell_height': last_row['swell_height'],
                'swell_period': last_row['swell_period'],
                'swell_direction': last_row['swell_direction']
            })

    # --- 7. UPSERT LOGIC (Update specific spot, keep others) ---
    headers = ['spot_name', 'datetime', 'swell_height', 'swell_period', 'swell_direction']
    final_rows = []
    
    # A. Read existing file if it exists
    if os.path.exists(output_csv_path):
        with open(output_csv_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            # KEEP rows that belong to OTHER spots
            # DISCARD rows that belong to CURRENT spot (we are updating it)
            for row in reader:
                if row['spot_name'] != name:
                    final_rows.append(row)
    
    # B. Add the NEW data for this spot
    final_rows.extend(new_rows)
    
    # C. Write everything back to the file
    with open(output_csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(final_rows)
    
    print(f"  -> Updated {name}: {len(new_rows)} rows (Total in file: {len(final_rows)})")

if __name__ == "__main__":
    initialize_gee()
    
    # --- Example of Consecutive Runs ---
    # This will result in ONE file with both locations. 
    # If you run it again, it updates them without duplicating rows.
    
    # fetch_swell_data(34.27, -119.29, "Ventura", "forecast.csv")
    # fetch_swell_data(34.03, -118.77, "Malibu", "forecast.csv")