import ee
import datetime
import math
import csv
import os
def get_wind_data(lat, lon, name, output_csv_path):
    """
    Fetches 3-day wind forecast (speed/direction) for a specific spot and appends to CSV.
    """
    # Define location
    point = ee.Geometry.Point([lon, lat])
    
    # Load ECMWF Forecast
    collection = ee.ImageCollection("ECMWF/NRT_FORECAST/IFS/OPER")
    
    # 1. ISOLATE THE LATEST MODEL RUN
    latest_image = collection.sort('system:time_start', False).first()
    latest_run_time = latest_image.get('system:time_start')
    
    forecast_run = collection.filter(ee.Filter.eq('system:time_start', latest_run_time))
    
    # 2. FILTER FOR THE NEXT 3 DAYS
    next_3_days = forecast_run.filter(ee.Filter.lte('forecast_hours', 72))
    
    print(f"Fetching WIND data for {name}...")

    def process_image(image):
        u = image.select('u_component_of_wind_10m_sfc')
        v = image.select('v_component_of_wind_10m_sfc')
        
        # Calculate Speed (m/s)
        speed = u.hypot(v).rename('wind_speed')
        
        # Calculate Direction (Meteorological convention: FROM)
        radToDeg = 180 / 3.14159265359
        direction = u.atan2(v).multiply(radToDeg).add(180).mod(360).rename('wind_direction')
        
        forecast_time = image.get('forecast_time')
        
        return image.addBands([speed, direction]).set('system:time_start', forecast_time)

    processed_forecast = next_3_days.map(process_image)

    # Convert to list
    def extract_features(image):
        date = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd HH:mm:ss')
        stats = image.reduceRegion(
            reducer=ee.Reducer.first(),
            geometry=point,
            scale=5000 
        )
        return ee.Feature(None, {
            'date': date,
            'wind_speed': stats.get('wind_speed'),
            'wind_direction': stats.get('wind_direction')
        })

    features = processed_forecast.map(extract_features).getInfo()['features']
    
    results = []
    for f in features:
        props = f['properties']
        if props.get('wind_speed') is not None:
            # Convert m/s to Knots (1 m/s = 1.94384 kts)
            speed_kts = float(props['wind_speed']) * 1.94384
            
            results.append({
                'spot_name': name,
                'datetime': props['date'],
                'wind_speed': speed_kts,
                'wind_direction': float(props['wind_direction'])
            })
            
    # Append to CSV
    file_exists = os.path.isfile(output_csv_path)
    headers = ['spot_name', 'datetime', 'wind_speed', 'wind_direction']
    
    with open(output_csv_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerows(results)
    
    print(f"Saved {len(results)} wind rows directly to {output_csv_path}")

if __name__ == "__main__":
    import ee
    try:
        ee.Initialize()
    except:
        pass
    # get_wind_data("ventura", "wind_raw.csv")
