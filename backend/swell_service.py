import ee
import datetime
import csv
import os

def initialize_gee():
    """Initialize Google Earth Engine with service account or user credentials."""
    try:
        # Check if running in GitHub Actions or with service account
        credentials_file = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        
        if credentials_file and os.path.exists(credentials_file):
            # Use service account authentication (for GitHub Actions)
            print(f"Authenticating with service account: {credentials_file}")
            credentials = ee.ServiceAccountCredentials(
                email=None,  # Will be read from the JSON file
                key_file=credentials_file
            )
            ee.Initialize(credentials)
        else:
            # Use standard user authentication (for local development)
            ee.Initialize()
            
        print("✓ GEE initialized successfully")
    except Exception as e:
        print(f"GEE Initialization failed: {e}")
        print("Please run 'earthengine authenticate' in your terminal.")
        raise e

def fetch_swell_data(lat, lon, name, output_csv_path):
    """
    Fetches 3-day swell forecast for a specific spot and appends to CSV.
    """
    # Dataset ID
    collection_id = 'COPERNICUS/MARINE/WAV/ANFC_0_083DEG_PT3H'
    
    # Define the point of interest
    point = ee.Geometry.Point([lon, lat])
    
    # Time range: Now to +3 days
    now = datetime.datetime.now()
    start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + datetime.timedelta(days=3)
    
    # Filter collection
    collection = ee.ImageCollection(collection_id) \
        .filterDate(start_date, end_date) \
        .filterBounds(point) \
        .select(['VHM0_SW1', 'VTM01_SW1', 'VMDR_SW1'])
        
    print(f"Fetching swell data for {name}...")
    
    # Smart Snap: Find nearest valid ocean pixel (copied from generic service)
    reference_image = collection.first().select('VHM0_SW1')
    available_points = reference_image.sample(
        region=point.buffer(30000),
        scale=9000,
        geometries=True
    )
    
    def add_dist(feature):
        dist = feature.geometry().distance(point)
        return feature.set('dist', dist)
        
    points_with_dist = available_points.map(add_dist)
    
    count = points_with_dist.size().getInfo()
    if count == 0:
        print(f"WARNING: No valid ocean pixels found within 30km of {lat}, {lon}")
        target_point = point 
    else:
        nearest_feature = points_with_dist.sort('dist', True).first()
        target_point = nearest_feature.geometry()
        dist = nearest_feature.get('dist').getInfo()
        print(f"Snapped to valid pixel {dist:.0f}m away.")

    # Helper to extracting data from an image
    def extract_data(image):
        date = ee.Date(image.get('system:time_start')).format('YYYY-MM-dd HH:mm:ss')
        stats = image.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=target_point, 
            scale=9000
        )
        return ee.Feature(None, {
            'date': date,
            'swell_height': stats.get('VHM0_SW1'),
            'swell_period': stats.get('VTM01_SW1'),
            'swell_direction': stats.get('VMDR_SW1')
        })

    features = collection.map(extract_data).getInfo()['features']
    
    results = []
    for f in features:
        props = f['properties']
        if props.get('swell_height') is not None:
             results.append({
                'spot_name': name,
                'datetime': props['date'],
                'swell_height': props['swell_height'],
                'swell_period': props['swell_period'],
                'swell_direction': props['swell_direction']
            })

    # Append to CSV
    file_exists = os.path.isfile(output_csv_path)
    headers = ['spot_name', 'datetime', 'swell_height', 'swell_period', 'swell_direction']
    
    with open(output_csv_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerows(results)
    
    print(f"Saved {len(results)} swell rows directly to {output_csv_path}")

if __name__ == "__main__":
    initialize_gee()
    # For testing, clear file first or just append?
    # Ideally the orchestration script handles file clearing.
    # verify_run just for one spot
    # fetch_swell_data("ventura", "swell_raw.csv") # Example usage
