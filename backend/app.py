from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import csv
import datetime
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
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
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
        dt_obj = datetime.datetime.strptime(row['date'], '%Y-%m-%d')
        
        # Rating Logic
        try:
            height = float(row['swell_height'])
            period = float(row['swell_period'])
            rating_val = height * period
        except:
            rating_val = 0
        
        rating = "Poor"
        color = "#ef4444" # Red
        
        # Swell height check for "Insanely Big" (Purple)
        # Assuming > 2.0m is big for this context, or maybe high rating? 
        # User said "insanely big the swell", implying height.
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

        forecast.append({
            'day': dt_obj.strftime('%A'),
            'date': dt_obj.strftime('%b %d'),
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

    return jsonify({
        "spotName": spot_name,
        "forecast": forecast
    })

if __name__ == '__main__':
    app.run(debug=True, port=8080)
