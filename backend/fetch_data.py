import os
import datetime
import swell_service
import wind_service
import aggregate_daily

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SWELL_FILE = os.path.join(BASE_DIR, '../data/swell_raw.csv')
WIND_FILE = os.path.join(BASE_DIR, '../data/wind_raw.csv')

def fetch_all_data():
    print(f"[{datetime.datetime.now()}] Starting data pipeline...")
    
    # 1. Initialize GEE
    try:
        swell_service.initialize_gee()
    except Exception as e:
        print(f"GEE Init failed: {e}")
        return

    # 2. Clear old raw files
    if os.path.exists(SWELL_FILE):
        os.remove(SWELL_FILE)
    if os.path.exists(WIND_FILE):
        os.remove(WIND_FILE)
        
    # 3. Fetch Swell & Wind for all spots
    # We use swell_service.SPOTS as the source of truth
    for key in swell_service.SPOTS.keys():
        try:
            swell_service.fetch_swell_data(key, SWELL_FILE)
            wind_service.get_wind_data(key, WIND_FILE)
        except Exception as e:
            print(f"Error fetching for {key}: {e}")
            import traceback
            traceback.print_exc()

    # 4. Aggregate
    try:
        aggregate_daily.aggregate_data()
    except Exception as e:
        print(f"Error aggregating data: {e}")

    print("Data pipeline completed.")

if __name__ == "__main__":
    fetch_all_data()
