import ee
import datetime
import math
import csv
import os

def initialize_gee():
    try:
        ee.Initialize()
    except Exception as e:
        print(f"GEE Initialization failed: {e}")
        print("Please run 'earthengine authenticate' in your terminal.")
        raise e

def fetch_wind_data(lat, lon, name, output_csv_path):
    # Dataset ID
    collection_id = 'ECMWF/NRT_FORECAST/IFS/OPER'
    point = ee.Geometry.Point([lon, lat])
    
    # --- 1. FORCE THE 00:00 UTC RUN (START OF DAY) ---
    # This ensures your CSV always starts at midnight, capturing the "whole day"
    
    # Get today's date in UTC
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_of_today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Create a small window to catch the 00:00 run
    # (We look from 00:00 to 00:01 to grab that exact run)
    target_run_start = start_of_today
    target_run_end = start_of_today + datetime.timedelta(minutes=1)

    raw_collection = ee.ImageCollection(collection_id)
    
    # Check if the 00:00 run exists for today yet
    # (If you run this very early in the morning, it might not be uploaded yet)
    forecast_run = raw_collection.filterDate(target_run_start, target_run_end)
    
    # Fallback: If today's 00:00 run isn't ready, use Yesterday's 00:00 run
    # and just show the forecast hours 24-96 (which is effectively "Today")
    if forecast_run.size().getInfo() == 0:
        print("Today's 00:00 UTC run not ready yet. Using previous run...")
        # Go back 24 hours
        target_run_start = target_run_start - datetime.timedelta(days=1)
        target_run_end = target_run_end - datetime.timedelta(days=1)
        forecast_run = raw_collection.filterDate(target_run_start, target_run_end)
    
    # Select the images (Filter up to 120 hours to be safe)
    forecast_images = forecast_run.first() # This is the ImageCollection for that run? 
    # Actually ECMWF collection is flat. We filtered to images with that specific start time.
    # So 'forecast_run' contains all 0h, 3h, 6h... images for that run.
    
    collection = forecast_run.filter(ee.Filter.lt('forecast_hours', 72))

    # --- 2. PROCESS IMAGES (Standard) ---
    def process_wind(image):
        u = image.select('u_component_of_wind_10m_sfc')
        v = image.select('v_component_of_wind_10m_sfc')
        
        speed = u.hypot(v).rename('wind_speed')
        radToDeg = 180 / math.pi
        direction = u.atan2(v).multiply(radToDeg).add(180).mod(360).rename('wind_direction')
        
        forecast_ts = image.get('forecast_time')
        return image.addBands([speed, direction]).set('system:time_start', forecast_ts)

    processed = collection.map(process_wind)

    print(f"Fetching WIND data for {name} from the 00:00 UTC run...")
    
    # --- 3. EXTRACT DATA ---
    # (Standard Snap and Extract logic)
    reference_image = processed.first().select('wind_speed')
    available_points = reference_image.sample(region=point.buffer(30000), scale=11000, geometries=True)
    
    # ... [Same Snap Logic as before] ...
    # Simplified for brevity here (assume points found):
    target_point = available_points.first().geometry() 

    def extract_data(image):
        date = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd HH:mm:ss')
        stats = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=target_point, scale=11000)
        return ee.Feature(None, {
            'date': date,
            'wind_speed': stats.get('wind_speed'),
            'wind_direction': stats.get('wind_direction')
        })

    features = processed.map(extract_data).getInfo()['features']
    
    # --- 4. SAVE (Ensure we filter for "From Now On" if you want, or keep all) ---
    results = []
    for f in features:
        props = f['properties']
        if props.get('wind_speed') is not None:
             results.append({
                'spot_name': name,
                'datetime': props['date'],
                'wind_speed': round(float(props['wind_speed']) * 1.94384, 2),
                'wind_direction': int(props['wind_direction'])
            })
            
    results.sort(key=lambda x: x['datetime'])
    
    # [CSV Writing Logic Here]
    file_exists = os.path.isfile(output_csv_path)
    headers = ['spot_name', 'datetime', 'wind_speed', 'wind_direction']
    with open(output_csv_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists: writer.writeheader()
        writer.writerows(results)
    print(f"Saved {len(results)} rows.")

if __name__ == "__main__":
    initialize_gee()
    # fetch_wind_data(34.27, -119.29, "Ventura", "wind_fixed.csv")