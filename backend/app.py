from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import csv
import datetime
import pytz
# import swell_service # Not strictly needed if reading from CSV

app = Flask(__name__, static_folder='../', static_url_path='')
CORS(app)

@app.route('/')
def root():
    return app.send_static_file('index.html')

@app.route('/api/forecast', methods=['GET'])
def get_forecast():
    spot_query = request.args.get('spot', '').lower()
    
    # Load aggregated daily data
    # Note: 'daily_forecast.csv' is creating by aggregate_daily.py
    data_file = os.path.join(os.path.dirname(__file__), '../data/daily_forecast.csv')
    if not os.path.exists(data_file):
        return jsonify({"error": "No cached data available. Please run fetch_data.py"}), 503
        
    try:
        rows = []
        with open(data_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        return jsonify({"error": f"Failed to read cache: {str(e)}"}), 500

    # Filter for spot
    spot_data = [row for row in rows if spot_query in row['spot_name'].lower()]
    
    # Fallback logic
    if not spot_data:
        if not spot_query:
             # Default to Devereaux
             spot_data = [row for row in rows if "devereaux" in row['spot_name'].lower()]
        
        if not spot_data:
             return jsonify({"error": "Spot not found in cache"}), 404

    spot_name = spot_data[0]['spot_name']

    # Filter past days and sort
    la_tz = pytz.timezone('America/Los_Angeles')
    now_la = datetime.datetime.now(la_tz)
    today_str = now_la.strftime('%Y-%m-%d')
    
    future_data = []
    
    for row in spot_data:
        if row['date'] >= today_str:
            future_data.append(row)
            
    # Sort by date (should be sorted by agg script, but double check)
    future_data.sort(key=lambda x: x['date'])
    
    # Take top 3 days
    final_rows = future_data[:3]
    
    forecast = []
    for row in final_rows:
        dt_obj = datetime.datetime.strptime(row['date'], '%Y-%m-%d').date()
        
        # Rating Logic
        try:
            height = float(row['swell_height'])
            period = float(row['swell_period'])
            rating_val = height * period
        except:
            rating_val = 0
            height = 0 # Added height = 0 here
        
        rating = "Poor"
        color = "#ef4444" # Red
        
        # Swell height check for "Insanely Big" (Purple)
        # Assuming > 2.0m is big for this context, or maybe high rating? 
        if height > 2.5:
             rating = "Epic"
             color = "#a855f7" # Purple
        elif rating_val > 18: 
            rating = "Excellent"
            color = "#15803d" # Dark Green
        elif rating_val > 12: 
            rating = "Good"
            color = "#22c55e" # Green
        elif rating_val > 6: 
            rating = "Fair"
            color = "#eab308" # Yellow

        # Check if this date is today (in LA time)
        today_la = now_la.date()
        day_label = "Today" if dt_obj == today_la else dt_obj.strftime('%A')
        
        # Re-format date object for display
        date_display = dt_obj.strftime('%b %d')

        forecast.append({
            'day': day_label,
            'date': date_display,
            'date_iso': row['date'],  # ISO format for API calls
            'swell': {
                'height': row['swell_height'],
                'unit': 'm',
                'direction': f"{row['swell_direction']}°", 
                'period': f"{row['swell_period']}s"
            },
            'wind': {
                'speed': row.get('avg_wind_speed', row.get('median_wind_speed', '0')), # Prefer avg
                'unit': 'kts',
                'direction': row.get('wind_direction', '0')
            },
            'tide': {
                'time': 'noon',
                'height': row.get('tide_height', '1.0m')
            },
            'rating': rating,
            'color': color
        })

    # Load region map
    locations_file = os.path.join(os.path.dirname(__file__), '../data/locations.csv')
    region = "Unknown"
    if os.path.exists(locations_file):
         try:
            with open(locations_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['site_name'] == spot_name:
                        region = row['region']
                        break
         except:
             pass

    return jsonify({
        "spotName": spot_name,
        "region": region,
        "forecast": forecast
    })

@app.route('/api/spots', methods=['GET'])
def get_spots():
    data_file = os.path.join(os.path.dirname(__file__), '../data/daily_forecast.csv')
    locations_file = os.path.join(os.path.dirname(__file__), '../data/locations.csv')
    
    if not os.path.exists(data_file):
        return jsonify([]), 200
    
    # helper: load region map
    region_map = {}
    if os.path.exists(locations_file):
        try:
            with open(locations_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    region_map[row['site_name']] = row['region']
        except:
             pass

    try:
        spots_list = []
        seen = set()
        with open(data_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['spot_name']
                if name not in seen:
                    spots_list.append({
                        "name": name,
                        "region": region_map.get(name, "Unknown Region")
                    })
                    seen.add(name)
        return jsonify(spots_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/hourly', methods=['GET'])
def get_hourly():
    spot = request.args.get('spot', '').strip()
    date_str = request.args.get('date', '').strip()  # Expected format: YYYY-MM-DD
    
    if not spot or not date_str:
        return jsonify({"error": "Missing spot or date parameter"}), 400
    
    swell_file = os.path.join(os.path.dirname(__file__), '../data/swell_raw.csv')
    wind_file = os.path.join(os.path.dirname(__file__), '../data/wind_raw.csv')
    
    hourly_data = []
    
    try:
        # Read swell data
        swell_dict = {}
        if os.path.exists(swell_file):
            with open(swell_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['spot_name'].lower() == spot.lower() and row['datetime'].startswith(date_str):
                        dt = row['datetime']
                        swell_dict[dt] = {
                            'height': float(row['swell_height']),
                            'period': float(row['swell_period']),
                            'direction': float(row['swell_direction'])
                        }
        
        # Read wind data and combine
        if os.path.exists(wind_file):
            with open(wind_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['spot_name'].lower() == spot.lower() and row['datetime'].startswith(date_str):
                        dt = row['datetime']
                        wind_data = {
                            'speed': float(row['wind_speed']),
                            'direction': float(row['wind_direction'])
                        }
                        
                        # Combine with swell if exists
                        if dt in swell_dict:
                            hourly_data.append({
                                'datetime': dt,
                                'swell': swell_dict[dt],
                                'wind': wind_data
                            })
        
        # Sort by datetime
        hourly_data.sort(key=lambda x: x['datetime'])
        
        return jsonify(hourly_data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=8080)
