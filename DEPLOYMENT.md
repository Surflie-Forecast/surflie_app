# GitHub Pages Deployment Workflow

## Overview
This app uses **static JSON files** for GitHub Pages deployment since GitHub Pages cannot run the Python Flask backend.

## Deployment Steps

### 1. Update Data
Run the data pipeline to fetch fresh data:
```bash
python backend/fetch_data.py
```

This will:
- Fetch swell data from Google Earth Engine
- Fetch wind data from Google Earth Engine  
- Fetch tide data from NOAA (high-res + peaks)
- Aggregate daily forecasts into `data/daily_forecast.csv`

### 2. Generate Static Files
Convert the CSV data to JSON files for GitHub Pages:
```bash
python backend/generate_static_files.py
```

This creates:
- `api/spots.json` - List of surf spots
- `api/forecast/{spot_name}.json` - Forecast data for each spot

### 3. Commit and Push
```bash
git add .
git commit -m "Update forecast data"
git push origin main
```

GitHub Pages will automatically deploy the updated files.

## File Structure

```
surf_forecast/
├── api/                    # Static JSON files (generated)
│   ├── spots.json
│   └── forecast/
│       ├── C st..json
│       └── Devereaux Beach.json
├── backend/                # Python scripts (not deployed)
│   ├── fetch_data.py       # Fetch fresh data
│   ├── generate_static_files.py  # Generate JSON
│   ├── aggregate_daily.py
│   ├── swell_service.py
│   ├── wind_service.py
│   └── tide_service.py
├── data/                   # CSV data files (not deployed)
│   ├── daily_forecast.csv
│   ├── swell_raw.csv
│   ├── wind_raw.csv
│   ├── tide_raw.csv
│   └── tide_peaks.csv
├── js/                     # Frontend JavaScript
│   ├── app.js
│   └── hourly.js
├── css/                    # Styles
│   └── styles.css
└── index.html              # Main page
```

## Automation (Optional)

You can automate this with GitHub Actions. Create `.github/workflows/update-data.yml`:

```yaml
name: Update Forecast Data

on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
  workflow_dispatch:  # Manual trigger

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      
      - name: Fetch data
        env:
          GOOGLE_APPLICATION_CREDENTIALS: ${{ secrets.GEE_CREDENTIALS }}
        run: |
          python backend/fetch_data.py
      
      - name: Generate static files
        run: |
          python backend/generate_static_files.py
      
      - name: Commit and push
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add api/
          git commit -m "Auto-update forecast data" || exit 0
          git push
```

## Notes

- The frontend now uses **static JSON files** (`api/forecast/{name}.json`)
- For local development with live backend, you can temporarily switch back to dynamic API in `js/app.js`
- Images should be in the `images/` folder and committed to the repo
- Make sure `images/{region}.jpg` exists for each region in your locations
