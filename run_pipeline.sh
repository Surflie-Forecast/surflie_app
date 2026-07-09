#!/bin/bash
# Surf Forecast — run the data pipeline locally and serve the site.
# No credentials needed: all sources (Open-Meteo, NOAA) are public APIs.

set -e

source .venv/bin/activate

echo "🌊 Running data pipeline..."
python backend/pipeline.py

echo ""
echo "✅ Done. Serve the site locally with:"
echo "   python -m http.server 8000   # then open http://localhost:8000"
