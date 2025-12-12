import os
import datetime
import swell_service
import wind_service
import tide_service
import aggregate_daily
import csv # Added for csv.DictReader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, '../data')
SWELL_FILE = os.path.join(DATA_DIR, 'swell_raw.csv')
WIND_FILE = os.path.join(DATA_DIR, 'wind_raw.csv')
TIDE_FILE = os.path.join(DATA_DIR, 'tide_raw.csv')
LOCATIONS_FILE = os.path.join(DATA_DIR, 'locations.csv')

def fetch_all_data():
    print(f"[{datetime.datetime.now()}] Starting data pipeline...")
    
    # 1. Initialize Copernicus
    try:
        swell_service.initialize_copernicus()
    except Exception as e:
        print(f"Copernicus Init failed: {e}")
        return



    # 2. Clear old raw files
    if os.path.exists(SWELL_FILE):
        os.remove(SWELL_FILE)
    if os.path.exists(WIND_FILE):
        os.remove(WIND_FILE)
    if os.path.exists(TIDE_FILE):
        os.remove(TIDE_FILE)
        
    # 3. Fetch data for each spot
    print(f"Reading locations from {LOCATIONS_FILE}...")
    try:
        with open(LOCATIONS_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['site_name']
                lat = float(row['latitude'])
                lon = float(row['longitude'])
                
                print(f"\nProcessing {name}...")
                
                # Fetch Swell
                try:
                    swell_service.fetch_swell_data(lat, lon, name, SWELL_FILE)
                except Exception as e:
                    print(f"Error fetching swell for {name}: {e}")
                    
                # Fetch Wind
                try:
                    wind_service.fetch_wind_data(lat, lon, name, WIND_FILE)
                except Exception as e:
                    print(f"Error fetching wind for {name}: {e}")

                # Fetch Tide
                try:
                    tide_service.fetch_tide_data(lat, lon, name, TIDE_FILE)
                except Exception as e:
                    print(f"Error fetching tide for {name}: {e}")

    except FileNotFoundError:
        print(f"Error: Locations file not found at {LOCATIONS_FILE}")
        return
    except Exception as e: # Added general exception for CSV processing errors
        print(f"Error processing locations file: {e}")
        import traceback
        traceback.print_exc()
        return

    # 4. Aggregate
    try:
        aggregate_daily.aggregate_data()
    except Exception as e:
        print(f"Error aggregating data: {e}")

    print("Data pipeline completed.")

    # 5. Final Verification
    # If files are empty or missing, fail the pipeline so GitHub Actions turns RED
    error_found = False
    
    if not os.path.exists(SWELL_FILE) or os.path.getsize(SWELL_FILE) < 100:
        print(f"❌ ERROR: Swell file is missing or empty: {SWELL_FILE}")
        error_found = True
        
    if not os.path.exists(WIND_FILE) or os.path.getsize(WIND_FILE) < 100:
        print(f"❌ ERROR: Wind file is missing or empty: {WIND_FILE}")
        error_found = True

    if error_found:
        raise RuntimeError("Pipeline failed: One or more data files are empty.")

if __name__ == "__main__":
    fetch_all_data()
