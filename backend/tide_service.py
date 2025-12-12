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

# --- 3. DATA FETCHING ---

def fetch_single_tide_type(station_id, start_date, end_date, interval, headers_ua):
    """
    Helper to fetch prediction data from NOAA for a specific interval.
    interval=None -> High Res (6-min)
    interval='hlo' -> High/Low Peaks
    """
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    params = {
        "product": "predictions",
        "station": station_id,
        "begin_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
        "datum": "MLLW",
        "time_zone": "lst_ldt",
        "units": "metric",
        "format": "json"
    }
    if interval:
        params["interval"] = interval

    response = requests.get(url, params=params, headers=headers_ua)
    response.raise_for_status()
    return response.json()

def save_tide_csv(spot_name, rows, output_path, headers):
    """
    Helper to upsert tide data into a CSV.
    """
    final_rows = []
    
    # Read existing
    if os.path.exists(output_path):
        with open(output_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['spot_name'] != spot_name:
                    final_rows.append(row)
    
    # Append new
    final_rows.extend(rows)
    
    # Write
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(final_rows)

def fetch_tide_data(lat, lon, name, output_raw_path):
    # Determines peaks path based on raw path convention
    # e.g. .../tide_raw.csv -> .../tide_peaks.csv
    base_dir = os.path.dirname(output_raw_path)
    output_peaks_path = os.path.join(base_dir, 'tide_peaks.csv')
    
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    
    start_of_today_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_window_la = start_of_today_la + datetime.timedelta(days=9)
    
    # 1. Find Stations
    print(f"Finding nearest tide stations for {name}...")
    candidates = find_nearest_stations(lat, lon, limit=3)
    
    if not candidates:
        print(f"Could not find any tide stations for {name}")
        return
    
    success = False
    
    headers_ua = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'}

    for station in candidates:
        station_id = station['id']
        station_name = station['name']
        dist = station['dist']
        print(f"  -> Trying {station_name} (ID: {station_id}, Dist: {dist:.1f} mi)...")

        try:
            # --- A. FETCH HIGH RES (6-min) ---
            data_raw = fetch_single_tide_type(station_id, start_of_today_la, end_of_window_la, None, headers_ua)
            
            if 'predictions' not in data_raw:
                print(f"    x No detail data available at {station_name}")
                continue

            # Process Raw
            rows_raw = []
            seen_dates = set()
            for p in data_raw['predictions']:
                time_str = p['t']
                val = float(p['v'])
                try:
                    dt_naive = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                    dt_aware = la_tz.localize(dt_naive)
                    if dt_aware >= start_of_today_la:
                        final_date_str = dt_aware.strftime('%Y-%m-%d %H:%M:%S')
                        if final_date_str not in seen_dates:
                            rows_raw.append({
                                'spot_name': name,
                                'datetime': final_date_str,
                                'tide_height': f"{val:.2f}"
                            })
                            seen_dates.add(final_date_str)
                except:
                    pass
            rows_raw.sort(key=lambda x: x['datetime'])

            # --- B. FETCH PEAKS (High/Low) ---
            rows_peaks = []
            try:
                data_peaks = fetch_single_tide_type(station_id, start_of_today_la, end_of_window_la, 'hilo', headers_ua)
                if 'predictions' in data_peaks:
                    for p in data_peaks['predictions']:
                        # type: 'H' or 'L'
                        t_type = p.get('type', '')
                        time_str = p['t']
                        val = float(p['v'])
                        
                        try:
                            dt_naive = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                            dt_aware = la_tz.localize(dt_naive)
                            if dt_aware >= start_of_today_la:
                                rows_peaks.append({
                                    'spot_name': name,
                                    'datetime': dt_aware.strftime('%Y-%m-%d %H:%M:%S'),
                                    'tide_height': f"{val:.2f}",
                                    'type': t_type # H or L
                                })
                        except:
                            pass
            except Exception as e:
                print(f"    ! Warning: Could not fetch peaks for {station_name} ({e}). Using raw only?")
            
            rows_peaks.sort(key=lambda x: x['datetime'])

            # --- SAVE FILES ---
            # 1. Save Raw
            save_tide_csv(name, rows_raw, output_raw_path, ['spot_name', 'datetime', 'tide_height'])
            
            # 2. Save Peaks (if any)
            if rows_peaks:
                save_tide_csv(name, rows_peaks, output_peaks_path, ['spot_name', 'datetime', 'tide_height', 'type'])
                print(f"  -> CHECK: Saved {len(rows_raw)} raw pts & {len(rows_peaks)} peaks for {name} using {station_name}")
            else:
                 print(f"  -> CHECK: Saved {len(rows_raw)} raw pts (NO PEAKS FOUND) for {name}")

            success = True
            break
            
        except Exception as e:
            print(f"    x Error fetching tide for {station_name}: {e}")
            continue
            
    if not success:
        print(f"FAILED to fetch tide data for {name} from any nearby station.")

if __name__ == "__main__":
    # Test execution
    fetch_tide_data(34.403, -119.880, "Test_Devereaux", "test_tide.csv")
