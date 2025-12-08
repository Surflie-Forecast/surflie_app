# Surf Forecast - Static Site Generation

This app uses pre-generated JSON files to work on GitHub Pages.

## How it Works

1. **CSV Data** → Stored in `/data/` (swell_raw.csv, wind_raw.csv, daily_forecast.csv)
2. **Build Script** (`backend/build.py`) → Converts CSV to JSON 
3. **Static JSON** → Stored in `/api/` directory
   - `/api/spots.json` - List of all spots
   - `/api/forecast/{spot}.json` - Daily forecast for each spot
   - `/api/hourly/{spot}/{date}.json` - Hourly data organized by spot and date
4. **Frontend** → Fetches from JSON files (no backend needed!)

## Auto-Updates via GitHub Actions

The workflow (`.github/workflows/update-forecast.yml`) runs every 6 hours:
1. Fetches new surf data from Google Earth Engine
2. Updates CSV files
3. Runs `build.py` to regenerate JSON
4. Commits changes to repo
5. GitHub Pages auto-deploys

## Local Development

```bash
# Fetch data and generate JSON
python3 backend/scheduler.py  # Fetches CSV data
python3 backend/build.py      # Converts CSV → JSON

# Serve locally
python3 -m http.server 8000
# Visit http://localhost:8000
```

## GitHub Pages Setup

1. Push code to GitHub
2. Settings → Pages → Source: `main` branch, `/` (root)
3. Add GEE credentials as secret: `GEE_SERVICE_ACCOUNT_KEY`
4. Site will update automatically every 6 hours!
