"""
Rating utility functions for surf forecast.
Uses centralized configuration from rating_config.py
"""
import csv
from collections import Counter
from rating_config import RatingConfig


def get_rating_and_color(swell_height, swell_period):
    """
    Calculate rating and color based on swell height and period.
    Uses configuration from rating_config.py for consistency.
    
    Args:
        swell_height: Wave height in meters
        swell_period: Swell period in seconds
        
    Returns:
        tuple: (rating_str, color_hex)
    """
    return RatingConfig.get_rating(swell_height, swell_period)


def get_daily_rating_from_hourly(spot_name, date_str, swell_file_path, wind_file_path=None):
    """
    Calculate daily rating based on most common hourly rating between 7am-7pm.
    
    This provides a more representative rating by analyzing surf conditions during
    prime surfing hours rather than using daily aggregated values.
    
    Args:
        spot_name: Name of the surf spot
        date_str: Date in YYYY-MM-DD format
        swell_file_path: Path to swell_raw.csv file
        wind_file_path: Path to wind_raw.csv file (optional)
        
    Returns:
        tuple: (rating_str, color_hex) - Most common rating during peak hours
        Falls back to ('Poor', '#ef4444') if no data available
    """
    try:
        # Read wind data if provided
        wind_data = {}
        if wind_file_path:
            import os
            if os.path.exists(wind_file_path):
                with open(wind_file_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row['spot_name'] == spot_name and row['datetime'].startswith(date_str):
                            wind_data[row['datetime']] = float(row['wind_speed'])
        
        # Read hourly swell data
        hourly_ratings = []
        
        with open(swell_file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['spot_name'] == spot_name:
                    datetime_str = row['datetime']
                    
                    # Check if this row is for the target date
                    if not datetime_str.startswith(date_str):
                        continue
                    
                    # Extract hour from datetime (format: "YYYY-MM-DD HH:MM:SS")
                    try:
                        time_part = datetime_str.split(' ')[1]  # "HH:MM:SS"
                        hour = int(time_part.split(':')[0])  # Extract hour
                        
                        # Filter for 7am-7pm (07:00 - 19:00 inclusive)
                        if 7 <= hour <= 19:
                            height = float(row['swell_height'])
                            period = float(row['swell_period'])
                            wind_speed = wind_data.get(datetime_str, 0)
                            
                            rating, color = RatingConfig.get_rating(height, period, wind_speed)
                            hourly_ratings.append((rating, color))
                    except (ValueError, IndexError):
                        continue
        
        # Find the most common color
        if hourly_ratings:
            # Count colors (we use color as the key since that's what we display)
            color_counts = Counter([color for _, color in hourly_ratings])
            most_common_color = color_counts.most_common(1)[0][0]
            
            # Find the rating that corresponds to this color
            for rating, color in hourly_ratings:
                if color == most_common_color:
                    return rating, color
        
        # Fallback if no data found
        return 'Poor', '#ef4444'
        
    except Exception as e:
        # Fallback on any error
        print(f"Warning: Error calculating hourly rating for {spot_name} on {date_str}: {e}")
        return 'Poor', '#ef4444'
