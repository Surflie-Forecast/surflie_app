import csv
import statistics
import datetime
import os
from collections import defaultdict

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SWELL_FILE = os.path.join(BASE_DIR, '../data/swell_raw.csv')
WIND_FILE = os.path.join(BASE_DIR, '../data/wind_raw.csv')
OUTPUT_FILE = os.path.join(BASE_DIR, '../data/daily_forecast.csv')

def parse_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def aggregate_data():
    print("Starting daily aggregation...")
    
    # 1. Read Swell Data
    swell_data = defaultdict(list)
    if os.path.exists(SWELL_FILE):
        with open(SWELL_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Key: Spot + Date
                try:
                    dt = row['datetime'].split(' ')[0] # YYYY-MM-DD
                    key = (row['spot_name'], dt)
                    swell_data[key].append(row)
                except Exception as e:
                    print(f"Skipping bad swell row: {row} - {e}")

    # 2. Read Wind Data
    wind_data = defaultdict(list)
    if os.path.exists(WIND_FILE):
        with open(WIND_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = row['datetime'].split(' ')[0]
                    key = (row['spot_name'], dt)
                    wind_data[key].append(row)
                except Exception as e:
                    print(f"Skipping bad wind row: {row} - {e}")
    
    # 3. Read Tide Data (Raw & Peaks)
    tide_raw_data = defaultdict(list)
    tide_peaks_data = defaultdict(list)
    
    TIDE_RAW_FILE = os.path.join(BASE_DIR, '../data/tide_raw.csv')
    TIDE_PEAKS_FILE = os.path.join(BASE_DIR, '../data/tide_peaks.csv')
    
    # Load Raw
    if os.path.exists(TIDE_RAW_FILE):
        with open(TIDE_RAW_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = row['datetime'].split(' ')[0]
                    key = (row['spot_name'], dt)
                    tide_raw_data[key].append(row)
                except: pass

    # Load Peaks
    if os.path.exists(TIDE_PEAKS_FILE):
        with open(TIDE_PEAKS_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = row['datetime'].split(' ')[0]
                    key = (row['spot_name'], dt)
                    tide_peaks_data[key].append(row)
                except: pass

    # 4. Aggregate
    # ONLY create rows for dates that have swell data (8-day limit)
    # Wind and tide data may extend further, but we ignore those extra days
    all_keys = set(swell_data.keys())
    
    daily_rows = []
    
    for spot, date in all_keys:
        s_rows = swell_data.get((spot, date), [])
        w_rows = wind_data.get((spot, date), [])
        
        # Swell Aggregates
        avg_height = 0.0
        avg_period = 0.0
        median_dir = 0.0
        
        if s_rows:
            heights = [parse_float(r['swell_height']) for r in s_rows if parse_float(r['swell_height']) is not None]
            periods = [parse_float(r['swell_period']) for r in s_rows if parse_float(r['swell_period']) is not None]
            dirs = [parse_float(r['swell_direction']) for r in s_rows if parse_float(r['swell_direction']) is not None]
            
            if heights: avg_height = statistics.mean(heights)
            if periods: avg_period = statistics.mean(periods)
            if dirs: median_dir = statistics.median(dirs)

        # Wind Aggregates
        avg_wind_speed = 0.0
        median_wind_speed = 0.0
        median_wind_dir = 0.0
        
        if w_rows:
            speeds = [parse_float(r['wind_speed']) for r in w_rows if parse_float(r['wind_speed']) is not None]
            dirs = [parse_float(r['wind_direction']) for r in w_rows if parse_float(r['wind_direction']) is not None]
            
            if speeds:
                avg_wind_speed = statistics.mean(speeds)
                median_wind_speed = statistics.median(speeds)
            if dirs:
                median_wind_dir = statistics.median(dirs)
        
        # Tide Aggregates (Prefer Peaks, Fallback to Raw)
        min_tide_val = 999.0
        max_tide_val = -999.0
        min_tide_time = ""
        max_tide_time = ""
        
        # Use Peaks if available for this day
        peaks_rows = tide_peaks_data.get((spot, date), [])
        raw_rows = tide_raw_data.get((spot, date), [])
        
        use_source = peaks_rows if peaks_rows else raw_rows
        
        if use_source:
            for r in use_source:
                val = parse_float(r['tide_height'])
                if val is not None:
                    # Parse time (HH:MM) from datetime "YYYY-MM-DD HH:MM:SS"
                    time_part = r['datetime'].split(' ')[1][:5]
                    
                    if val < min_tide_val:
                        min_tide_val = val
                        min_tide_time = time_part
                    if val > max_tide_val:
                        max_tide_val = val
                        max_tide_time = time_part
        
        if min_tide_val == 999.0: min_tide_val = 0.0
        if max_tide_val == -999.0: max_tide_val = 0.0
        
        daily_rows.append({
            'spot_name': spot,
            'date': date,
            'swell_height': f"{avg_height:.2f}",
            'swell_period': f"{avg_period:.1f}",
            'swell_direction': f"{median_dir:.0f}",
            'avg_wind_speed': f"{avg_wind_speed:.1f}",
            'median_wind_speed': f"{median_wind_speed:.1f}",
            'wind_direction': f"{median_wind_dir:.0f}",
            'tide_min': f"{min_tide_val:.2f}",
            'tide_min_time': min_tide_time,
            'tide_max': f"{max_tide_val:.2f}",
            'tide_max_time': max_tide_time
        })
        
    # Sort by date then spot
    daily_rows.sort(key=lambda x: (x['date'], x['spot_name']))
    
    # Write Output
    headers = [
        'spot_name', 'date', 
        'swell_height', 'swell_period', 'swell_direction',
        'avg_wind_speed', 'median_wind_speed', 'wind_direction',
        'tide_min', 'tide_min_time', 'tide_max', 'tide_max_time'
    ]
    
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(daily_rows)
        
    print(f"Aggregated daily forecast saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    aggregate_data()
