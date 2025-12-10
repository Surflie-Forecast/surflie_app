import requests
import math
import datetime
import csv
import os
import pytz

# --- 1. GEOMETRY UTILITIES ---

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance (in miles) between two points 
    on the earth (specified in decimal degrees).
    """
    R = 3958.8  # Earth radius in miles
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) * math.sin(dlat / 2) +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) * math.sin(dlon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# --- 2. STATION DISCOVERY ---

def find_nearest_stations(target_lat, target_lon, limit=5):
    """
    Fetches all active tide prediction stations from NOAA and returns
    a list of the closest stations (ID, Name, Distance).
    """
    # URL to get all stations that offer Tide Predictions
    stations_url = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions"
    
    try:
        # Add User-Agent to avoid blocking
        headers_ua = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'}
        response = requests.get(stations_url, headers=headers_ua)
        response.raise_for_status()
        data = response.json()
        
        stations = data.get('stations', [])
        candidates = []
        
        for s in stations:
            if 'lat' not in s or 'lng' not in s:
                continue
                
            s_lat = float(s['lat'])
            s_lon = float(s['lng'])
            
            dist = haversine_distance(target_lat, target_lon, s_lat, s_lon)
            candidates.append({
                'id': s['id'], 
                'name': s['name'], 
                'dist': dist
            })
        
        # Sort by distance
        candidates.sort(key=lambda x: x['dist'])
        
        return candidates[:limit]

    except Exception as e:
        print(f"Error finding stations: {e}")
        return []

# --- 3. DATA FETCHING ---

def fetch_tide_data(lat, lon, name, output_csv_path):
    # Match timezone logic from swell_service.py
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    # Define exact Start (Midnight Today LA) and End (Midnight + 3 Days)
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=3)
    
    # 1. Find Stations
    print(f"Finding nearest tide stations for {name}...")
    candidates = find_nearest_stations(lat, lon, limit=3)
    
    if not candidates:
        print(f"Could not find any tide stations for {name}")
        return
    
    # 2. Try Candidates in Order
    success = False
    
    for station in candidates:
        station_id = station['id']
        station_name = station['name']
        dist = station['dist']
        print(f"  -> Trying {station_name} (ID: {station_id}, Dist: {dist:.1f} mi)...")

        url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        params = {
            "product": "predictions",
            "station": station_id,
            "begin_date": start_of_today_la.strftime("%Y%m%d"),
            "end_date": end_of_window_la.strftime("%Y%m%d"),
            "datum": "MLLW",
            "time_zone": "lst_ldt", # Local Standard/Daylight Time
            "units": "metric",      # Metric for consistency with 'm' (meters)
            # "interval": "h",      # REMOVED to get 6-min resolution
            "format": "json"
        }

        try:
            # Add User-Agent to avoid blocking
            headers_ua = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'}
            response = requests.get(url, params=params, headers=headers_ua)
            response.raise_for_status()
            data = response.json()
            
            if 'predictions' not in data:
                print(f"    x No prediction data available at {station_name}")
                continue # Try next station

            predictions = data['predictions']
            
            # 3. Process Data
            new_rows = []
            seen_dates = set()
            
            for p in predictions:
                # p['t'] is "YYYY-MM-DD HH:MM" in lst_ldt (Local Time)
                time_str = p['t']
                val = float(p['v'])
                
                # Parse naive and localize to LA
                try:
                    dt_naive = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                    dt_aware = la_tz.localize(dt_naive)
                    
                    # Filter: >= Today Midnight
                    if dt_aware >= start_of_today_la:
                        final_date_str = dt_aware.strftime('%Y-%m-%d %H:%M:%S')
                        
                        if final_date_str not in seen_dates:
                            new_rows.append({
                                'spot_name': name,
                                'datetime': final_date_str,
                                'tide_height': f"{val:.2f}"
                            })
                            seen_dates.add(final_date_str)
                except Exception as e:
                    print(f"Error parsing date {time_str}: {e}")
            
            if not new_rows:
                print(f"    x No valid future data found for {station_name}")
                continue

            new_rows.sort(key=lambda x: x['datetime'])
            
            # 4. Upsert Logic (Remove old rows for this spot, append new)
            headers = ['spot_name', 'datetime', 'tide_height']
            final_rows = []
            
            if os.path.exists(output_csv_path):
                with open(output_csv_path, 'r', newline='') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row['spot_name'] != name:
                            final_rows.append(row)
            
            final_rows.extend(new_rows)
            
            with open(output_csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(final_rows)
                
            print(f"  -> CHECK: Saved {len(new_rows)} tide rows for {name} using station {station_name}")
            success = True
            break # Stop trying other stations
            
        except Exception as e:
            print(f"    x Error fetching tide for {station_name}: {e}")
            continue
            
    if not success:
        print(f"FAILED to fetch tide data for {name} from any nearby station.")

if __name__ == "__main__":
    # Test execution
    fetch_tide_data(34.403, -119.880, "Test_Devereaux", "test_tide.csv")
