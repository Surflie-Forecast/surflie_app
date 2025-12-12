#!/bin/bash

# Surf Forecast - Complete Pipeline Runner
# This script runs the entire data pipeline locally

set -e  # Exit on error

echo "🌊 Starting Surf Forecast Pipeline..."
echo "======================================"

# Load environment variables if setup_env.sh exists
if [ -f "setup_env.sh" ]; then
    echo "🔐 Loading credentials from setup_env.sh..."
    source setup_env.sh
else
    echo "⚠️  setup_env.sh not found - using system environment variables"
fi

# Activate virtual environment
source .venv/bin/activate

# Step 1: Fetch latest data from GEE
echo ""
echo "📡 Step 1/3: Fetching latest swell and tide data..."
python backend/fetch_data.py

# Step 2: Aggregate daily forecasts
echo ""
echo "📊 Step 2/3: Aggregating daily forecasts..."
python backend/aggregate_daily.py

# Step 3: Build static JSON files
echo ""
echo "🔨 Step 3/3: Building static JSON files..."
python backend/build.py

# Step 4: Generate static files for GitHub Pages
echo ""
echo "📄 Generating static files for deployment..."
python backend/generate_static_files.py

echo ""
echo "======================================"
echo "✅ Pipeline completed successfully!"
echo ""
echo "To deploy to GitHub Pages, run:"
echo "  git add -A"
echo "  git commit -m 'Update forecast data'"
echo "  git push origin rating_system"
