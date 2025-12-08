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
    
    # 3. Aggregate
    # Get all unique keys
    all_keys = set(swell_data.keys()) | set(wind_data.keys())
    
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
        
        daily_rows.append({
            'spot_name': spot,
            'date': date,
            'swell_height': f"{avg_height:.2f}",
            'swell_period': f"{avg_period:.1f}",
            'swell_direction': f"{median_dir:.0f}",
            'avg_wind_speed': f"{avg_wind_speed:.1f}",
            'median_wind_speed': f"{median_wind_speed:.1f}",
            'wind_direction': f"{median_wind_dir:.0f}",
            'tide_height': "1.0m" # Dummy
        })
        
    # Sort by date then spot
    daily_rows.sort(key=lambda x: (x['date'], x['spot_name']))
    
    # Write Output
    headers = [
        'spot_name', 'date', 
        'swell_height', 'swell_period', 'swell_direction',
        'avg_wind_speed', 'median_wind_speed', 'wind_direction',
        'tide_height'
    ]
    
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(daily_rows)
        
    print(f"Aggregated daily forecast saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    aggregate_data()
