# Surf Forecast

Automated surf forecast site. A scheduled pipeline fetches marine/weather
data from public APIs, builds static JSON, and deploys everything to GitHub
Pages — no server, no database, no credentials.

## Architecture

```
INGESTION  (GitHub Actions, every 6 h)
  backend/adapters/          one module per data source
    openmeteo_marine.py      waves — NCEP GFS Wave (default) + 4 comparison
                             models via Open-Meteo, all tagged by source
    cdip_mop.py              waves — CDIP MOP 1 km California nearshore grid
                             (overlay-only; THREDDS OPeNDAP ascii, no netCDF deps)
    openmeteo_weather.py     wind  — NOAA GFS via Open-Meteo
    noaa_tides.py            tides — NOAA CO-OPS (nearest station)
        │   all emit the same normalized rows (spot, ts_utc, variable, value, source)
        ▼
NORMALIZED STORE  (backend/store.py — in-memory, dumped to data/store.csv)
        │
BUILDERS  (backend/builders.py — aggregation, ratings, local-time conversion)
        ▼
STATIC JSON  (api/ — the only contract the frontend knows)
        ▼
GitHub Pages  (deployed as a build artifact; data is never committed to git)
```

Key properties:

- **Adding a data source = one adapter file.** Implement
  `fetch(spot, start_utc, end_utc) -> rows` and register it in
  `backend/adapters/__init__.py`. Store, builders, and frontend are untouched.
- **A source outage degrades freshness, not availability.** If a critical
  source fails, the pipeline exits non-zero, the deploy is skipped, and the
  previous snapshot stays live.
- **Everything in the store is UTC**; builders convert to each spot's
  timezone (from `data/locations.csv`).
- **Spots are identified by slug** in file paths and URLs; display names are
  free-form.

## Static API contract

| File | Content |
|---|---|
| `api/spots.json` | All spots: name, slug, region, lat/lon |
| `api/forecast/{slug}.json` | Daily summaries + ratings (10 days) |
| `api/hourly/{slug}/{date}.json` | 3-hourly swell + wind entries |
| `api/tide/{slug}.json` | 6-min tide curve + high/low peaks |
| `api/models/{slug}.json` | Combined Hs per wave model (3-hourly) for the comparison overlay |

The displayed wave height is **combined significant wave height (Hs)** from
the default model (NCEP GFS Wave — full variable set, 16-day horizon; see
`data_exploration/model_exploration_devereaux.ipynb`). The swell partition
(period/direction) qualifies it and drives the rating. The "Models" button
on the chart overlays every fetched model's Hs.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt   # just `requests`

./run_pipeline.sh                # fetch data + build api/ (~6 s)
python -m http.server 8000       # open http://localhost:8000
```

## Deployment

`.github/workflows/build-deploy.yml` runs the pipeline and deploys the site
as a GitHub Pages artifact:

- every 6 hours (data refresh),
- on every push to `main` (code changes),
- or manually from the Actions tab.

**One-time setup:** repo *Settings → Pages → Source = "GitHub Actions"*.
No repository secrets are required.

## Adding a surf spot

Add a row to `data/locations.csv` (`site_name, slug, region, latitude,
longitude, timezone`). The next pipeline run picks it up.

## Rating logic

`backend/rating_config.py` is the single source of truth. After changing it,
regenerate the frontend copy:

```bash
python backend/sync_rating_logic.py   # rewrites js/rating.js
```

## Tech stack

- **Data**: Open-Meteo (marine: GFS Wave default + MFWAM/ECMWF/GWAM
  comparison; weather: GFS), NOAA CO-OPS (tides)
- **Pipeline**: Python 3.12+, stdlib + `requests`
- **Hosting**: GitHub Pages (artifact deploys via GitHub Actions)
- **Frontend**: vanilla JS/HTML/CSS, Leaflet
