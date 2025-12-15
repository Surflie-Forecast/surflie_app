#!/usr/bin/env python3
"""
Generate static JSON files for GitHub Pages deployment.
This script reads the CSV data and creates JSON files that match the API structure.
"""

import csv
import json
import os
from collections import defaultdict
from rating_utils import get_daily_rating_from_hourly

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, '../data')
API_DIR = os.path.join(BASE_DIR, '../api')

def generate_spots_json():
    """Generate api/spots.json from locations.csv"""
    locations_file = os.path.join(DATA_DIR, 'locations.csv')
    output_file = os.path.join(API_DIR, 'spots.json')
    
    spots = []
    with open(locations_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            spots.append({
                'name': row['site_name'],
                'region': row['region'],
                'latitude': float(row['latitude']) if row.get('latitude') else None,
                'longitude': float(row['longitude']) if row.get('longitude') else None
            })
    
    with open(output_file, 'w') as f:
        json.dump(spots, f, indent=2)
    
    print(f"✓ Generated {output_file}")
    return spots

def generate_forecast_json(spot_name, region):
    """Generate api/forecast/{spot_name}.json from daily_forecast.csv"""
    daily_file = os.path.join(DATA_DIR, 'daily_forecast.csv')
    swell_file = os.path.join(DATA_DIR, 'swell_raw.csv')
    forecast_dir = os.path.join(API_DIR, 'forecast')
    os.makedirs(forecast_dir, exist_ok=True)
    
    output_file = os.path.join(forecast_dir, f'{spot_name}.json')
    
    forecast_data = []
    
    with open(daily_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                # Calculate rating and color based on hourly data (7am-7pm)
                wind_file = os.path.join(DATA_DIR, 'wind_raw.csv')
                rating, color = get_daily_rating_from_hourly(spot_name, row['date'], swell_file, wind_file)
                
                # Parse date for formatting
                date_parts = row['date'].split('-')
                if len(date_parts) == 3:
                    year, month, day = date_parts
                    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                    date_formatted = f"{months[int(month)-1]} {int(day)}"
                else:
                    date_formatted = row['date']
                
                forecast_data.append({
                    'day': 'Day',  # Will be calculated by frontend
                    'date': date_formatted,
                    'date_iso': row['date'],
                    'swell': {
                        'height': row['swell_height'],
                        'period': row['swell_period'] + 's',
                        'direction': row['swell_direction'] + '°',
                        'unit': 'm'
                    },
                    'wind': {
                        'speed': row['median_wind_speed'],
                        'direction': row['wind_direction'],
                        'unit': 'kts'
                    },
                    'tide': {
                        'max': {
                            'height': row['tide_max'],
                            'time': row['tide_max_time']
                        },
                        'min': {
                            'height': row['tide_min'],
                            'time': row['tide_min_time']
                        }
                    },
                    'rating': rating,
                    'color': color
                })
    
    result = {
        'spotName': spot_name,
        'region': region,
        'forecast': forecast_data
    }
    
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"✓ Generated {output_file}")

import shutil  # Added for rmtree

def clean_output():
    """Clean specific output directories/files before regeneration."""
    print("Cleaning old static files...")
    
    # Clean forecast directory
    forecast_dir = os.path.join(API_DIR, 'forecast')
    if os.path.exists(forecast_dir):
        try:
            shutil.rmtree(forecast_dir)
            print("✓ Removed old api/forecast directory")
        except Exception as e:
            print(f"Warning: Could not remove {forecast_dir}: {e}")
            
    # Clean spots.json
    spots_file = os.path.join(API_DIR, 'spots.json')
    if os.path.exists(spots_file):
        try:
            os.remove(spots_file)
            print("✓ Removed old api/spots.json")
        except Exception as e:
            print(f"Warning: Could not remove {spots_file}: {e}")

def main():
    print("Generating static JSON files for GitHub Pages...")
    print()
    
    # Clean old outputs
    clean_output()
    
    # Generate spots.json
    spots = generate_spots_json()
    
    # Generate forecast JSON for each spot
    for spot in spots:
        generate_forecast_json(spot['name'], spot['region'])
    
    print()
    print("✅ All static files generated successfully!")
    print("You can now deploy to GitHub Pages.")

if __name__ == '__main__':
    main()
