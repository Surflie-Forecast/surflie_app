#!/usr/bin/env python3
"""
Generate static JSON files for GitHub Pages deployment.
This script reads the CSV data and creates JSON files that match the API structure.
"""

import csv
import json
import os
from collections import defaultdict

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
                'region': row['region']
            })
    
    with open(output_file, 'w') as f:
        json.dump(spots, f, indent=2)
    
    print(f"✓ Generated {output_file}")
    return spots

def get_rating_and_color(swell_height, swell_period):
    """Determine rating and color based on swell height and period (matches app.py logic)"""
    try:
        height = float(swell_height)
        period = float(swell_period)
        rating_val = height * period
    except:
        rating_val = 0
        height = 0
    
    rating = "Poor"
    color = "#ef4444"  # Red
    
    # Swell height check for "Epic" (Purple)
    if height > 2.5 and period > 12:
        rating = "Epic"
        color = "#a855f7"  # Purple
    elif rating_val > 13:
        rating = "Excellent"
        color = "#15803d"  # Dark Green
    elif rating_val > 9:
        rating = "Good"
        color = "#22c55e"  # Green
    elif rating_val > 4:
        rating = "Fair"
        color = "#eab308"  # Yellow
    
    return rating, color

def generate_forecast_json(spot_name, region):
    """Generate api/forecast/{spot_name}.json from daily_forecast.csv"""
    daily_file = os.path.join(DATA_DIR, 'daily_forecast.csv')
    forecast_dir = os.path.join(API_DIR, 'forecast')
    os.makedirs(forecast_dir, exist_ok=True)
    
    output_file = os.path.join(forecast_dir, f'{spot_name}.json')
    
    forecast_data = []
    
    with open(daily_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['spot_name'] == spot_name:
                rating, color = get_rating_and_color(row['swell_height'], row['swell_period'])
                
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

def main():
    print("Generating static JSON files for GitHub Pages...")
    print()
    
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
