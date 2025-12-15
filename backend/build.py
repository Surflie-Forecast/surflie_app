#!/usr/bin/env python3
"""
Build script to convert CSV data into static JSON files for GitHub Pages deployment.
"""
import os
import json
import csv
import shutil
from datetime import datetime, timedelta
from collections import defaultdict
import pytz
from rating_utils import get_daily_rating_from_hourly

# Paths
DATA_DIR = os.path.join(os.path.dirname(__file__), '../data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '../api')

def ensure_dirs():
    """Create output directories after cleaning old JSON files."""
    # Clean old JSON files by removing and recreating directories
    forecast_dir = os.path.join(OUTPUT_DIR, 'forecast')
    hourly_dir = os.path.join(OUTPUT_DIR, 'hourly')
    
    if os.path.exists(forecast_dir):
        shutil.rmtree(forecast_dir)
        print("✓ Cleaned old forecast files")
    
    if os.path.exists(hourly_dir):
        shutil.rmtree(hourly_dir)
        print("✓ Cleaned old hourly files")
    
    # Remove spots.json if it exists
    spots_file = os.path.join(OUTPUT_DIR, 'spots.json')
    if os.path.exists(spots_file):
        os.remove(spots_file)
    
    # Recreate directories
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(forecast_dir, exist_ok=True)
    os.makedirs(hourly_dir, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, 'tide'), exist_ok=True)

def load_locations():
    """Load locations from CSV."""
    locations_file = os.path.join(DATA_DIR, 'locations.csv')
    locations = {}
    
    if os.path.exists(locations_file):
        with open(locations_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                locations[row['site_name']] = {
                    'region': row['region'],
                    'latitude': float(row['latitude']) if row.get('latitude') else None,
                    'longitude': float(row['longitude']) if row.get('longitude') else None
                }
    
    return locations

def generate_spots_json(locations):
    """Generate spots.json with list of all spots."""
    spots_data = []
    seen = set()
    
    # Read from daily_forecast.csv to get available spots
    forecast_file = os.path.join(DATA_DIR, 'daily_forecast.csv')
    print(f"DEBUG: Reading forecast from {forecast_file}")
    
    if os.path.exists(forecast_file):
        with open(forecast_file, 'r') as f:
            lines = f.readlines()
            print(f"DEBUG: Found {len(lines)} lines in CSV (including header)")
            if len(lines) > 0:
                print(f"DEBUG: Header: {lines[0].strip()}")
                
            f.seek(0) # Reset pointer
            reader = csv.DictReader(f)
            for row in reader:
                spot_name = row['spot_name']
                if spot_name not in seen:
                    loc_data = locations.get(spot_name, {})
                    spots_data.append({
                        'name': spot_name,
                        'region': loc_data.get('region', 'Unknown Region'),
                        'latitude': loc_data.get('latitude'),
                        'longitude': loc_data.get('longitude')
                    })
                    seen.add(spot_name)
    
    # Write spots.json
    output_file = os.path.join(OUTPUT_DIR, 'spots.json')
    with open(output_file, 'w') as f:
        json.dump(spots_data, f, indent=2)
    
    print(f"✓ Generated {output_file} with {len(spots_data)} spots")
    return list(seen)

def generate_forecast_json(spot_name, locations):
    """Generate forecast JSON for a specific spot."""
    forecast_file = os.path.join(DATA_DIR, 'daily_forecast.csv')
    
    if not os.path.exists(forecast_file):
        return
    
    forecast_data = []
    
    with open(forecast_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                # Parse date and generate day label using LA time
                try:
                    date_obj = datetime.strptime(row['date'], '%Y-%m-%d')
                    # Get today in LA timezone
                    la_tz = pytz.timezone('America/Los_Angeles')
                    today = datetime.now(la_tz).date()
                    
                    if date_obj.date() == today:
                        day_label = 'Today'
                    elif date_obj.date() == today + timedelta(days=1):
                        day_label = 'Tomorrow'
                    else:
                        day_label = date_obj.strftime('%A')  # Day name
                    
                    date_display = date_obj.strftime('%b %d')  # e.g., "Dec 07"
                except:
                    day_label = 'Unknown'
                    date_display = row['date']
                
                # Calculate rating and color based on hourly data (7am-7pm)
                swell_file = os.path.join(DATA_DIR, 'swell_raw.csv')
                wind_file = os.path.join(DATA_DIR, 'wind_raw.csv')
                rating, color = get_daily_rating_from_hourly(spot_name, row['date'], swell_file, wind_file)
                
                forecast_data.append({
                    'day': day_label,
                    'date': date_display,
                    'date_iso': row['date'],
                    'swell': {
                        'height': row['swell_height'],
                        'period': row['swell_period'],
                        'direction': row['swell_direction'],
                        'unit': 'm'
                    },
                    'wind': {
                        'speed': row.get('avg_wind_speed') or row.get('median_wind_speed', '0'),
                        'direction': row['wind_direction'],
                        'unit': 'kts'
                    },
                    'tide': {
                        'max': {
                            'height': row.get('tide_max', '0'),
                            'time': row.get('tide_max_time', '--:--')
                        },
                        'min': {
                            'height': row.get('tide_min', '0'),
                            'time': row.get('tide_min_time', '--:--')
                        }
                    },
                    'rating': rating,
                    'color': color
                })
    
    if forecast_data:
        output_data = {
            'spotName': spot_name,
            'region': locations.get(spot_name, {}).get('region', 'Unknown Region'),
            'forecast': forecast_data
        }
        
        # Write JSON file
        output_file = os.path.join(OUTPUT_DIR, 'forecast', f'{spot_name}.json')
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✓ Generated forecast for {spot_name}")

def generate_hourly_json(spot_name):
    """Generate hourly JSON files for a specific spot."""
    swell_file = os.path.join(DATA_DIR, 'swell_raw.csv')
    wind_file = os.path.join(DATA_DIR, 'wind_raw.csv')
    
    if not os.path.exists(swell_file) or not os.path.exists(wind_file):
        return
    
    # Read swell data
    swell_data = {}
    with open(swell_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                dt = row['datetime']
                swell_data[dt] = {
                    'height': float(row['swell_height']),
                    'period': float(row['swell_period']),
                    'direction': float(row['swell_direction'])
                }
    
    # Read wind data and combine by date
    hourly_by_date = defaultdict(list)
    
    with open(wind_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                dt = row['datetime']
                date_str = dt.split(' ')[0]  # Get YYYY-MM-DD
                
                if dt in swell_data:
                    hourly_entry = {
                        'datetime': dt,
                        'swell': swell_data[dt],
                        'wind': {
                            'speed': float(row['wind_speed']),
                            'direction': float(row['wind_direction'])
                        }
                    }
                    hourly_by_date[date_str].append(hourly_entry)
    
    # Write JSON files per date
    spot_dir = os.path.join(OUTPUT_DIR, 'hourly', spot_name)
    os.makedirs(spot_dir, exist_ok=True)
    
    for date_str, hourly_data in hourly_by_date.items():
        hourly_data.sort(key=lambda x: x['datetime'])
        output_file = os.path.join(spot_dir, f'{date_str}.json')
        
        with open(output_file, 'w') as f:
            json.dump(hourly_data, f, indent=2)
    
    if hourly_by_date:
        print(f"✓ Generated {len(hourly_by_date)} hourly files for {spot_name}")

def main():
    print("Building static JSON files from CSV data...")
    print("-" * 50)
    
    ensure_dirs()
    locations = load_locations()
    
    # Generate spots list
    spots = generate_spots_json(locations)
    
    # Generate forecast and hourly data for each spot
    for spot_name in spots:
        generate_forecast_json(spot_name, locations)
        generate_hourly_json(spot_name)
        generate_tide_json(spot_name)
    
    print("-" * 50)
    print(f"✓ Build complete! Generated JSON files in {OUTPUT_DIR}")

def generate_tide_json(spot_name):
    """Generate tide JSON file for a specific spot."""
    tide_file = os.path.join(DATA_DIR, 'tide_raw.csv')
    peaks_file = os.path.join(DATA_DIR, 'tide_peaks.csv')
    
    if not os.path.exists(tide_file):
        return
    
    tide_data = []
    
    # Read Raw Data
    with open(tide_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                tide_data.append({
                    't': row['datetime'],
                    'v': float(row['tide_height'])
                })
                
    # Read Peaks Data
    peaks_data = []
    if os.path.exists(peaks_file):
        with open(peaks_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['spot_name'] == spot_name:
                    peaks_data.append({
                        't': row['datetime'],
                        'v': float(row['tide_height']),
                        'type': row['type'] # 'H' or 'L'
                    })
    
    if tide_data:
        # Sort by time
        tide_data.sort(key=lambda x: x['t'])
        peaks_data.sort(key=lambda x: x['t'])
        
        output_data = {
            'dataset': tide_data,
            'peaks': peaks_data
        }
        
        output_file = os.path.join(OUTPUT_DIR, 'tide', f'{spot_name}.json')
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✓ Generated tide file for {spot_name} with {len(tide_data)} points and {len(peaks_data)} peaks")

if __name__ == '__main__':
    main()
