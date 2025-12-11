import ee
import datetime
import math
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

def fetch_wind_data(lat, lon, name, output_csv_path):
    # --- 1. TIMEZONE SETUP ---
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    # Define exact Start/End of the requested days
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=9)
    
    # Calculate GEE Request Window (UTC) with a BUFFER
    # We ask GEE for 3 hours BEFORE midnight to ensure we don't miss edge cases
    # (e.g., if a timestamp is 23:59:59 or if we need to interpolate later)
    buffer_hours = 3
    
    req_start_utc = (start_of_today_la - datetime.timedelta(hours=buffer_hours)).astimezone(datetime.timezone.utc)
    req_end_utc = end_of_window_la.astimezone(datetime.timezone.utc)

    print(f"Target Window (LA): {start_of_today_la} to {end_of_window_la}")
    
    # --- 2. SELECT FORECAST RUN ---
    collection_id = 'ECMWF/NRT_FORECAST/IFS/OPER'
    
    # Find the 00:00 UTC run for the current calendar date
    run_date_str = start_of_today_la.strftime('%Y-%m-%d')
    target_run_start = datetime.datetime.strptime(run_date_str, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
    target_run_end = target_run_start + datetime.timedelta(minutes=1)

    raw_collection = ee.ImageCollection(collection_id)
    forecast_run = raw_collection.filterDate(target_run_start, target_run_end)
    
    if forecast_run.size().getInfo() == 0:
        print(f"Run for {run_date_str} not ready. Using previous day...")
        target_run_start -= datetime.timedelta(days=1)
        target_run_end -= datetime.timedelta(days=1)
        forecast_run = raw_collection.filterDate(target_run_start, target_run_end)

    # Get enough forecast hours to cover the timezone offset + 6 days
    collection = forecast_run.filter(ee.Filter.lt('forecast_hours', 150))

    # --- 3. PROCESS IMAGES ---
    def process_wind(image):
        u = image.select('u_component_of_wind_10m_sfc')
        v = image.select('v_component_of_wind_10m_sfc')
        
        speed = u.hypot(v).rename('wind_speed')
        radToDeg = 180 / math.pi
        direction = u.atan2(v).multiply(radToDeg).add(180).mod(360).rename('wind_direction')
        
        # Add timestamp and variables
        return image.addBands([speed, direction]) \
                    .set('system:time_start', image.get('forecast_time'))

    # Filter processed images by our Buffered UTC window
    processed = collection.map(process_wind) \
                          .filter(ee.Filter.date(req_start_utc, req_end_utc))

    # --- 4. EXTRACT DATA ---
    point = ee.Geometry.Point([lon, lat])
    # Snap logic (simplified for reliability)
    sample_image = processed.first()
    # If buffer found no images (very unlikely), exit
    if not sample_image:
         print("No images found in window.")
         return

    # Using reduceRegion directly on the point (efficient for single point)
    def extract_data(image):
        stats = image.reduceRegion(
            reducer=ee.Reducer.mean(), 
            geometry=point, 
            scale=11000  # ECMWF resolution ~9-11km
        )
        return ee.Feature(None, {
            'time_millis': image.get('system:time_start'),
            'wind_speed': stats.get('wind_speed'),
            'wind_direction': stats.get('wind_direction')
        })

    features = processed.map(extract_data).getInfo()['features']

    # --- 5. CLEAN & SAVE ---
    results = []
    for f in features:
        props = f['properties']
        if props.get('wind_speed') is not None:
            # Convert to LA Time
            dt_utc = datetime.datetime.fromtimestamp(props['time_millis'] / 1000.0, tz=datetime.timezone.utc)
            dt_la = dt_utc.astimezone(la_tz)
            
            # STRICT FILTERING: 
            # We fetched a buffer (-3h), but now we only save rows that are 
            # actually >= Today's Midnight.
            # (Remove this 'if' statement if you prefer to see the hours leading up to midnight)
            if dt_la >= start_of_today_la:
                results.append({
                    'spot_name': name,
                    'datetime': dt_la.strftime('%Y-%m-%d %H:%M:%S'),
                    'wind_speed': round(float(props['wind_speed']) * 1.94384, 2),
                    'wind_direction': int(props['wind_direction'])
                })

    results.sort(key=lambda x: x['datetime'])
    
    # Save
    headers = ['spot_name', 'datetime', 'wind_speed', 'wind_direction']
    file_exists = os.path.isfile(output_csv_path)
    
    with open(output_csv_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists: writer.writeheader()
        writer.writerows(results)
    
    print(f"Saved {len(results)} rows. (First: {results[0]['datetime'] if results else 'None'})")

if __name__ == "__main__":
    initialize_gee()
    # fetch_wind_data(34.27, -119.29, "Ventura", "wind_buffered.csv")